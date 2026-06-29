"""CLI integration tests for capture_core and --capture-file.

Per AGENTS.md: engine modules are tested via CLI subprocess, not direct imports.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ponytail: surface src/ to subprocesses so `python -m agent_memory.cli` resolves without a pip install.
_SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")
if "PYTHONPATH" in os.environ:
    if _SRC_DIR not in os.environ["PYTHONPATH"].split(os.pathsep):
        os.environ["PYTHONPATH"] = _SRC_DIR + os.pathsep + os.environ["PYTHONPATH"]
else:
    os.environ["PYTHONPATH"] = _SRC_DIR


@pytest.fixture
def temp_project():
    """Create a temporary project with memory directory and required files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        memory_dir = project_dir / "memory"
        memory_dir.mkdir()
        (memory_dir / "learnings.jsonl").touch()
        (memory_dir / "quarantine.jsonl").touch()
        (memory_dir / "archive").mkdir()
        yield project_dir


class TestCaptureFileCli:
    """--capture-file CLI flag tests."""

    def test_capture_file_heuristic(self, temp_project):
        """--capture-file processes conversation and logs memories."""
        conv_file = temp_project / "conversation.txt"
        conv_file.write_text(
            "Human: no, don't use sync writes in src/db.py, use async instead\n"
            "AI: understood, switching to async writes"
        )

        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.cli", "--capture-file", str(conv_file)],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0
        assert "CAPTURE" in result.stdout
        assert "heuristic" in result.stdout

        # Verify entries were logged
        learnings_path = temp_project / "memory" / "learnings.jsonl"
        lines = [line for line in learnings_path.read_text().strip().split("\n") if line]
        assert len(lines) >= 1

    def test_capture_file_disabled(self, temp_project):
        """--capture-file respects capture_enabled: false."""
        memory_dir = temp_project / "memory"
        (memory_dir / "config.json").write_text(json.dumps({
            "tuning": {"capture_enabled": False}
        }))

        conv_file = temp_project / "conversation.txt"
        conv_file.write_text("Human: test conversation")

        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.cli", "--capture-file", str(conv_file)],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0
        assert "disabled" in result.stdout.lower()

    def test_capture_file_max_summaries(self, temp_project):
        """--capture-file respects capture_max_summaries."""
        memory_dir = temp_project / "memory"
        (memory_dir / "config.json").write_text(json.dumps({
            "tuning": {"capture_max_summaries": 2}
        }))

        # Create a conversation with many turns
        turns = "\n".join(f"Human: turn {i} about something" for i in range(10))
        conv_file = temp_project / "conversation.txt"
        conv_file.write_text(turns)

        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.cli", "--capture-file", str(conv_file)],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0
        assert "Summaries: 2" in result.stdout

    def test_capture_file_not_found(self, temp_project):
        """--capture-file errors on missing file."""
        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.cli", "--capture-file", "nonexistent.txt"],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 1
        assert "Cannot read" in result.stderr

    def test_capture_file_mutual_exclusion(self, temp_project):
        """--capture-file cannot be combined with --stats."""
        conv_file = temp_project / "conversation.txt"
        conv_file.write_text("test")

        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.cli", "--capture-file", str(conv_file), "--stats"],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode != 0

    def test_capture_file_empty_conversation(self, temp_project):
        """--capture-file handles empty conversation gracefully."""
        conv_file = temp_project / "conversation.txt"
        conv_file.write_text("")

        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.cli", "--capture-file", str(conv_file)],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0
        assert "CAPTURE" in result.stdout

    def test_capture_file_logs_correction(self, temp_project):
        """--capture-file logs a correction signal from conversation text."""
        conv_file = temp_project / "conversation.txt"
        conv_file.write_text(
            "Human: no, don't use sync writes in src/db.py, use async instead"
        )

        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.cli", "--capture-file", str(conv_file)],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0

        learnings_path = temp_project / "memory" / "learnings.jsonl"
        lines = [line for line in learnings_path.read_text().strip().split("\n") if line]
        assert len(lines) >= 1

        # At least one entry should reference async or sync
        entries = [json.loads(line) for line in lines]
        actions = " ".join(e.get("action", "") for e in entries)
        assert "async" in actions.lower() or "sync" in actions.lower()

    def test_capture_file_config_mode(self, temp_project):
        """--capture-file with capture_mode: online falls back to heuristic."""
        memory_dir = temp_project / "memory"
        (memory_dir / "config.json").write_text(json.dumps({
            "capture_mode": "online",
            "capture_online_endpoint": None,
            "capture_online_model": None,
        }))

        conv_file = temp_project / "conversation.txt"
        conv_file.write_text("Human: test conversation about src/app.py")

        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.cli", "--capture-file", str(conv_file)],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0
        # With no online endpoint configured, should fall back to heuristic
        assert "heuristic" in result.stdout


class TestCaptureConfigValidation:
    """Config validation tests for capture keys."""

    def test_invalid_capture_mode(self, temp_project):
        """Invalid capture_mode raises ValueError."""
        memory_dir = temp_project / "memory"
        (memory_dir / "config.json").write_text(json.dumps({
            "capture_mode": "invalid_mode"
        }))

        conv_file = temp_project / "conversation.txt"
        conv_file.write_text("test")

        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.cli", "--capture-file", str(conv_file)],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 1
        assert "capture_mode" in result.stderr

    def test_invalid_capture_max_summaries(self, temp_project):
        """Non-integer capture_max_summaries raises TypeError."""
        memory_dir = temp_project / "memory"
        (memory_dir / "config.json").write_text(json.dumps({
            "tuning": {"capture_max_summaries": "not_a_number"}
        }))

        conv_file = temp_project / "conversation.txt"
        conv_file.write_text("test")

        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.cli", "--capture-file", str(conv_file)],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 1
        assert "capture_max_summaries" in result.stderr

    def test_capture_enabled_false_skips_logging(self, temp_project):
        """capture_enabled: false produces no entries."""
        memory_dir = temp_project / "memory"
        (memory_dir / "config.json").write_text(json.dumps({
            "tuning": {"capture_enabled": False}
        }))

        conv_file = temp_project / "conversation.txt"
        conv_file.write_text("Human: test")

        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.cli", "--capture-file", str(conv_file)],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0

        learnings_path = temp_project / "memory" / "learnings.jsonl"
        lines = [line for line in learnings_path.read_text().strip().split("\n") if line]
        assert len(lines) == 0
