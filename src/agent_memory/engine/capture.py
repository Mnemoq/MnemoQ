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

from agent_memory.engine.auto_learn import _derive_domain
from agent_memory.engine.evaluate import evaluate_core
from agent_memory.engine.handlers import log_core
from agent_memory.engine.reranker import _call_llm, _probe_llm_endpoint

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

_SPEAKER_RE = re.compile(r'^(Human|User|AI|Agent|Assistant|Cascade|Claude):\s*(.*)', re.IGNORECASE)
_FILE_PATH_RE = re.compile(r'[\w/\\]+\.\w{1,5}')
_BACKTICK_RE = re.compile(r'`([^`]+)`')
_PASCAL_CAMEL_RE = re.compile(r'\b[A-Z][a-z]+[A-Z]\w*|\b[A-Z]{2,}[a-z]\w*')
_CORRECTION_RE = re.compile(r'\b(no|stop|don\'t|wrong|actually|shouldn\'t|not that)\b', re.IGNORECASE)
_CORRECTION_SELF_RE = re.compile(r'\b(?:no,|stop|don\'t|actually|shouldn\'t|not that)\b', re.IGNORECASE)
_CORRECTION_NEGATE_RE = re.compile(r'\b(wrong)\b', re.IGNORECASE)
_BUG_FIX_RE = re.compile(r'\b(fixed|error|bug|crash|exception|traceback|failed)\b', re.IGNORECASE)
_DECISION_RE = re.compile(r'\b(let\'s|we should|going forward|decided|let us|shall we)\b', re.IGNORECASE)
_PREFERENCE_RE = re.compile(r'\b(always|never|remember|don\'t forget|note this)\b', re.IGNORECASE)
_PREFERENCE_SELF_RE = re.compile(r'\b(remember|don\'t forget|note this)\b', re.IGNORECASE)
_PREFERENCE_NEGATE_RE = re.compile(r'\b(always|never)\b', re.IGNORECASE)
_WORKAROUND_RE = re.compile(r'\b(for now|workaround|temporarily|pin|hack|skip)\b', re.IGNORECASE)
_USE_INSTEAD_RE = re.compile(r'(?<!don\'t )(?<!dont )use\s+(.+?)\s+instead', re.IGNORECASE)

_NEGATION_WORDS = ("not", "don't", "dont", "no", "never", "isn't", "wasn't")
_OUTCOME_REGEXES = [
    ("correction", _CORRECTION_SELF_RE, False),
    ("correction", _CORRECTION_NEGATE_RE, True),
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
    current_speaker = "human"
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


def _extract_components(text: str) -> list[str]:
    """Extract capitalized terms, backtick-quoted code, camelCase/PascalCase."""
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

    # Capitalized words (not at start of sentence)
    for m in re.finditer(r'(?<=[.!?]\s)\s*([A-Z][a-z]+)', text):
        val = m.group(1)
        if val not in components and len(val) > 2:
            components.append(val)

    return components if components else ["unknown"]


def _extract_files(text: str) -> list[str]:
    """Extract file paths from text."""
    files = []
    for m in _FILE_PATH_RE.finditer(text):
        val = m.group(0)
        if val not in files and not val.endswith("."):
            files.append(val)
    return files if files else ["unknown"]


def _is_negated(text: str, match_start: int) -> bool:
    """Check if a negation word appears within 10 chars before match_start."""
    window = text[max(0, match_start - 10):match_start].lower()
    return any(neg in window for neg in _NEGATION_WORDS)


def _detect_outcome(text: str) -> str:
    """Detect the outcome type from text.

    First non-negated match wins. Correction regex keywords (no, don't,
    shouldn't, not that) are exempt from negation checking — they ARE
    the negation signals.
    """
    for outcome, regex, check_negation in _OUTCOME_REGEXES:
        for m in regex.finditer(text):
            if check_negation and _is_negated(text, m.start()):
                continue
            return outcome
    return "none"


def _extract_gist(text: str, max_chars: int = 200) -> str:
    """Extract a gist from text using sentence-boundary detection.

    Splits on punctuation followed by whitespace + capital letter,
    not on every period (which breaks on file extensions, version
    numbers, abbreviations like 'e.g.').
    """
    text = text.strip()
    if not text:
        return ""
    boundary = _SENTENCE_BOUNDARY_RE.search(text)
    if boundary and boundary.start() <= max_chars:
        return text[:boundary.start() + 1].strip()
    return text[:max_chars].strip()


def _extract_corrected_action(text: str) -> tuple[str, str]:
    """Extract corrected_action and rejected_action from correction text."""
    corrected = ""
    rejected = ""
    match = _USE_INSTEAD_RE.search(text)
    if match:
        corrected = match.group(1).strip()
    # "don't use X" → rejected = X
    reject_match = re.search(r"don'?t\s+(?:use|do)\s+(.+?)(?:\s+instead|$)", text, re.IGNORECASE)
    if reject_match:
        rejected = reject_match.group(1).strip()
    return corrected, rejected


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

    for speaker, turn_text in turns:
        if not turn_text.strip():
            continue

        outcome = _detect_outcome(turn_text)
        components = _extract_components(turn_text)
        files = _extract_files(turn_text)

        gist = _extract_gist(turn_text)

        dedup_key = (outcome, tuple(sorted(components)), tuple(sorted(files)), gist[:50])
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

        if outcome == "correction":
            corrected, rejected = _extract_corrected_action(turn_text)
            if corrected:
                summary["corrected_action"] = corrected
            if rejected:
                summary["rejected_action"] = rejected
        elif outcome == "bug_fixed":
            # Try to extract error text
            err_match = re.search(r'(error|exception|crash|traceback):\s*(.+?)(?:\n|$)', turn_text, re.IGNORECASE)
            if err_match:
                summary["error_text"] = err_match.group(2).strip()

        summaries.append(summary)
        step += 1

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

    return {
        "exit_code": 0,
        "status": "ok",
        "summaries_count": len(summaries),
        "auto_logged": auto_logged,
        "suggestions": suggestions,
        "extraction_tier": tier,
        "disabled": False,
    }
