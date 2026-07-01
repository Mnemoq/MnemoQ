# Copyright (C) 2026 Mnemoq
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Conversation capture module for the memory engine.

Takes raw conversation text, extracts learnable moments as summary dicts
in the format evaluate_core() consumes, and auto-logs memories.

Three-tier extraction: online LLM, offline LLM, heuristic fallback.
heuristic_extract() is the floor — it never returns None.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

from mnemoq.engine.auto_learn import _derive_domain
from mnemoq.engine.evaluate import evaluate_core
from mnemoq.engine.handlers import log_core
from mnemoq.engine.metrics import log_event
from mnemoq.engine.reranker import _call_llm, _probe_llm_endpoint

# ---------------------------------------------------------------------------
# Shared prompt builder + response parser (used by both LLM tiers)
# ---------------------------------------------------------------------------

_EXTRACTION_SYSTEM = (
    "Extract learnable moments from this conversation as a JSON array. "
    "One entry per meaningful exchange. Fields: "
    'step(int), prompt_type("human"|"agent"), '
    'outcome("correction"|"preference"|"bug_fixed"|"decision"|"workaround"|"none"), '
    "text(terse gist), components(string[]), files_touched(string[]), "
    "corrected_action?(string), rejected_action?(string), error_text?(string). "
    "Be terse."
)

_MAX_CONVERSATION_CHARS = 8000


def _build_extraction_prompt(conversation_text: str) -> str:
    """Build the LLM extraction prompt. Truncates to ~2000 tokens."""
    truncated = conversation_text[:_MAX_CONVERSATION_CHARS]
    if len(conversation_text) > _MAX_CONVERSATION_CHARS:
        truncated += "\n[...truncated...]"
    return f"{_EXTRACTION_SYSTEM}\n\nUser: {truncated}"


def _parse_llm_response(response_text: str) -> list[dict] | None:
    """Extract JSON array from LLM response. Handles markdown code fences."""
    if not response_text:
        return None
    text = response_text.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```) and last line (```)
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to find a JSON array in the text
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                return None
        else:
            return None
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "entries" in data:
        entries = data["entries"]
        if isinstance(entries, list):
            return entries
    return None


# ---------------------------------------------------------------------------
# Heuristic extractor — pure functions, no I/O
# ---------------------------------------------------------------------------

_SPEAKER_RE = re.compile(
    r'^(?:\*{0,2}|#{1,3}\s*|\[)(Human|User|AI|Agent|Assistant|Cascade|Claude)(?:\]|\*{0,2}:)\s*(.*)',
    re.IGNORECASE,
)
_FILE_PATH_RE = re.compile(r'[\w/\\]+\.\w{1,5}')
_BACKTICK_RE = re.compile(r'`([^`]+)`')
_PASCAL_CAMEL_RE = re.compile(r'\b[A-Z][a-z]+[A-Z]\w*|\b[A-Z]{2,}[a-z]\w*')
_CORRECTION_SELF_RE = re.compile(r'\b(?:no,|stop|shouldn\'t|not that)(?!\w)', re.IGNORECASE)
_CORRECTION_WRONG_RE = re.compile(r'\bwrong\b', re.IGNORECASE)
_BUG_FIX_RE = re.compile(r'\b(fixed|error|bug|crash|exception|traceback|failed)\b', re.IGNORECASE)
_DECISION_RE = re.compile(r'\b(let\'s|we should|going forward|decided|let us|shall we)\b', re.IGNORECASE)
_PREFERENCE_SELF_RE = re.compile(r'\b(remember|don\'t forget|note this)\b', re.IGNORECASE)
_PREFERENCE_NEGATE_RE = re.compile(r'\b(always|never)\b', re.IGNORECASE)
_WORKAROUND_RE = re.compile(
    r'\b(?:for\s+now|workaround|temporarily|hack\b|skip\s+(?!to\b)(?:the\s+)?\w+|pin\s+(?:to|version))\b',
    re.IGNORECASE,
)

