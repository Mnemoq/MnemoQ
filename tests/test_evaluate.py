"""Integration test for per-prompt evaluation via CLI subprocess.

Per AGENTS.md, engine modules are tested via cli.py CLI integration.
Mirrors tests/test_auto_learn.py patterns.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _make_temp_project():
    """Create a temp project with learnings.jsonl, metrics.jsonl, config.json."""
    tmpdir = tempfile.mkdtemp()
    project_dir = Path(tmpdir)
    memory_dir = project_dir / "memory"
    memory_dir.mkdir()

    (memory_dir / "learnings.jsonl").touch()
    (memory_dir / "metrics.jsonl").touch()

    config = {
        "project_name": "test-eval",
        "tuning": {
            "evaluate_enabled": True,
            "evaluate_auto_log_threshold": 0.9,
            "evaluate_max_per_turn": 3,
        },
    }
    with open(memory_dir / "config.json", "w") as f:
        json.dump(config, f)

    return project_dir, memory_dir


def _run_evaluate(summary_json, memory_dir, project_dir):
    """Run mnemoq --evaluate with the given JSON string."""
    src_dir = str(Path(__file__).parent.parent / "src")
    env = dict(os.environ, PYTHONPATH=src_dir)
    return subprocess.run(
        [sys.executable, "-m", "mnemoq.cli", "--evaluate", summary_json,
         "--memory-dir", str(memory_dir)],
        capture_output=True, text=True, env=env, cwd=str(project_dir),
    )


def _read_learnings(memory_dir):
    with open(memory_dir / "learnings.jsonl") as f:
        return [json.loads(line) for line in f if line.strip()]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_auto_log_human_correction():
    """High-confidence human-correction summary → entry auto-logged to learnings.jsonl."""
    project_dir, memory_dir = _make_temp_project()
    summary = json.dumps({
        "step": 1,
        "prompt_type": "human",
        "outcome": "correction",
        "corrected_action": "use async log writes",
        "text": "corrected the logger",
        "components": ["Logger"],
        "files_touched": ["src/log.py"],
    })
    result = _run_evaluate(summary, memory_dir, project_dir)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    entries = _read_learnings(memory_dir)
    assert len(entries) == 1
    assert entries[0]["source_agent"] == "system"
    assert entries[0]["action"].startswith("ALWAYS ")


def _make_temp_project_adaptive(threshold, **adaptive):
    """Temp project with adaptive thresholds enabled."""
    project_dir, memory_dir = _make_temp_project()
    tuning = {
        "evaluate_enabled": True,
        "evaluate_auto_log_threshold": threshold,
        "evaluate_max_per_turn": 3,
        "adaptive_thresholds": True,
    }
    tuning.update(adaptive)
    config = {"project_name": "test-eval", "tuning": tuning}
    with open(memory_dir / "config.json", "w") as f:
        json.dump(config, f)
    return project_dir, memory_dir


_DECISION_SUMMARY = json.dumps({
    "step": 1,
    "outcome": "decision",
    "text": "route all writes through the repository layer",
    "components": ["Repo"],
    "files_touched": ["src/repo.py"],
})


def test_adaptive_off_writes_no_state_file():
    """With the flag off (default), no .domain_state.json is created."""
    project_dir, memory_dir = _make_temp_project_threshold(0.5)
    result = _run_evaluate(_DECISION_SUMMARY, memory_dir, project_dir)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert _read_learnings(memory_dir), "0.60 decision should log at threshold 0.5"
    assert not (memory_dir / ".domain_state.json").exists()


def test_adaptive_on_writes_state_and_bumps_offset():
    """With the flag on, an auto-log records a positive offset for its domain."""
    project_dir, memory_dir = _make_temp_project_adaptive(0.5)
    result = _run_evaluate(_DECISION_SUMMARY, memory_dir, project_dir)
    assert result.returncode == 0, f"stderr: {result.stderr}"

    state_path = memory_dir / ".domain_state.json"
    assert state_path.exists(), "adaptive on must persist .domain_state.json"
    state = json.loads(state_path.read_text())
    # decision on src/repo.py -> some domain with a bumped offset + accept count
    assert len(state) == 1
    entry = next(iter(state.values()))
    assert entry["offset"] > 0
    assert entry["accept"] == 1


def test_adaptive_high_offset_suppresses_auto_log():
    """A pre-existing high offset raises the domain threshold above the
    detector's confidence, demoting the auto-log to a suggestion. Isolates the
    per-domain threshold effect without relying on dedup behaviour."""
    from mnemoq.engine.auto_learn import _derive_domain

    project_dir, memory_dir = _make_temp_project_adaptive(0.5)
    domain = _derive_domain("src/repo.py")
    # offset 0.2 -> effective threshold 0.70 (after decay 0.18 -> 0.68),
    # both above the 0.60 decision confidence, so it must NOT auto-log.
    (memory_dir / ".domain_state.json").write_text(json.dumps({
        domain: {"offset": 0.2, "accept": 0,
                 "detector_reject": 0, "actuation_reject": 0}
    }))

    result = _run_evaluate(_DECISION_SUMMARY, memory_dir, project_dir)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert not _read_learnings(memory_dir), \
        "0.60 decision must be suppressed when the domain threshold is ~0.68"


def _make_temp_project_threshold(threshold):
    """Temp project with a custom evaluate_auto_log_threshold."""
    project_dir, memory_dir = _make_temp_project()
    config = {
        "project_name": "test-eval",
        "tuning": {
            "evaluate_enabled": True,
            "evaluate_auto_log_threshold": threshold,
            "evaluate_max_per_turn": 3,
        },
    }
    with open(memory_dir / "config.json", "w") as f:
        json.dump(config, f)
    return project_dir, memory_dir


def test_workaround_auto_logs_at_threshold_0_5():
    """Boundary: the lowest-confidence detector (workaround, 0.55) auto-logs at
    the new default threshold of 0.5. Guards the #3 threshold change."""
    project_dir, memory_dir = _make_temp_project_threshold(0.5)
    summary = json.dumps({
        "step": 1,
        "prompt_type": "agent",
        "outcome": "workaround",
        "text": "pin urllib3 to <2 until the C extension is rebuilt",
        "components": ["HttpClient"],
        "files_touched": ["src/http.py"],
    })
    result = _run_evaluate(summary, memory_dir, project_dir)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    entries = _read_learnings(memory_dir)
    assert len(entries) == 1, "0.55 workaround should auto-log at threshold 0.5"
    assert entries[0]["debt_level"] == "workaround"
    assert entries[0]["action"].startswith("ALWAYS ")


