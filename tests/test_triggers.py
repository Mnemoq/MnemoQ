"""Tests for sleep cycle trigger logic in engine/triggers.py.

Direct-import tests (follows test_server.py exception pattern per AGENTS.md).
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mnemoq.engine.triggers import (
    _effective_sleep_days,
    _last_consolidation_ts,
    check_sleep_cycle,
)


class _Paths:
    """Minimal paths stub for trigger tests."""
    def __init__(self, memory_dir):
        self.memory_dir = str(memory_dir)
        self.quarantine_path = os.path.join(str(memory_dir), "quarantine.jsonl")


@pytest.fixture
def temp_memory(tmp_path):
    """Create a temp memory dir with empty quarantine.jsonl."""
    q = tmp_path / "quarantine.jsonl"
    q.write_text("")
    return _Paths(tmp_path)


def _write_metrics(paths, events):
    """Write metrics.jsonl with given event dicts."""
    path = os.path.join(paths.memory_dir, "metrics.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


class TestThresholdTrigger:
    @pytest.mark.smoke
    def test_fires_above_default(self, temp_memory):
        ctx = {"sleep_cycle_days": 0, "sleep_cycle_quarantine_threshold": 0}
        due, reasons = check_sleep_cycle(temp_memory, ctx, 21)
        assert due
        assert "threshold" in reasons

    @pytest.mark.smoke
    def test_does_not_fire_at_default(self, temp_memory):
        ctx = {"sleep_cycle_days": 0, "sleep_cycle_quarantine_threshold": 0}
        due, reasons = check_sleep_cycle(temp_memory, ctx, 20)
        assert not due
        assert reasons == []

    def test_respects_configured_threshold(self, temp_memory):
        ctx = {"sleep_cycle_days": 0, "sleep_cycle_quarantine_threshold": 0,
               "sleep_cycle_unresolved_threshold": 50}
        due, reasons = check_sleep_cycle(temp_memory, ctx, 26)
        assert not due
        due, reasons = check_sleep_cycle(temp_memory, ctx, 51)
        assert "threshold" in reasons

    def test_disabled_when_zero(self, temp_memory):
        ctx = {"sleep_cycle_days": 0, "sleep_cycle_quarantine_threshold": 0,
               "sleep_cycle_unresolved_threshold": 0}
        due, reasons = check_sleep_cycle(temp_memory, ctx, 1000)
        assert "threshold" not in reasons


class TestTimeTrigger:
    def test_fires_when_never_consolidated(self, temp_memory):
        ctx = {"sleep_cycle_days": 1, "sleep_cycle_quarantine_threshold": 0}
        due, reasons = check_sleep_cycle(temp_memory, ctx, 0)
        assert "time" in reasons

    def test_fires_when_old_consolidation(self, temp_memory):
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        _write_metrics(temp_memory, [{"event_type": "consolidate", "ts": old_ts}])
        ctx = {"sleep_cycle_days": 1, "sleep_cycle_quarantine_threshold": 0}
        due, reasons = check_sleep_cycle(temp_memory, ctx, 0)
        assert "time" in reasons

    def test_does_not_fire_when_recent(self, temp_memory):
        recent_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _write_metrics(temp_memory, [{"event_type": "consolidate", "ts": recent_ts}])
        ctx = {"sleep_cycle_days": 1, "sleep_cycle_quarantine_threshold": 0}
        due, reasons = check_sleep_cycle(temp_memory, ctx, 0)
        assert "time" not in reasons

    def test_disabled_when_zero(self, temp_memory):
        ctx = {"sleep_cycle_days": 0, "sleep_cycle_quarantine_threshold": 0}
        due, reasons = check_sleep_cycle(temp_memory, ctx, 0)
        assert "time" not in reasons


class TestQuarantineTrigger:
    @pytest.mark.smoke
    def test_fires_at_threshold(self, temp_memory):
        with open(temp_memory.quarantine_path, "w") as f:
            for _ in range(20):
                f.write('{"raw": "x", "reason": "test"}\n')
        ctx = {"sleep_cycle_days": 0, "sleep_cycle_quarantine_threshold": 20}
        due, reasons = check_sleep_cycle(temp_memory, ctx, 0)
        assert "quarantine" in reasons

    def test_does_not_fire_below_threshold(self, temp_memory):
        with open(temp_memory.quarantine_path, "w") as f:
            for _ in range(19):
                f.write('{"raw": "x", "reason": "test"}\n')
        ctx = {"sleep_cycle_days": 0, "sleep_cycle_quarantine_threshold": 20}
        due, reasons = check_sleep_cycle(temp_memory, ctx, 0)
        assert "quarantine" not in reasons

    def test_disabled_when_zero(self, temp_memory):
        with open(temp_memory.quarantine_path, "w") as f:
            for _ in range(100):
                f.write('{"raw": "x", "reason": "test"}\n')
        ctx = {"sleep_cycle_days": 0, "sleep_cycle_quarantine_threshold": 0}
        due, reasons = check_sleep_cycle(temp_memory, ctx, 0)
        assert "quarantine" not in reasons

    def test_ignores_blank_lines(self, temp_memory):
        with open(temp_memory.quarantine_path, "w") as f:
            f.write('{"raw": "x", "reason": "test"}\n')
            f.write("\n\n\n")
            f.write('{"raw": "y", "reason": "test"}\n')
        ctx = {"sleep_cycle_days": 0, "sleep_cycle_quarantine_threshold": 2}
        due, reasons = check_sleep_cycle(temp_memory, ctx, 0)
        assert "quarantine" in reasons


class TestLastConsolidationTs:
    def test_returns_none_when_no_metrics_file(self, temp_memory):
        assert _last_consolidation_ts(temp_memory) is None

    def test_returns_last_consolidate_ts(self, temp_memory):
        ts1 = "2025-01-01T00:00:00Z"
        ts2 = "2025-06-01T00:00:00Z"
        _write_metrics(temp_memory, [
            {"event_type": "retrieval", "ts": ts1},
            {"event_type": "consolidate", "ts": ts1},
            {"event_type": "consolidate", "ts": ts2},
        ])
        assert _last_consolidation_ts(temp_memory) == ts2

    def test_returns_none_when_no_consolidate_events(self, temp_memory):
        _write_metrics(temp_memory, [{"event_type": "retrieval", "ts": "2025-01-01T00:00:00Z"}])
        assert _last_consolidation_ts(temp_memory) is None


class TestSelfDampingInterval:
    """Part A: _effective_sleep_days (Hog1 cadence damping)."""

    def _event(self, **fields):
        return {"event_type": "consolidate", "ts": "2025-01-01T00:00:00Z", **fields}

    def test_adjustment_zero_returns_base(self):
        ctx = {"consolidation_interval_adjustment": 0.0}
        ev = self._event(promotion_candidates=100)
        assert _effective_sleep_days(1, ctx, ev) == 1

    def test_no_last_event_returns_base(self):
        ctx = {"consolidation_interval_adjustment": 0.25}
        assert _effective_sleep_days(1, ctx, None) == 1

    def test_high_activity_widens(self):
        # activity 20 == ref -> a=1 -> factor 1.25
        ctx = {"consolidation_interval_adjustment": 0.25,
               "sleep_cycle_unresolved_threshold": 20}
        ev = self._event(promotion_candidates=20)
        assert _effective_sleep_days(4, ctx, ev) == 5.0  # 4 * 1.25

    def test_no_op_narrows(self):
        # activity 0 -> a=0 -> factor 0.75
        ctx = {"consolidation_interval_adjustment": 0.25,
               "sleep_cycle_unresolved_threshold": 20}
        ev = self._event(promotion_candidates=0)
        assert _effective_sleep_days(4, ctx, ev) == 3.0  # 4 * 0.75

    def test_floor_never_below_half_day(self):
        ctx = {"consolidation_interval_adjustment": 0.9,
               "sleep_cycle_unresolved_threshold": 20}
        ev = self._event()  # activity 0 -> factor 0.1 -> 1*0.1=0.1, floored to 0.5
        assert _effective_sleep_days(1, ctx, ev) == 0.5

    def test_activity_sums_all_components(self):
        ctx = {"consolidation_interval_adjustment": 0.5,
               "sleep_cycle_unresolved_threshold": 30}
        # 10 + 10 + 10 = 30 == ref -> a=1 -> factor 1.5
        ev = self._event(promotion_candidates=10, contradictions=10, stale_entries=10)
        assert _effective_sleep_days(2, ctx, ev) == 3.0


class TestDampedTimeTrigger:
    """Part A wired through check_sleep_cycle: a busy last pass defers the
    time trigger that a no-op pass would fire."""

    def _consolidation(self, days_ago, **fields):
        ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        return {"event_type": "consolidate", "ts": ts, **fields}

    def test_busy_pass_defers_time_trigger(self, temp_memory):
        # elapsed ~1.04 days; base 1 day. High activity -> effective 1.25 -> defer.
        _write_metrics(temp_memory, [self._consolidation(
            1.04, promotion_candidates=50)])
        ctx = {"sleep_cycle_days": 1, "sleep_cycle_quarantine_threshold": 0,
               "sleep_cycle_unresolved_threshold": 20,
               "consolidation_interval_adjustment": 0.25}
        due, reasons = check_sleep_cycle(temp_memory, ctx, 0)
        assert "time" not in reasons

    def test_no_op_pass_fires_time_trigger(self, temp_memory):
        # same elapsed, but no activity -> effective 0.75 -> fires.
        _write_metrics(temp_memory, [self._consolidation(
            1.04, promotion_candidates=0)])
        ctx = {"sleep_cycle_days": 1, "sleep_cycle_quarantine_threshold": 0,
               "sleep_cycle_unresolved_threshold": 20,
               "consolidation_interval_adjustment": 0.25}
        due, reasons = check_sleep_cycle(temp_memory, ctx, 0)
        assert "time" in reasons

    def test_safety_nets_fire_regardless_of_damping(self, temp_memory):
        # Busy pass would defer 'time', but the unresolved threshold still fires.
        _write_metrics(temp_memory, [self._consolidation(
            0.1, promotion_candidates=50)])
        ctx = {"sleep_cycle_days": 1, "sleep_cycle_quarantine_threshold": 0,
               "sleep_cycle_unresolved_threshold": 20,
               "consolidation_interval_adjustment": 0.25}
        due, reasons = check_sleep_cycle(temp_memory, ctx, 25)
        assert due
        assert "threshold" in reasons


class TestMultipleTriggers:
    def test_all_three_can_fire(self, temp_memory):
        with open(temp_memory.quarantine_path, "w") as f:
            for _ in range(25):
                f.write('{"raw": "x", "reason": "test"}\n')
        ctx = {"sleep_cycle_days": 1, "sleep_cycle_quarantine_threshold": 20}
        due, reasons = check_sleep_cycle(temp_memory, ctx, 60)
        assert due
        assert "threshold" in reasons
        assert "time" in reasons
        assert "quarantine" in reasons