_NEGATION_WORDS = ("not", "don't", "dont", "no", "never", "isn't", "wasn't")
_OUTCOME_REGEXES = [
    ("correction", _CORRECTION_SELF_RE, False),
    ("correction", _CORRECTION_WRONG_RE, True),
    ("bug_fixed", _BUG_FIX_RE, True),
    ("decision", _DECISION_RE, True),
    ("preference", _PREFERENCE_SELF_RE, False),
    ("preference", _PREFERENCE_NEGATE_RE, True),
    ("workaround", _WORKAROUND_RE, True),
]
_SENTENCE_BOUNDARY_RE = re.compile(r'[.!?]\s+[A-Z]')

_OUTCOME_WEIGHTS = {
    "correction": 5, "bug_fixed": 4, "decision": 3,
    "preference": 3, "workaround": 2, "none": 1,
}


def _split_turns(text: str) -> list[tuple[str, str]]:
    """Split raw conversation into (speaker, text) pairs."""
    if not text or not text.strip():
        return []
    turns = []
    current_speaker = "agent"
    current_lines: list[str] = []

    for line in text.split("\n"):
        match = _SPEAKER_RE.match(line)
        if match:
            if current_lines:
                turns.append((current_speaker, "\n".join(current_lines).strip()))
            current_speaker = match.group(1).lower()
            current_lines = [match.group(2)]
        else:
            current_lines.append(line)

    if current_lines:
        turns.append((current_speaker, "\n".join(current_lines).strip()))

    return turns


_CAPITAL_STOPWORDS = {
    "The", "This", "That", "Then", "When", "Here", "There", "Where",
    "What", "Which", "With", "From", "Into", "Also", "Just", "Like",
    "Some", "Such", "More", "Most", "Only", "Very", "Well", "Even",
    "Still", "Over", "Under",
    "Human", "Agent", "Assistant", "Cascade", "Claude",
}

_SNAKE_STOPWORDS = {
    "sort_of", "out_of", "kind_of", "lot_of", "lots_of", "set_of",
    "pair_of", "list_of", "part_of", "rest_of", "type_of", "form_of",
    "line_of", "point_of", "sense_of", "bit_of", "piece_of",
    "number_of", "lack_of", "use_of", "state_of", "rate_of",
    "end_of", "side_of", "means_of", "matter_of", "case_of",
    "fact_of", "idea_of", "notion_of", "plenty_of", "short_of",
    "tired_of", "proud_of", "aware_of", "capable_of", "certain_of",
    "clear_of", "free_of", "guilty_of", "innocent_of", "sure_of",
    "worthy_of", "devoid_of", "inclusive_of", "exclusive_of",
    "irrespective_of", "regardless_of",
}


def _extract_components(text: str) -> list[str]:
    """Extract capitalized terms, backtick-quoted code, camelCase/PascalCase, UPPER_CASE, snake_case."""
    components: list[str] = []

    # Backtick-quoted code references
    for m in _BACKTICK_RE.finditer(text):
        val = m.group(1).strip()
        if val and len(val) < 80 and val not in components:
            components.append(val)

    # PascalCase / camelCase identifiers
    for m in _PASCAL_CAMEL_RE.finditer(text):
        val = m.group(0)
        if val not in components:
            components.append(val)

    # Standalone capitalized words anywhere
    for m in re.finditer(r'\b([A-Z][a-z]{2,})\b', text):
        val = m.group(1)
        if val not in components and val not in _CAPITAL_STOPWORDS:
            components.append(val)

    # UPPER_CASE constants
    for m in re.finditer(r'\b([A-Z][A-Z_]{2,})\b', text):
        val = m.group(1)
        if val not in components:
            components.append(val)

    # snake_case identifiers
    for m in re.finditer(r'\b([a-z]+_[a-z_]+)\b', text):
        val = m.group(1)
        if val not in components and val not in _SNAKE_STOPWORDS:
            components.append(val)

    return components if components else ["unknown"]


def _extract_files(text: str) -> list[str]:
    """Extract file paths from text."""
    files = []
    for m in _FILE_PATH_RE.finditer(text):
        val = m.group(0)
        if val in files or val.endswith("."):
            continue
        # Filter out e.g, i.e, version numbers
        if re.match(r'^[a-z]\.[a-z]$', val, re.IGNORECASE):
            continue
        if re.match(r'^v\d+\.\d+', val, re.IGNORECASE):
            continue
        # Filter domains like example.com
        if re.match(r'^\w+\.(com|org|net|io|dev)$', val, re.IGNORECASE):
            continue
        # Strip leading ~/ and ./
        val = re.sub(r'^[~./]+', '', val)
        if val and val not in files:
            files.append(val)
    return files if files else ["unknown"]


