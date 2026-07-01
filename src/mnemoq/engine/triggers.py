# Copyright (C) 2026 Mnemoq
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Sleep cycle trigger checks for the memory engine."""

import json
import os
from datetime import datetime, timezone

from mnemoq.engine.metrics import _metrics_path, _si


def _last_consolidation_event(paths):
    """Get the most recent 'consolidate' event dict from metrics.jsonl, or None.

    ponytail: O(n) scan of entire metrics file. Upgrade path: reverse-read
    or cache in a sidecar file. Fine for <10k lines.
    """
    path = _metrics_path(paths)
    if not os.path.exists(path):
        return None
    last = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event_type") == "consolidate":
                last = event
    return last


def _last_consolidation_ts(paths):
    """Timestamp of the last consolidate event, or None (back-compat wrapper)."""
    event = _last_consolidation_event(paths)
    return event.get("ts") if event else None


def _effective_sleep_days(base_days, ctx, last_event):
    """Self-damping consolidation cadence (Hog1 loop).

    Scales base_days by the last consolidation's activity: a busy pass widens
    the interval (damp storms during heavy capture), a no-op narrows it (catch
    up when churning but quiet). Bounded by consolidation_interval_adjustment
    and clamped so the time trigger can never be disabled.

    adjustment == 0 (or no prior consolidation) → returns base_days unchanged.
    """
    adjustment = ctx.get("consolidation_interval_adjustment", 0.25)
    if not adjustment or adjustment <= 0 or not last_event:
        return base_days
    activity = (_si(last_event.get("promotion_candidates"))
                + _si(last_event.get("contradictions"))
                + _si(last_event.get("stale_entries")))
    ref = ctx.get("sleep_cycle_unresolved_threshold", 20) or 20
    a = min(1.0, activity / ref) if ref > 0 else 0.0
    factor = 1 + adjustment * (2 * a - 1)  # a=0 -> 1-adj, a=1 -> 1+adj
    eff = base_days * factor
    floor = max(0.5, base_days * (1 - adjustment))
    ceiling = base_days * (1 + adjustment)
    return max(floor, min(ceiling, eff))


def check_sleep_cycle(paths, ctx, unresolved_count):
    """Check all sleep cycle triggers.
    Returns (due: bool, reasons: list[str]).
    """
    reasons = []

    # Threshold: unresolved entries
    unresolved_threshold = ctx.get("sleep_cycle_unresolved_threshold", 20)
    if unresolved_threshold and unresolved_threshold > 0 and unresolved_count > unresolved_threshold:
        reasons.append("threshold")

    # Time-based: days since last consolidation. The effective interval is
    # self-damped by the last pass's activity (Hog1 loop) — this modulates ONLY
    # the time cadence, never the threshold/quarantine safety nets below.
    sleep_cycle_days = ctx.get("sleep_cycle_days", 1)
    if sleep_cycle_days and sleep_cycle_days > 0:
        last_event = _last_consolidation_event(paths)
        effective_days = _effective_sleep_days(sleep_cycle_days, ctx, last_event)
        last_ts = last_event.get("ts") if last_event else None
        if not last_ts:
            reasons.append("time")  # never consolidated
        else:
            try:
                last = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                elapsed = datetime.now(timezone.utc) - last
                # fractional days so sub-day narrowing is honoured
                if elapsed.total_seconds() / 86400.0 >= effective_days:
                    reasons.append("time")
            except (ValueError, TypeError):
                reasons.append("time")  # can't parse -> safer to trigger

    # Quarantine growth
    quarantine_threshold = ctx.get("sleep_cycle_quarantine_threshold", 20)
    if quarantine_threshold and quarantine_threshold > 0:
        if os.path.exists(paths.quarantine_path):
            with open(paths.quarantine_path, encoding="utf-8") as f:
                count = sum(1 for line in f if line.strip())
            if count >= quarantine_threshold:
                reasons.append("quarantine")

    return len(reasons) > 0, reasons
