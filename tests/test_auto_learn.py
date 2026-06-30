"""Integration test for auto-learning via CLI subprocess.

Per AGENTS.md, engine modules are tested via cli.py CLI integration.
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

    # learnings.jsonl with an under-retrieved entry
    entry = {
        "ts": "2026-01-01T00:00:00Z",
        "step": 1,
        "source_agent": "gm",
        "type": "bug_fix",
        "domain": "tooling",
        "components": ["TestComp"],
        "files_touched": ["test.py"],
        "trigger": "When testing stuff",
        "action": "ALWAYS use pytest",
        "reason": "pytest is the standard",
        "importance": 7,
        "severity": "major",
        "resolved": False,
        "access_count": 1,
        "reinforcement_count": 6,
        "schema_version": 1,
    }
    with open(memory_dir / "learnings.jsonl", "w") as f:
        f.write(json.dumps(entry) + "\n")

    # metrics.jsonl — empty
    (memory_dir / "metrics.jsonl").touch()

    # config.json with auto-learn enabled
    config = {
        "project_name": "test-project",
        "tuning": {
            "auto_learn_enabled": True,
            "auto_learn_under_retrieved_access": 2,
            "auto_learn_under_retrieved_reinforcement": 5,
        },
    }
    with open(memory_dir / "config.json", "w") as f:
        json.dump(config, f)

    return project_dir, memory_dir


def test_auto_learn_generates_entries():
    """Run --auto-learn and verify entries are generated in learnings.jsonl."""
    project_dir, memory_dir = _make_temp_project()

    src_dir = str(Path(__file__).parent.parent / "src")
    env = dict(os.environ, PYTHONPATH=src_dir)

    result = subprocess.run(
        [sys.executable, "-m", "mnemoq.cli", "--auto-learn",
         "--memory-dir", str(memory_dir)],
        capture_output=True, text=True, env=env, cwd=str(project_dir),
    )

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    # Check learnings.jsonl has new entries
    with open(memory_dir / "learnings.jsonl") as f:
        lines = [json.loads(line) for line in f if line.strip()]

    # Original entry + at least one generated meta_learning
    assert len(lines) > 1
    generated = [e for e in lines if e.get("source_agent") == "system"]
    assert len(generated) >= 1

    # Verify meta_learning entries are resolved
    meta = [e for e in generated if e.get("type") == "meta_learning"]
    for m in meta:
        assert m["resolved"] is True

    # Verify bug_fix entries are unresolved
    bug_fixes = [e for e in generated if e.get("type") == "bug_fix"]
    for b in bug_fixes:
        assert b["resolved"] is False


def test_auto_learn_disabled():
    """Verify --auto-learn returns early when disabled."""
    project_dir, memory_dir = _make_temp_project()

    # Override config to disable
    config = {
        "project_name": "test-project",
        "tuning": {"auto_learn_enabled": False},
    }
    with open(memory_dir / "config.json", "w") as f:
        json.dump(config, f)

    src_dir = str(Path(__file__).parent.parent / "src")
    env = dict(os.environ, PYTHONPATH=src_dir)

    result = subprocess.run(
        [sys.executable, "-m", "mnemoq.cli", "--auto-learn",
         "--memory-dir", str(memory_dir)],
        capture_output=True, text=True, env=env, cwd=str(project_dir),
    )

    assert result.returncode == 0
    assert "disabled" in result.stdout.lower()

    # learnings.jsonl should be unchanged
    with open(memory_dir / "learnings.jsonl") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) == 1  # only the original entry
