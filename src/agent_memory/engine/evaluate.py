"""Per-prompt evaluation module for the memory engine.

Pure detector functions receive a structured summary dict (no I/O).
evaluate_core() handles orchestration, validation, and auto-logging.
"""

from __future__ import annotations

import json
import time

from agent_memory.engine.auto_learn import _derive_domain
from agent_memory.engine.handlers import log_core
from agent_memory.engine.metrics import log_event
from agent_memory.engine.validation import validate_entry

# ---------------------------------------------------------------------------
# Candidate builder
# ---------------------------------------------------------------------------

def _build_candidate(summary, ctx, **overrides):
    """Build a schema-valid candidate entry from summary + detector overrides.

    Returns None if components or files_touched are empty — caller treats as no signal.
    """
    files = summary.get("files_touched", [])
    if not files:
        return None
    components = summary.get("components", [])
    if not components:
        return None
    candidate = {
        "step": summary.get("step", 1),
        "source_agent": "system",
        "type": overrides.get("type", "architectural_pattern"),
        "domain": _derive_domain(files[0]),
        "components": components,
        "files_touched": files,
        "trigger": overrides["trigger"],
        "action": overrides["action"],
        "reason": overrides["reason"],
        "importance": overrides.get("importance", 5),
        "severity": overrides.get("severity", "minor"),
        "resolved": False,
    }
    if "debt_level" in overrides:
        candidate["debt_level"] = overrides["debt_level"]
    return candidate


# ---------------------------------------------------------------------------
# Pure detectors — (summary, ctx) -> (confidence, candidate) | None
# ---------------------------------------------------------------------------

_REMEMBER_KEYWORDS = ("remember", "don't forget", "always", "never", "note this")


def detect_human_correction(summary, ctx):
    """Detect human-in-the-loop corrections and generate ALWAYS/NEVER rules."""
    if summary.get("prompt_type") != "human" or summary.get("outcome") != "correction":
        return None
    corrected = summary.get("corrected_action", "")
    rejected = summary.get("rejected_action", "")
    if not corrected and not rejected:
        return None
    text = summary.get("text", "")
    components = summary.get("components", [])
    if corrected:
        action = f"ALWAYS {corrected}"
    else:
        action = f"NEVER {rejected}"
    if text:
        trigger = f"When {text}"
    else:
        trigger = f"When correcting {', '.join(components)}"
    reason = f"Human correction detected: {text or rejected or corrected}"
    candidate = _build_candidate(
        summary, ctx,
        type="architectural_pattern",
        action=action,
        trigger=trigger,
        reason=reason,
        importance=8,
        severity="major",
    )
    if candidate is None:
        return None
    return (0.95, candidate)


def detect_explicit_remember(summary, ctx):
    """Detect explicit 'remember'/'don't forget' instructions."""
    outcome = summary.get("outcome")
    if outcome not in ("preference", "decision"):
        return None
    text = summary.get("text", "")
    if not text:
        return None
    text_lower = text.lower()
    if not any(kw in text_lower for kw in _REMEMBER_KEYWORDS):
        return None
    candidate = _build_candidate(
        summary, ctx,
        type="architectural_pattern",
        action=f"ALWAYS {text}",
        trigger=f"When {text}",
        reason=f"Explicit remember instruction: {text}",
        importance=6,
        severity="minor",
    )
    if candidate is None:
        return None
    return (0.85, candidate)


def detect_bug_fixed(summary, ctx):
    """Detect bug-fix outcomes and generate preventive rules."""
    if summary.get("outcome") != "bug_fixed":
        return None
    error_text = summary.get("error_text", "")
    text = summary.get("text", "")
    if not error_text and not text:
        return None
    components = summary.get("components", [])
    source = error_text or text
    candidate = _build_candidate(
        summary, ctx,
        type="bug_fix",
        action=f"ALWAYS check for {source} when modifying {', '.join(components)}",
        trigger=f"When fixing bugs in {', '.join(components)}",
        reason=f"Bug fixed: {source}",
        importance=7,
        severity="major",
    )
    if candidate is None:
        return None
    return (0.70, candidate)


