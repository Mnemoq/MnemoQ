# Copyright (C) 2026 Mnemoq
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Per-prompt evaluation module for the memory engine.

Pure detector functions receive a structured summary dict (no I/O).
evaluate_core() handles orchestration, validation, and auto-logging.
"""

from __future__ import annotations

import json
import time

from mnemoq.engine.auto_learn import _derive_domain
from mnemoq.engine.handlers import log_core
from mnemoq.engine.homeostasis import (
    decay_all,
    effective_threshold,
    load_state,
    record_auto_log,
    record_outcome,
    save_state,
)
from mnemoq.engine.metrics import log_event
from mnemoq.engine.validation import validate_entry

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

_REMEMBER_KEYWORDS = (
    # Direct memory requests
    "remember", "don't forget", "do not forget", "note this",
    "take note", "worth noting", "make a note", "jot this down",
    "keep this", "keep that", "hold onto this", "don't lose this",
    "save this", "store this", "log this", "record this",
    # Mnemonic / mental model
    "keep in mind", "bear in mind", "keep this in mind",
    "have in mind", "hold in mind", "carry this forward",
    "stick with", "stay with", "stick to", "hold to",
    # Directive / imperative
    "make sure", "be sure", "ensure", "make certain",
    "double-check", "double check", "triple-check",
    "verify that", "confirm that", "check that",
    "watch out for", "look out for", "be careful of",
    "be cautious of", "be wary of", "mind that", "mind you",
    "pay attention to", "be mindful of", "be aware of",
    "take care to", "take care that", "see to it that",
    # Importance / emphasis
    "important that", "it's important", "it is important",
    "critical that", "it's critical", "it is critical",
    "crucial that", "it's crucial", "it is crucial",
    "essential that", "it's essential", "it is essential",
    "vital that", "it's vital", "it is vital",
    "key thing", "key point", "the key is",
    "must", "need to", "gotta", "have to",
    # Temporal persistence
    "going forward", "from now on", "for future",
    "in the future", "next time", "every time",
    "whenever you", "any time you", "each time",
    "before you", "after you", "once you",
    # Preference / comparative
    "prefer", "preferably", "rather than", "instead of",
    "avoid", "refrain from", "steer clear of", "stay away from",
    "don't bother with", "no need to", "skip",
    "better to", "best to", "worse to", "you should",
    "you shouldn't", "you should not", "you must not",
    "you ought to", "you'd better", "you had better",
    # Always / never
    "always", "never", "forever", "invariably",
    "without fail", "by default", "as a rule",
    "as a general rule", "rule of thumb",
    # Anti-pattern / gotcha
    "gotcha", "pitfall", "trap", "stumbling block",
    "footgun", "sharp edge", "landmine", "trip up",
    "bite you", "come back to haunt", "got caught on",
    "easy to miss", "easy to forget", "easy to overlook",
    "not obvious", "it's not obvious", "tricky part",
    "the catch is", "here's the catch", "the trick is",
    "the tricky part", "the hard part is",
    "subtle", "not straightforward", "deceptively simple",
    # Convention / standard
    "convention", "by convention", "we follow",
    "the pattern is", "idiomatic", "best practice",
    "the standard is", "our standard", "we always do",
    "we never do", "we typically", "we usually",
    "in this codebase", "in this project", "in this repo",
    "house style", "style guide", "coding standard",
    # Constraint / boundary
    "don't exceed", "stay within", "keep under",
    "limit to", "cap at", "no more than",
    "at most", "at least", "minimum", "maximum",
    "upper bound", "lower bound", "hard limit",
    "soft limit", "threshold", "boundary",
    "constraint", "must not exceed", "cannot exceed",
    # Observation / discovery
    "turns out", "discovered", "found that",
    "realized", "learned that", "noticed",
    "observed", "the root cause is", "root cause",
    "the issue was", "the problem was", "the cause is",
    "what's happening is", "what's going on is",
    "the reason is", "because of", "due to",
    # Dependency / prerequisite
    "depends on", "requires", "prerequisite",
    "before doing", "needs to happen first",
    "must happen before", "can't do without",
    "relies on", "is required before",
    "won't work without", "fails without",
    # Prohibition / cessation
    "don't", "stop doing", "cease", "halt",
    "no longer", "not anymore", "get rid of",
    "remove this", "delete this", "drop this",
    "deprecate", "deprecated", "phase out",
    "don't do that", "not that", "not this way",
    "wrong way", "the wrong way", "not how",
    # Rationale / reasoning
    "the reason we", "the reason for", "rationale",
    "because we chose", "because we decided", "we chose to",
    "we decided to", "the motivation is", "motivation behind",
    "justification", "why we do", "why we use",
    "why we chose", "why we switched", "the thinking is",
    "the idea is that", "the principle is", "design principle",
    "architectural reason", "the trade-off is", "tradeoff is",
    "we traded", "in exchange for",
    # Update / change / migration
    "switched to", "migrated to", "moved to", "replaced with",
    "updated from", "upgraded from", "changed from",
    "converted to", "ported to", "refactored to",
    "transitioned to", "adopted", "rolled out",
    "phased in", "introduced in", "added in version",
    "as of version", "since version", "breaking change",
    "migration path", "upgrade path",
    # Warning / caution
    "warning", "caution", "caveat", "heads up",
    "risky", "risk of", "danger of", "dangerous",
    "fragile", "brittle", "flaky", "unreliable",
    "don't rely on", "not reliable", "not safe",
    "be careful when", "be careful with", "exercise caution",
    "proceed with caution", "at your own risk",
    "known issue", "known problem", "known limitation",
    # Environment / platform-specific
    "only on", "only works on", "specific to",
    "platform-specific", "depends on the platform",
    "on windows", "on linux", "on macos", "on mac",
    "in production", "in staging", "in development",
    "in ci", "locally", "in the cloud",
    "environment variable", "env var", "config-dependent",
    "version-specific", "requires version", "only supported",
    # Performance / optimization
    "bottleneck", "optimize", "optimization",
    "slow because", "slow when", "performance issue",
    "expensive operation", "costly", "inefficient",
    "o(n)", "o(n^2)", "o(log n)", "o(1)",
    "time complexity", "space complexity",
    "memory usage", "memory leak", "cpu intensive",
    "io bound", "cpu bound", "network bound",
    "cache this", "memoize", "lazy load", "eager load",
    "precompute", "batch", "chunk", "stream",
    # Testing / edge case
    "edge case", "corner case", "boundary case",
    "regression", "regression test", "test case",
    "unit test", "integration test", "e2e test",
    "should test", "need to test", "must test",
    "untested", "missing test", "no coverage",
    "test for", "assert that", "expect it to",
    "when testing", "in tests", "mock this",
    "stub this", "fixture", "snapshot",
)


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

    base_threshold = ctx.get("evaluate_auto_log_threshold", 0.9)

    # Per-domain adaptive threshold (homeostasis). Behaviour is byte-identical
    # to the static scalar when adaptive_thresholds is off (default). When on,
    # relax all offsets ('between events' decay) once per call, then use a
    # per-domain effective threshold and record feedforward/outcome signals.
    adaptive = ctx.get("adaptive_thresholds", False)
    state = None
    if adaptive:
        state = load_state(paths)
        decay_all(state, ctx.get("adaptive_decay", 0.9))

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

        domain = candidate.get("domain", "tooling")
        if adaptive:
            threshold = effective_threshold(state, domain, base_threshold, ctx)
        else:
            threshold = base_threshold

        if confidence >= threshold:
            result = log_core(json.dumps(candidate), paths, ctx)
            status = result.get("status", "")
            if adaptive:
                record_auto_log(state, domain, ctx)
                record_outcome(state, domain, status)
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

    if adaptive and state is not None:
        save_state(paths, state)

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