def _is_negated(text: str, match_start: int) -> bool:
    """Check if a negation word appears within 20 chars before match_start."""
    window = text[max(0, match_start - 20):match_start].lower()
    return any(neg in window for neg in _NEGATION_WORDS)


def _detect_outcomes(text: str) -> list[str]:
    """Detect all outcome types from text.

    Returns a list of outcomes (deduplicated, order preserved). Correction
    regex keywords are exempt from negation checking — they ARE the
    negation signals.
    """
    seen_outcomes: set[str] = set()
    results: list[str] = []
    for outcome, regex, check_negation in _OUTCOME_REGEXES:
        for m in regex.finditer(text):
            if check_negation and _is_negated(text, m.start()):
                continue
            if outcome not in seen_outcomes:
                seen_outcomes.add(outcome)
                results.append(outcome)
            break
    return results


_GIST_SIGNAL_RE = re.compile(
    r'[\w/\\]+\.\w{1,5}|error|exception|crash|traceback|no,|stop|wrong|shouldn\'t|not that',
    re.IGNORECASE,
)


def _extract_gist(text: str, max_chars: int = 200) -> str:
    """Extract a gist from text using sentence-boundary detection.

    Splits on punctuation followed by whitespace + capital letter,
    not on every period (which breaks on file extensions, version
    numbers, abbreviations like 'e.g.').

    Prefers sentences containing signal (file paths, error terms, correction
    signals). Truncates at word boundary if max_chars is reached.
    """
    text = text.strip()
    if not text:
        return ""

    # Split into sentences using sentence boundary regex
    sentences: list[str] = []
    last_end = 0
    for m in _SENTENCE_BOUNDARY_RE.finditer(text):
        sentences.append(text[last_end:m.start() + 1].strip())
        last_end = m.start() + 1
    if last_end < len(text):
        sentences.append(text[last_end:].strip())
    if not sentences:
        sentences = [text]

    # Prefer first sentence with signal; fall back to first sentence
    chosen = sentences[0]
    for s in sentences:
        if len(s) <= max_chars and _GIST_SIGNAL_RE.search(s):
            chosen = s
            break

    if len(chosen) <= max_chars:
        return chosen

    # Word-boundary truncation
    truncated = chosen[:max_chars]
    last_space = truncated.rfind(' ')
    if last_space > 0:
        return truncated[:last_space].strip()
    return truncated.strip()


