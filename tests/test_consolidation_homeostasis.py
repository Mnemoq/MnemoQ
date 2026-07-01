"""Integration test for PR 3 Part B: the consolidation pass recomputes each
domain's access-based usefulness offset into .domain_state.json.

Direct-import style (per the test_triggers/test_config exception in AGENTS.md).
"""
import json

from conftest import _make_ctx, _make_paths

from mnemoq.engine.consolidation import consolidate_core


def _system_entry(domain, access_count, step, ts):
    return {
        "ts": ts,
        "step": step,
        "source_agent": "system",
        "type": "architectural_pattern",
        "domain": domain,
        "components": ["Thing"],
        "files_touched": ["src/thing.py"],
        "trigger": f"When touching {domain}",
        "action": "ALWAYS do the thing",
        "reason": "captured",
        "importance": 5,
        "severity": "minor",
        "resolved": False,
        "access_count": access_count,
    }


def _write_learnings(memory_dir, entries):
    with open(memory_dir / "learnings.jsonl", "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def test_consolidation_sets_usefulness_offset(temp_project):
    memory_dir = temp_project / "memory"
    # backend: 2 well-accessed entries (>= min_samples 2) -> lowered
    # frontend: 2 never-accessed entries -> offset stays 0
    entries = [
        _system_entry("backend", 5, 1, "2025-01-01T00:00:01Z"),
        _system_entry("backend", 5, 1, "2025-01-01T00:00:02Z"),
        _system_entry("frontend", 0, 1, "2025-01-01T00:00:03Z"),
        _system_entry("frontend", 0, 1, "2025-01-01T00:00:04Z"),
    ]
    _write_learnings(memory_dir, entries)

    paths = _make_paths(memory_dir, temp_project)
    ctx = _make_ctx(adaptive_thresholds=True, adaptive_min_samples=2)

    result = consolidate_core(None, False, True, paths, ctx)
    assert result["exit_code"] == 0

    state = json.loads((memory_dir / ".domain_state.json").read_text())
    # backend mean_access 5 == access_ref 5 -> usefulness 1.0 -> -gain (-0.1)
    assert state["backend"]["usefulness_offset"] == -0.1
    # frontend passed the gate but has zero access -> no lowering
    assert state["frontend"]["usefulness_offset"] == 0.0


def test_consolidation_no_state_when_flag_off(temp_project):
    memory_dir = temp_project / "memory"
    _write_learnings(memory_dir, [
        _system_entry("backend", 9, 1, "2025-01-01T00:00:01Z"),
        _system_entry("backend", 9, 1, "2025-01-01T00:00:02Z"),
    ])
    paths = _make_paths(memory_dir, temp_project)
    ctx = _make_ctx(adaptive_thresholds=False, adaptive_min_samples=2)

    result = consolidate_core(None, False, True, paths, ctx)
    assert result["exit_code"] == 0
    assert not (memory_dir / ".domain_state.json").exists()
