"""CLI integration tests for scripts/sim_dialogue.py.

Per AGENTS.md: scripts are tested via CLI subprocess, not direct imports.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "sim_dialogue.py"
REPO_ROOT = Path(__file__).parent.parent


def _run(args, cwd=REPO_ROOT):
    cmd = [sys.executable, str(SCRIPT)] + args
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd), timeout=60)


def test_dry_run_produces_signals():
    """Dry-run with enough turns should detect signals from non-none outcomes."""
    result = _run(["--turns", "20", "--direct", "--dry-run", "--seed", "42"])
    assert result.returncode == 0, result.stderr
    assert "Signals detected:" in result.stdout
    # With 20 turns and 80% non-none outcomes, we expect at least 1 signal
    signals_line = next((line for line in result.stdout.splitlines() if line.startswith("Signals detected:")), "")
    signals_count = int(signals_line.split(":")[1].strip())
    assert signals_count > 0, f"Expected signals > 0, got {signals_count}"


def test_dry_run_no_writes():
    """Dry-run must not create or modify learnings.jsonl."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        mem_dir = os.path.join(tmpdir, "memory")
        os.makedirs(mem_dir, exist_ok=True)
        learnings = os.path.join(mem_dir, "learnings.jsonl")
        # Write a sentinel line to verify it's not deleted
        with open(learnings, "w") as f:
            f.write('{"sentinel": true}\n')
        result = _run(["--turns", "5", "--direct", "--dry-run", "--seed", "42", "--memory-dir", mem_dir])
        assert result.returncode == 0, result.stderr
        # Sentinel should still be there (clean not applied in dry-run)
        with open(learnings) as f:
            assert f.read() == '{"sentinel": true}\n'


def test_none_outcome_zero_signals():
    """When all turns are 'none' outcome, signals_detected should be 0.

    We can't force all outcomes to 'none' via CLI, but we can verify that
    a single turn with a known seed produces 0 signals when the outcome
    happens to be 'none'. Instead, verify via transcript inspection.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        transcript = os.path.join(tmpdir, "transcript.jsonl")
        result = _run(["--turns", "50", "--direct", "--dry-run", "--seed", "42", "--transcript", transcript])
        assert result.returncode == 0, result.stderr
        assert os.path.exists(transcript)
        none_turns = []
        with open(transcript) as f:
            for line in f:
                record = json.loads(line)
                if record["summary"]["outcome"] == "none":
                    none_turns.append(record)
        # With 50 turns and 20% none weight, we expect ~10 none turns
        assert len(none_turns) > 0, "Expected at least some 'none' outcome turns"
        for turn in none_turns:
            assert turn["evaluate_result"]["signals_detected"] == 0, \
                f"None turn {turn['step']} had {turn['evaluate_result']['signals_detected']} signals"


def test_clean_requires_confirm():
    """--clean without --confirm should fail."""
    result = _run(["--turns", "5", "--direct", "--clean"])
    assert result.returncode != 0
    assert "--clean requires --confirm" in result.stderr


def test_pipeline_requires_confirm():
    """--pipeline without --confirm or --dry-run should fail."""
    result = _run(["--turns", "5", "--pipeline"])
    assert result.returncode != 0
    assert "--confirm" in result.stderr


def test_to_fakes_requires_direct():
    """--to-fakes with --pipeline should fail."""
    result = _run(["--turns", "5", "--pipeline", "--dry-run", "--to-fakes"])
    assert result.returncode != 0
    assert "--to-fakes requires --direct" in result.stderr


def test_mutual_exclusion_direct_pipeline():
    """--direct and --pipeline cannot be combined."""
    result = _run(["--turns", "5", "--direct", "--pipeline", "--dry-run"])
    assert result.returncode != 0


def test_seed_reproducibility():
    """Same seed should produce identical output."""
    r1 = _run(["--turns", "10", "--direct", "--dry-run", "--seed", "99"])
    r2 = _run(["--turns", "10", "--direct", "--dry-run", "--seed", "99"])
    assert r1.returncode == 0
    assert r1.stdout == r2.stdout, "Same seed should produce identical output"


def test_transcript_format():
    """Transcript should be valid JSONL with expected fields."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        transcript = os.path.join(tmpdir, "transcript.jsonl")
        result = _run(["--turns", "3", "--direct", "--dry-run", "--seed", "42", "--transcript", transcript])
        assert result.returncode == 0, result.stderr
        assert os.path.exists(transcript)
        with open(transcript) as f:
            lines = f.readlines()
        assert len(lines) == 3
        for line in lines:
            record = json.loads(line)
            assert "step" in record
            assert "domain" in record
            assert "human" in record
            assert "agent" in record
            assert "summary" in record
            assert "evaluate_result" in record
            assert record["summary"]["prompt_type"] == "human"


def test_invalid_domain():
    """Invalid domain should fail."""
    result = _run(["--turns", "5", "--direct", "--dry-run", "--domain", "nonexistent"])
    assert result.returncode != 0
    assert "--domain" in result.stderr


def test_no_mode_specified():
    """Neither --direct nor --pipeline should fail."""
    result = _run(["--turns", "5", "--dry-run"])
    assert result.returncode != 0
    assert "--direct" in result.stderr or "--pipeline" in result.stderr