def test_workaround_suggests_at_threshold_0_6():
    """Above the workaround confidence (0.55 < 0.6) it stays a suggestion."""
    project_dir, memory_dir = _make_temp_project_threshold(0.6)
    summary = json.dumps({
        "step": 1,
        "prompt_type": "agent",
        "outcome": "workaround",
        "text": "pin urllib3 to <2 until the C extension is rebuilt",
        "components": ["HttpClient"],
        "files_touched": ["src/http.py"],
    })
    result = _run_evaluate(summary, memory_dir, project_dir)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    entries = _read_learnings(memory_dir)
    assert len(entries) == 0, "0.55 workaround should NOT auto-log at threshold 0.6"


def test_suggest_only_medium_confidence():
    """Medium-confidence summary (decision, conf 0.60 < 0.9 threshold) → suggestion, no write."""
    project_dir, memory_dir = _make_temp_project()
    summary = json.dumps({
        "step": 1,
        "prompt_type": "agent",
        "outcome": "decision",
        "text": "use event sourcing for audit log",
        "components": ["AuditLog"],
        "files_touched": ["src/audit.py"],
    })
    result = _run_evaluate(summary, memory_dir, project_dir)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    entries = _read_learnings(memory_dir)
    assert len(entries) == 0
    assert "Suggested" in result.stdout or "suggestions" in result.stdout.lower()


def test_no_signal_outcome_none():
    """outcome=none → signals_detected:0, no write."""
    project_dir, memory_dir = _make_temp_project()
    summary = json.dumps({
        "step": 1,
        "prompt_type": "agent",
        "outcome": "none",
        "text": "nothing notable",
        "components": ["Foo"],
        "files_touched": ["src/foo.py"],
    })
    result = _run_evaluate(summary, memory_dir, project_dir)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    entries = _read_learnings(memory_dir)
    assert len(entries) == 0
    assert "Signals detected: 0" in result.stdout


def test_no_signal_empty_components():
    """Empty components → _build_candidate returns None → no signal."""
    project_dir, memory_dir = _make_temp_project()
    summary = json.dumps({
        "step": 1,
        "prompt_type": "human",
        "outcome": "correction",
        "corrected_action": "do X",
        "text": "fix",
        "components": [],
        "files_touched": ["src/x.py"],
    })
    result = _run_evaluate(summary, memory_dir, project_dir)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    entries = _read_learnings(memory_dir)
    assert len(entries) == 0


def test_dedup_on_re_run():
    """Running the same auto-log summary twice → second run reports duplicate, no double-write."""
    project_dir, memory_dir = _make_temp_project()
    summary = json.dumps({
        "step": 1,
        "prompt_type": "human",
        "outcome": "correction",
        "corrected_action": "use async log writes",
        "text": "corrected the logger",
        "components": ["Logger"],
        "files_touched": ["src/log.py"],
    })

    result1 = _run_evaluate(summary, memory_dir, project_dir)
    assert result1.returncode == 0

    result2 = _run_evaluate(summary, memory_dir, project_dir)
    assert result2.returncode == 0

    entries = _read_learnings(memory_dir)
    assert len(entries) == 1, "Second run should not append a duplicate entry"

    # Second run should report duplicate or semantic_duplicate status
    assert "duplicate" in result2.stdout.lower() or "DUPLICATE" in result2.stdout


def test_disabled_returns_no_write():
    """config.json with evaluate_enabled:false → disabled response, no write."""
    project_dir, memory_dir = _make_temp_project()

    config = {
        "project_name": "test-eval",
        "tuning": {"evaluate_enabled": False},
    }
    with open(memory_dir / "config.json", "w") as f:
        json.dump(config, f)

    summary = json.dumps({
        "step": 1,
        "prompt_type": "human",
        "outcome": "correction",
        "corrected_action": "use async writes",
        "text": "corrected",
        "components": ["Logger"],
        "files_touched": ["src/log.py"],
    })
    result = _run_evaluate(summary, memory_dir, project_dir)
    assert result.returncode == 0
    assert "disabled" in result.stdout.lower()

    entries = _read_learnings(memory_dir)
    assert len(entries) == 0


def test_invalid_downgrade_skipped():
    """High-confidence signal with invalid step -- skipped_invalid, not written to learnings.jsonl."""
    project_dir, memory_dir = _make_temp_project()
    summary = json.dumps({
        "step": 0,  # invalid — step must be positive
        "prompt_type": "human",
        "outcome": "correction",
        "corrected_action": "use async writes",
        "text": "corrected the logger",
        "components": ["Logger"],
        "files_touched": ["src/log.py"],
    })
    result = _run_evaluate(summary, memory_dir, project_dir)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    entries = _read_learnings(memory_dir)
    assert len(entries) == 0, "Invalid candidate should not be written to learnings.jsonl"

    # The verbose output should mention skipped/invalid
    assert "Skipped" in result.stdout or "skipped" in result.stdout.lower()
