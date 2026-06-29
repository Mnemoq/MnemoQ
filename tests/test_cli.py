"""Tests for CLI output: version stderr, retrieval stdout stability, resolver."""
import json
import subprocess
import sys

import pytest


class TestVersionStderr:
    """Test --version outputs to stderr."""

    def test_version_outputs_to_stderr(self):
        """Test that --version outputs to stderr, not stdout."""
        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.cli", "--version"],
            capture_output=True, text=True
        )

        assert result.returncode == 0
        assert "agent-memory-engine" in result.stderr
        assert result.stdout == ""

    def test_version_format(self):
        """Test that --version has correct format."""
        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.cli", "--version"],
            capture_output=True, text=True
        )

        assert result.returncode == 0
        assert "v" in result.stderr
        assert "." in result.stderr


class TestRetrievalStdoutStability:
    """Test that retrieval stdout is stable."""

    def test_retrieval_stdout_stable(self, temp_project):
        """Test that retrieval output is stable across runs."""
        learning = {
            "step": 1,
            "source_agent": "gm",
            "type": "bug_fix",
            "domain": "tooling",
            "components": ["TestComponent"],
            "files_touched": ["test.py"],
            "trigger": "When testing stability",
            "action": "ALWAYS test stability",
            "reason": "Stability should be maintained",
            "importance": 7,
            "severity": "major"
        }

        learning_file = temp_project / "learning.json"
        learning_file.write_text(json.dumps(learning))

        subprocess.run(
            [sys.executable, "-m", "agent_memory.cli", "--log-file", str(learning_file)],
            cwd=temp_project, capture_output=True, text=True
        )

        result1 = subprocess.run(
            [sys.executable, "-m", "agent_memory.cli", "--step", "1", "--components", "TestComponent"],
            cwd=temp_project, capture_output=True, text=True
        )

        result2 = subprocess.run(
            [sys.executable, "-m", "agent_memory.cli", "--step", "1", "--components", "TestComponent"],
            cwd=temp_project, capture_output=True, text=True
        )

        assert "When testing stability" in result1.stdout
        assert "When testing stability" in result2.stdout
        assert "WARNINGS" in result1.stdout
        assert "WARNINGS" in result2.stdout

    def test_retrieval_no_version_in_stdout(self, temp_project):
        """Test that version info is not in retrieval stdout."""
        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.cli", "--step", "1", "--domain", "tooling"],
            cwd=temp_project, capture_output=True, text=True
        )

        assert "v1." not in result.stdout
        assert "agent-memory-engine" not in result.stdout


class TestResolver:
    """Test resolve_memory_dir() and _get_paths() guard."""

    def test_resolve_memory_dir_priority(self, monkeypatch, tmp_path):
        """Test resolve_memory_dir() honors priority: --memory-dir > env > cwd/memory."""
        from agent_memory.cli import resolve_memory_dir

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        result = resolve_memory_dir(str(memory_dir))
        assert result == str(memory_dir.resolve())

        env_dir = tmp_path / "env_memory"
        env_dir.mkdir()
        monkeypatch.setenv("AGENT_MEMORY_DIR", str(env_dir))
        result = resolve_memory_dir(None)
        assert result == str(env_dir.resolve())

        monkeypatch.delenv("AGENT_MEMORY_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        result = resolve_memory_dir(None)
        assert result == str(memory_dir.resolve())

    def test_resolve_memory_dir_errors(self, monkeypatch, tmp_path):
        """Test resolve_memory_dir() raises ValueError on invalid paths."""
        from agent_memory.cli import resolve_memory_dir

        with pytest.raises(ValueError, match="--memory-dir path does not exist"):
            resolve_memory_dir(str(tmp_path / "nonexistent"))

        with pytest.raises(ValueError, match="--memory-dir path does not exist"):
            resolve_memory_dir("")

        monkeypatch.delenv("AGENT_MEMORY_DIR", raising=False)
        monkeypatch.setenv("AGENT_MEMORY_DIR", str(tmp_path / "nonexistent"))
        with pytest.raises(ValueError, match="AGENT_MEMORY_DIR path does not exist"):
            resolve_memory_dir(None)

        monkeypatch.delenv("AGENT_MEMORY_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="No memory directory found"):
            resolve_memory_dir(None)

    def test_get_paths_raises_if_uninitialized(self):
        """Test _get_paths() raises RuntimeError if PATHS is None."""
        from agent_memory import cli as filter
        old_paths = filter.PATHS
        try:
            filter.PATHS = None
            with pytest.raises(RuntimeError, match="PATHS not initialized"):
                filter._get_paths()
        finally:
            filter.PATHS = old_paths