def _extract_error_text(text: str) -> str:
    """Extract error context from text using three strategies.

    1. Colon syntax: 'error: ...'
    2. Exception class names: TypeError, ValueError, etc.
    3. Natural language: 'got a TypeError', 'encountered a crash'

    Captures up to 3 lines or 300 chars for tracebacks.
    """
    # Strategy 1: colon syntax
    m = re.search(r'(error|exception|crash|traceback):\s*(.+)', text, re.IGNORECASE)
    if m:
        # Capture up to 3 lines for tracebacks
        lines = text.split('\n')
        for i, line in enumerate(lines):
            if re.search(r'(error|exception|crash|traceback):', line, re.IGNORECASE):
                multi = '\n'.join(lines[i:i+3])
                return multi[:300].strip()

    # Strategy 2: exception class names
    m = re.search(r'\b([A-Z]\w*(?:Error|Exception))\b', text)
    if m:
        return m.group(1)

    # Strategy 3: natural language
    m = re.search(
        r'\b(?:got|hit|encountered)\s+(?:a\s+|an\s+)?(.+?(?:error|exception|crash|traceback))',
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()

    return ""


def _extract_corrected_action(text: str) -> tuple[str, str]:
    """Extract corrected_action and rejected_action from correction text."""
    corrected = ""
    rejected = ""

    # "use X instead", "do X instead", "try X instead", "switch to X"
    for pat in (
        r'(?<!don\'t )(?<!dont )use\s+(.+?)\s+instead',
        r'\bdo\s+(.+?)\s+instead',
        r'\btry\s+(.+?)\s+instead',
        r'\bswitch\s+to\s+(.+?)(?:\s+instead|$)',
    ):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            corrected = m.group(1).strip()
            break

    # "replace X with Y" → corrected=Y, rejected=X
    m = re.search(r'\breplace\s+(.+?)\s+with\s+(.+?)(?:[,;]|$)', text, re.IGNORECASE)
    if m:
        rejected = m.group(1).strip()
        corrected = m.group(2).strip()

    # "use X rather than Y" → corrected=X, rejected=Y
    m = re.search(r'\buse\s+(.+?)\s+rather\s+than\s+(.+?)(?:[,;]|$)', text, re.IGNORECASE)
    if m:
        corrected = m.group(1).strip()
        rejected = m.group(2).strip()

    # "X not Y" → corrected=X, rejected=Y (skip copular negations like "this is not a bug")
    m = re.search(r'\b(\w+(?:\s+\w+)?)\s+not\s+(\w+(?:\s+\w+)?)(?:[,;.]|$)', text, re.IGNORECASE)
    if m and not corrected:
        g1 = m.group(1).strip()
        if not g1.endswith(('is', 'are', 'was', 'were', 'be', 'am')):
            corrected = g1
            rejected = m.group(2).strip()

    # "don't use X", "stop using X", "avoid X", "never use X" → rejected=X
    for pat in (
        r"don'?t\s+(?:use|do)\s+(.+?)(?:\s+instead|$)",
        r'\bstop\s+using\s+(.+?)(?:[,;.]|$)',
        r'\bavoid\s+(.+?)(?:[,;.]|$)',
        r'\bnever\s+use\s+(.+?)(?:[,;.]|$)',
    ):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            rejected = m.group(1).strip()
            break

    return corrected, rejected


_FILLER_WORDS = {"ok", "thanks", "done", "sure", "yes", "no problem", "got it",
                 "cool", "great", "nice", "ack", "acknowledged", "will do"}


def _is_filler(text: str) -> bool:
    """Return True if text is a short filler response with no signal."""
    stripped = text.strip().lower()
    return len(stripped) < 15 and stripped in _FILLER_WORDS


def heuristic_extract(conversation_text: str, ctx: dict | None = None) -> list[dict]:
    """Extract summary dicts from raw conversation text using heuristics.

    Always returns at least one summary dict — this is the LOTS mandate.
    """
    if not conversation_text or not conversation_text.strip():
        return [{
            "step": 1,
            "prompt_type": "agent",
            "outcome": "none",
            "text": "empty conversation",
            "components": ["unknown"],
            "files_touched": ["unknown"],
        }]

    turns = _split_turns(conversation_text)
    if not turns:
        turns = [("agent", conversation_text.strip())]

    summaries: list[dict] = []
    seen: set[tuple] = set()
    step = 1
    prev_gist = ""

    for speaker, turn_text in turns:
        if not turn_text.strip():
            continue

        outcomes = _detect_outcomes(turn_text)
        if not outcomes:
            outcomes = ["none"]

        # 4.1: skip filler unless non-none outcome detected
        if _is_filler(turn_text) and outcomes == ["none"]:
            continue

        components = _extract_components(turn_text)
        files = _extract_files(turn_text)
        gist = _extract_gist(turn_text)

        for outcome in outcomes:
            dedup_key = (outcome, tuple(sorted(components)), tuple(sorted(files)), gist)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            summary: dict = {
                "step": step,
                "prompt_type": "human" if speaker in ("human", "user") else "agent",
                "outcome": outcome,
                "text": gist,
                "components": components,
                "files_touched": files,
            }

            # 2.3: cross-turn context for corrections
            if outcome == "correction" and prev_gist:
                summary["context_turn"] = prev_gist[:100]

            # 3.5: corrected_action for correction AND preference
            if outcome in ("correction", "preference"):
                corrected, rejected = _extract_corrected_action(turn_text)
                if corrected:
                    summary["corrected_action"] = corrected
                if rejected:
                    summary["rejected_action"] = rejected

            # 2.2: error text for ANY outcome (not just bug_fixed)
            err = _extract_error_text(turn_text)
            if err:
                summary["error_text"] = err

            summaries.append(summary)
            step += 1

        prev_gist = gist

    # Always return at least one summary
    if not summaries:
        summaries.append({
            "step": 1,
            "prompt_type": "agent",
            "outcome": "none",
            "text": conversation_text.strip()[:200],
            "components": _extract_components(conversation_text),
            "files_touched": _extract_files(conversation_text),
        })

    return summaries


# ---------------------------------------------------------------------------
# Offline LLM extractor — reuses existing _probe_llm_endpoint + _call_llm
# ---------------------------------------------------------------------------

def offline_llm_extract(conversation_text: str, ctx: dict | None = None) -> list[dict] | None:
    """Extract summaries using a local LLM (Ollama/LM Studio).

    Returns None on any failure (no endpoint, network error, parse error).
    """
    ctx = ctx or {}
    endpoint = _probe_llm_endpoint(ctx.get("capture_llm_endpoint"))
    if endpoint is None:
        return None

    prompt = _build_extraction_prompt(conversation_text)
    try:
        response_text = _call_llm(endpoint, ctx.get("capture_llm_model"), prompt)
    except Exception:
        return None

    return _parse_llm_response(response_text)


# ---------------------------------------------------------------------------
# Online LLM extractor — OpenAI-compatible API via urllib
# ---------------------------------------------------------------------------

def online_llm_extract(conversation_text: str, ctx: dict | None = None) -> list[dict] | None:
    """Extract summaries using an OpenAI-compatible API.

    Returns None on any failure (no key, network error, parse error).
    """
    ctx = ctx or {}
    endpoint = ctx.get("capture_online_endpoint")
    if not endpoint:
        return None

    model = ctx.get("capture_online_model")
    if not model:
        return None

    api_key = ctx.get("capture_online_api_key") or os.environ.get("CAPTURE_API_KEY")
    if not api_key:
        return None

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": _EXTRACTION_SYSTEM},
            {"role": "user", "content": conversation_text[:_MAX_CONVERSATION_CHARS]},
        ],
        "stream": False,
        "response_format": {"type": "json_object"},
    }).encode()

    req = urllib.request.Request(
        endpoint.rstrip("/") + "/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            response_text = data["choices"][0]["message"]["content"]
    except Exception:
        return None

    return _parse_llm_response(response_text)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _score_summary(summary: dict) -> int:
    """Score a summary by outcome weight + signal bonuses."""
    score = _OUTCOME_WEIGHTS.get(summary.get("outcome", "none"), 1)
    components = summary.get("components", ["unknown"])
    files = summary.get("files_touched", ["unknown"])
    if components != ["unknown"]:
        score += 1
    if files != ["unknown"]:
        score += 1
    return score


def capture_core(conversation_text: str, paths, ctx: dict) -> dict:
    """Capture a conversation interaction as memory.

    Cascade: online → offline → heuristic. heuristic always succeeds.
    Feeds summaries to evaluate_core(), directly logs "none" outcomes.
    """
    if not ctx.get("capture_enabled", True):
        return {
            "exit_code": 0,
            "status": "ok",
            "disabled": True,
            "summaries_count": 0,
            "auto_logged": [],
            "suggestions": [],
            "extraction_tier": "none",
        }

    mode = ctx.get("capture_mode", "heuristic")
    summaries: list[dict] | None = None
    tier = "heuristic"

    if mode == "online":
        summaries = online_llm_extract(conversation_text, ctx)
        if summaries is not None:
            tier = "online"

    if summaries is None and mode in ("online", "offline"):
        summaries = offline_llm_extract(conversation_text, ctx)
        if summaries is not None:
            tier = "offline"

    if summaries is None:
        summaries = heuristic_extract(conversation_text, ctx)
        tier = "heuristic"

    # Rank by score, then cap summaries
    max_summaries = ctx.get("capture_max_summaries", 10)
    summaries.sort(key=_score_summary, reverse=True)
    summaries = summaries[:max_summaries]

    auto_logged: list[dict] = []
    suggestions: list[dict] = []
    always_log = ctx.get("capture_always_log", True)

    for summary in summaries:
        summary.setdefault("step", 1)
        eval_result = evaluate_core(summary, paths, ctx)

        auto_logged.extend(eval_result.get("auto_logged", []))
        suggestions.extend(eval_result.get("suggestions", []))

        # For "none" outcome where no detector fired, log directly
        # (only if there's real signal — not just smalltalk/filler)
        requires_signal = ctx.get("capture_none_log_requires_signal", True)
        has_real_signal = (summary.get("components", ["unknown"]) != ["unknown"]
                           or summary.get("files_touched", ["unknown"]) != ["unknown"])
        if always_log and summary.get("outcome") == "none" and eval_result.get("signals_detected", 0) == 0:
            if requires_signal and not has_real_signal:
                continue
            files = summary.get("files_touched", ["unknown"])
            entry = {
                "step": summary.get("step", 1),
                "source_agent": "system",
                "type": "architectural_pattern",
                "domain": _derive_domain(files[0] if files else "unknown"),
                "components": summary.get("components", ["unknown"]),
                "files_touched": files,
                "trigger": f"When interacting about {', '.join(summary.get('components', ['unknown']))}",
                "action": f"ALWAYS consider this context: {summary.get('text', 'conversation interaction')}",
                "reason": "Captured from conversation interaction",
                "importance": 3,
                "severity": "minor",
                "resolved": False,
            }
            log_result = log_core(json.dumps(entry), paths, ctx)
            auto_logged.append({
                "confidence": 1.0,
                "status": log_result.get("status", "added"),
                "type": entry["type"],
                "trigger": entry["trigger"],
                "action": entry["action"],
                "components": entry["components"],
                "files_touched": entry["files_touched"],
                "domain": entry["domain"],
            })

    # Escalation / tier-degradation observability.
    #
    # The cascade is LLM-primary -> heuristic-fallback (online -> offline ->
    # heuristic), so "degraded" means the configured `mode` could not be
    # satisfied and extraction fell to a lower tier. For mode="heuristic"
    # (the default) there is nothing to degrade to, so degraded is always False.
    # Per-domain rollups (see metrics._capture_stats) attribute the run-level
    # tier to every domain touched by this capture.
    _TIER_RANK = {"online": 3, "offline": 2, "heuristic": 1}
    degraded = _TIER_RANK.get(tier, 1) < _TIER_RANK.get(mode, 1)
    domains = sorted({
        _derive_domain((s.get("files_touched") or ["unknown"])[0])
        for s in summaries
    }) or ["unknown"]
    log_event(paths, "capture",
              mode=mode,
              tier=tier,
              degraded=degraded,
              summaries_count=len(summaries),
              auto_logged=len(auto_logged),
              domains=domains)

    return {
        "exit_code": 0,
        "status": "ok",
        "summaries_count": len(summaries),
        "auto_logged": auto_logged,
        "suggestions": suggestions,
        "extraction_tier": tier,
        "disabled": False,
    }


def parse_transcript(transcript_path: str) -> str:
    """Parse a Windsurf transcript JSONL file into conversation text.

    Extracts the last user_input + all subsequent entries.
    Returns text in 'Human: ... / Agent: ...' format for heuristic_extract.
    Resilient to unknown entry types and malformed JSON lines.
    """
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return ""

    entries: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    last_user_idx = -1
    for i in range(len(entries) - 1, -1, -1):
        if entries[i].get("type") == "user_input":
            last_user_idx = i
            break

    if last_user_idx == -1:
        return ""

    turns: list[str] = []
    for entry in entries[last_user_idx:]:
        etype = entry.get("type")
        if etype == "user_input":
            text = entry.get("user_input", {}).get("user_response", "")
            if text:
                turns.append(f"Human: {text}")
        elif etype == "planner_response":
            text = entry.get("planner_response", {}).get("response", "")
            if text:
                turns.append(f"Agent: {text}")
        elif etype == "code_action":
            path = entry.get("code_action", {}).get("path", "unknown")
            turns.append(f"Agent: [edited {path}]")

    return "\n".join(turns)