def detect_decision(summary, ctx):
    """Detect architectural decisions worth recording."""
    if summary.get("outcome") != "decision":
        return None
    text = summary.get("text", "")
    if not text:
        return None
    components = summary.get("components", [])
    candidate = _build_candidate(
        summary, ctx,
        type="architectural_pattern",
        action=f"ALWAYS {text}",
        trigger=f"When deciding on {', '.join(components)}",
        reason=f"Architectural decision: {text}",
        importance=5,
        severity="minor",
    )
    if candidate is None:
        return None
    return (0.60, candidate)


def detect_workaround(summary, ctx):
    """Detect workarounds and flag them as technical debt."""
    if summary.get("outcome") != "workaround":
        return None
    text = summary.get("text", "")
    if not text:
        return None
    components = summary.get("components", [])
    candidate = _build_candidate(
        summary, ctx,
        type="bug_fix",
        debt_level="workaround",
        action=f"ALWAYS {text}",
        trigger=f"When working around {', '.join(components)}",
        reason=f"Workaround applied: {text}",
        importance=6,
        severity="major",
    )
    if candidate is None:
        return None
    return (0.55, candidate)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def evaluate_core(summary, paths, ctx):
    """Evaluate a structured summary for learnable moments.

    Returns dict with signals_detected, auto_logged, suggestions, skipped_invalid.
    """
    if not ctx.get("evaluate_enabled", True):
        return {"exit_code": 0, "status": "ok", "disabled": True,
                "signals_detected": 0, "auto_logged": [], "suggestions": [], "skipped_invalid": []}

    if not isinstance(summary, dict):
        return {"exit_code": 1, "status": "error", "message": "summary must be a dict",
                "signals_detected": 0, "auto_logged": [], "suggestions": [], "skipped_invalid": []}

    _start = time.perf_counter()

    detectors = [
        detect_human_correction,
        detect_explicit_remember,
        detect_bug_fixed,
        detect_decision,
        detect_workaround,
    ]
    signals = []
    for detector in detectors:
        result = detector(summary, ctx)
        if result is not None:
            signals.append(result)

    signals.sort(key=lambda x: x[0], reverse=True)

    max_per_turn = ctx.get("evaluate_max_per_turn", 3)
    signals = signals[:max_per_turn]

    threshold = ctx.get("evaluate_auto_log_threshold", 0.9)
    auto_logged = []
    suggestions = []
    skipped_invalid = []

    for confidence, candidate in signals:
        errors = validate_entry(candidate, ctx)
        if errors:
            skipped_invalid.append({
                "confidence": confidence,
                "candidate": candidate,
                "errors": errors,
            })
            continue

        if confidence >= threshold:
            result = log_core(json.dumps(candidate), paths, ctx)
            status = result.get("status", "")
            auto_logged.append({
                "confidence": confidence,
                "status": status,
                "type": candidate["type"],
                "trigger": candidate["trigger"],
                "action": candidate["action"],
                "components": candidate.get("components", []),
                "files_touched": candidate.get("files_touched", []),
                "domain": candidate.get("domain", "tooling"),
            })
        else:
            suggestions.append({
                "confidence": confidence,
                "candidate": candidate,
            })

    latency_ms = round((time.perf_counter() - _start) * 1000, 2)
    log_event(paths, "evaluate",
              signals_detected=len(signals),
              auto_logged=len(auto_logged),
              suggested=len(suggestions),
              skipped_invalid=len(skipped_invalid),
              latency_ms=latency_ms)

    return {
        "exit_code": 0,
        "status": "ok",
        "signals_detected": len(signals),
        "auto_logged": auto_logged,
        "suggestions": suggestions,
        "skipped_invalid": skipped_invalid,
    }
