# Copyright (C) 2026 Mnemoq
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Lightweight promotion feedback loop for the Sleep Cycle.

Records which entries the consolidation pass proposed for promotion and whether
those entries were subsequently *reinforced* (their access_count grew after the
proposal). This turns the one-way "propose and forget" cycle into an observable
loop: R6's metrics can then tell whether promoted candidates actually earn their
retrieval, without R7 changing any candidacy behavior.

Architecture mirrors ``homeostasis.py`` deliberately — a best-effort JSON state
file (``<memory_dir>/.promotion_state.json``) whose helpers never raise, so a
corrupt or unwritable file degrades to a no-op rather than disrupting a cycle.

State shape::

    {"<key>": {"proposed_access": int,   # access_count when first proposed
               "proposed_score": float,
               "domain": str,
               "reinforced": bool}}       # access grew on a later pass

``<key>`` is stable per entry: ``"<step>:<trigger-prefix>"``.
"""

from __future__ import annotations

import json
import os

STATE_FILENAME = ".promotion_state.json"


def _state_path(paths):
    return os.path.join(paths.memory_dir, STATE_FILENAME)


def load_state(paths):
    """Load promotion state. Returns {} on any error (best-effort)."""
    path = _state_path(paths)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(paths, state):
    """Persist promotion state. Silently ignores failures (best-effort)."""
    try:
        with open(_state_path(paths), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=0)
    except OSError:
        pass


def entry_key(entry):
    """Stable per-entry key: step + a short trigger prefix."""
    trigger = (entry.get("trigger", "") or "").strip()[:40]
    return f"{entry.get('step', '?')}:{trigger}"


def record_and_follow_up(state, candidates, current_by_key):
    """Update `state` in place and return a follow-up summary.

    Args:
        state: the mutable promotion-state dict (from load_state).
        candidates: list of (score, entry) tuples proposed this pass.
        current_by_key: {entry_key: access_count} over all active entries, used
            to detect reinforcement of previously-proposed candidates.

    Returns:
        {"tracked": int, "reinforced": int} — how many previously-proposed
        candidates are still tracked and how many grew in access since proposal.
    """
    # Follow-up: did any previously-proposed candidate gain access since?
    reinforced = 0
    tracked = 0
    for key, rec in state.items():
        if not isinstance(rec, dict):
            continue
        tracked += 1
        now_access = current_by_key.get(key)
        if now_access is None:
            continue
        if now_access > int(rec.get("proposed_access", 0) or 0) and not rec.get("reinforced"):
            rec["reinforced"] = True
        if rec.get("reinforced"):
            reinforced += 1

    # Record this pass's candidates as tracked (idempotent per key).
    for score, entry in candidates:
        key = entry_key(entry)
        rec = state.get(key)
        if not isinstance(rec, dict):
            state[key] = {
                "proposed_access": int(entry.get("access_count", 0) or 0),
                "proposed_score": round(float(score), 4),
                "domain": entry.get("domain", ""),
                "reinforced": False,
            }

    return {"tracked": tracked, "reinforced": reinforced}
