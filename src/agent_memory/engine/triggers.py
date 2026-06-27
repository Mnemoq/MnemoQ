"""Sleep cycle trigger checks for the memory engine."""

import json
import os
from datetime import datetime, timezone

from agent_memory.engine.metrics import _metrics_path


def _last_consolidation_ts(paths):
    """Get timestamp of last consolidate event from metrics.jsonl.

    ponytail: O(n) scan of entire metrics file. Upgrade path: reverse-read
    or cache the timestamp in a sidecar file. Fine for <10k lines.
    """
    path = _metrics_path(paths)
    if not os.path.exists(path):
        return None
    last_ts = None
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
                last_ts = event.get("ts")
    return last_ts


def check_sleep_cycle(paths, ctx, unresolved_count):
    """Check all sleep cycle triggers.
    Returns (due: bool, reasons: list[str]).
    """
    reasons = []

    # Threshold: unresolved entries
    if unresolved_count > 50:
        reasons.append("threshold")

    # Time-based: days since last consolidation
    sleep_cycle_days = ctx.get("sleep_cycle_days", 7)
    if sleep_cycle_days and sleep_cycle_days > 0:
        last_ts = _last_consolidation_ts(paths)
        if not last_ts:
            reasons.append("time")  # never consolidated
        else:
            try:
                last = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                elapsed = datetime.now(timezone.utc) - last
                if elapsed.days >= sleep_cycle_days:
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
