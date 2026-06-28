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

from agent_memory.engine.triggers import _last_consolidation_ts, check_sleep_cycle


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
    def test_fires_above_default(self, temp_memory):
        ctx = {"sleep_cycle_days": 0, "sleep_cycle_quarantine_threshold": 0}
        due, reasons = check_sleep_cycle(temp_memory, ctx, 21)
        assert due
        assert "threshold" in reasons

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
