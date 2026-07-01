# Copyright (C) 2026 Mnemoq
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Per-domain adaptive auto-log threshold (homeostasis).

Biomimicry-derived control loop (see nimbalyst-local/plans/
biomimicry-adaptive-control.md). Turns the single static
EVALUATE_AUTO_LOG_THRESHOLD into a per-domain *regulated variable*.

State lives in ``<memory_dir>/.domain_state.json``:

    {"<domain>": {"offset": float,
                  "accept": int,
                  "detector_reject": int,
                  "actuation_reject": int}}

Two upward forces raise a domain's effective threshold, both decaying back
toward the base:

  * Feedforward inhibition (flood control): ``offset += bump`` on each auto-log,
    ``offset *= decay`` each event. Under sustained flooding the offset
    converges to ``bump / (1 - decay)`` — set that to the ceiling.
  * Reject bias (detector-quality + redundancy): a volume-gated additive term
    ``reject_gain * reject_rate`` that raises the bar for domains whose
    detectors emit invalid (CONFLICT/QUARANTINED) or redundant (DUPLICATE)
    entries.

The accept-driven *lowering* half of the adaptive-threshold idea is deliberately
deferred to a periodic recompute that reads access-based usefulness — lowering
the bar off synchronous "added" counts would be a runaway positive-feedback
loop. ``accept`` is tracked here only to feed that future recompute and the
sample gate.

All I/O is best-effort: helpers never raise, so a corrupt/unwritable state file
degrades to global (non-adaptive) behaviour rather than disrupting the engine.
"""

from __future__ import annotations

import json
import os

STATE_FILENAME = ".domain_state.json"

# Which log_core status maps to which counter.
_ACCEPT_STATUSES = {"added"}
_ACTUATION_REJECT_STATUSES = {"duplicate", "semantic_duplicate"}
_DETECTOR_REJECT_STATUSES = {"conflict", "quarantined"}


def _state_path(paths):
    return os.path.join(paths.memory_dir, STATE_FILENAME)


def _blank():
    return {"offset": 0.0, "accept": 0, "detector_reject": 0, "actuation_reject": 0}


def load_state(paths):
    """Load domain state. Returns {} on any error (best-effort)."""
    path = _state_path(paths)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(paths, state):
    """Persist domain state. Silently ignores failures (best-effort)."""
    try:
        with open(_state_path(paths), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=0)
    except OSError:
        pass


def _domain(state, domain):
    """Return the entry for `domain`, creating a blank one if absent."""
    entry = state.get(domain)
    if not isinstance(entry, dict):
        entry = _blank()
        state[domain] = entry
    else:
        for k, v in _blank().items():
            entry.setdefault(k, v)
    return entry


def decay_all(state, decay):
    """Relax every domain's offset toward 0 ('between events' decay).

    Offsets that fall below a small epsilon are zeroed and — if the domain has
    no accumulated counters — dropped entirely to keep the file compact.
    """
    for domain in list(state.keys()):
        entry = _domain(state, domain)
        entry["offset"] = round(entry["offset"] * decay, 6)
        if entry["offset"] < 1e-4:
            entry["offset"] = 0.0
            if not (entry["accept"] or entry["detector_reject"]
                    or entry["actuation_reject"]):
                del state[domain]


def effective_threshold(state, domain, base, ctx):
    """Compute the per-domain effective auto-log threshold.

    threshold = clamp(base + feedforward_offset + reject_bias,
                      base - floor, base + ceiling)

    reject_bias is applied only once the domain has accumulated at least
    `adaptive_min_samples` outcome events (volume gate) — new/low-traffic
    domains fall back to the base threshold.
    """
    floor = ctx.get("adaptive_offset_floor", 0.1)
    ceiling = ctx.get("adaptive_offset_ceiling", 0.2)
    entry = state.get(domain)
    if not isinstance(entry, dict):
        return base

    offset = float(entry.get("offset", 0.0) or 0.0)

    reject_bias = 0.0
    samples = (int(entry.get("accept", 0) or 0)
               + int(entry.get("detector_reject", 0) or 0)
               + int(entry.get("actuation_reject", 0) or 0))
    min_samples = ctx.get("adaptive_min_samples", 10)
    if samples >= min_samples:
        rejects = (int(entry.get("detector_reject", 0) or 0)
                   + int(entry.get("actuation_reject", 0) or 0))
        reject_rate = rejects / samples
        reject_bias = ctx.get("adaptive_reject_gain", 0.15) * reject_rate

    eff = base + offset + reject_bias
    return max(base - floor, min(base + ceiling, eff))


def record_auto_log(state, domain, ctx):
    """Feedforward inhibition: bump the domain's offset after an auto-log."""
    entry = _domain(state, domain)
    ceiling = ctx.get("adaptive_offset_ceiling", 0.2)
    bump = ctx.get("adaptive_bump", 0.02)
    entry["offset"] = round(min(ceiling, entry["offset"] + bump), 6)


def record_outcome(state, domain, status):
    """Update accept/reject counters from a log_core status string."""
    entry = _domain(state, domain)
    s = (status or "").lower()
    if s in _ACCEPT_STATUSES:
        entry["accept"] += 1
    elif s in _ACTUATION_REJECT_STATUSES:
        entry["actuation_reject"] += 1
    elif s in _DETECTOR_REJECT_STATUSES:
        entry["detector_reject"] += 1
