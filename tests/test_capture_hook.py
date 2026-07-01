"""Tests for Windsurf transcript parsing and --capture-hook CLI flag.

Pure-function tests for parse_transcript (AGENTS.md § Testing exception:
pure functions from capture module may be imported directly).
CLI integration tests use subprocess (matching test_capture_cli.py pattern).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

_SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")
if "PYTHONPATH" in os.environ:
    if _SRC_DIR not in os.environ["PYTHONPATH"].split(os.pathsep):
        os.environ["PYTHONPATH"] = _SRC_DIR + os.pathsep + os.environ["PYTHONPATH"]
else:
    os.environ["PYTHONPATH"] = _SRC_DIR

from mnemoq.engine.capture import parse_transcript


class TestParseTranscript:
    """Pure-function tests for parse_transcript()."""

    @pytest.mark.smoke
    def test_parse_transcript_basic(self, tmp_path):
        """JSONL with user_input + planner_response → formatted conversation text."""
        transcript = tmp_path / "transcript.jsonl"
        lines = [
            json.dumps({"type": "user_input", "user_input": {"user_response": "don't use sync writes"}}),
            json.dumps({"type": "planner_response", "planner_response": {"response": "Got it, switching to async."}}),
        ]
        transcript.write_text("\n".join(lines) + "\n")
        result = parse_transcript(str(transcript))
        assert "Human: don't use sync writes" in result
        assert "Agent: Got it, switching to async." in result

    def test_parse_transcript_last_exchange_only(self, tmp_path):
        """Multiple exchanges → only last one extracted."""
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            json.dumps({"type": "user_input", "user_input": {"user_response": "first prompt"}}) + "\n"
            + json.dumps({"type": "planner_response", "planner_response": {"response": "first response"}}) + "\n"
            + json.dumps({"type": "user_input", "user_input": {"user_response": "second prompt"}}) + "\n"
            + json.dumps({"type": "planner_response", "planner_response": {"response": "second response"}}) + "\n"
        )
        result = parse_transcript(str(transcript))
        assert "second prompt" in result
        assert "second response" in result
        assert "first prompt" not in result
        assert "first response" not in result

    def test_parse_transcript_unknown_types(self, tmp_path):
        """Unknown entry types skipped gracefully."""
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            json.dumps({"type": "user_input", "user_input": {"user_response": "hello"}}) + "\n"
            + json.dumps({"type": "some_unknown_type", "data": "ignored"}) + "\n"
            + json.dumps({"type": "planner_response", "planner_response": {"response": "hi there"}}) + "\n"
        )
        result = parse_transcript(str(transcript))
        assert "Human: hello" in result
        assert "Agent: hi there" in result
        assert "ignored" not in result

    def test_parse_transcript_empty_file(self, tmp_path):
        """Empty JSONL → empty string."""
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("")
        result = parse_transcript(str(transcript))
        assert result == ""

    def test_parse_transcript_malformed_lines(self, tmp_path):
        """Bad JSON lines skipped, valid ones processed."""
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            "this is not json\n"
            + json.dumps({"type": "user_input", "user_input": {"user_response": "valid prompt"}}) + "\n"
            + "{broken json\n"
            + json.dumps({"type": "planner_response", "planner_response": {"response": "valid response"}}) + "\n"
        )
        result = parse_transcript(str(transcript))
        assert "Human: valid prompt" in result
        assert "Agent: valid response" in result

    @pytest.mark.smoke
    def test_parse_transcript_missing_file(self):
        """Non-existent file → empty string."""
        result = parse_transcript("/nonexistent/path/to/transcript.jsonl")
        assert result == ""

    def test_parse_transcript_no_user_input(self, tmp_path):
        """Transcript with no user_input → empty string."""
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            json.dumps({"type": "planner_response", "planner_response": {"response": "no user prompt"}}) + "\n"
        )
        result = parse_transcript(str(transcript))
        assert result == ""

    def test_parse_transcript_code_action(self, tmp_path):
        """code_action entries mapped to Agent: [edited path]."""
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            json.dumps({"type": "user_input", "user_input": {"user_response": "fix the bug"}}) + "\n"
            + json.dumps({"type": "code_action", "code_action": {"path": "/src/main.py"}}) + "\n"
            + json.dumps({"type": "planner_response", "planner_response": {"response": "Fixed it."}}) + "\n"
        )
        result = parse_transcript(str(transcript))
        assert "Human: fix the bug" in result
        assert "Agent: [edited /src/main.py]" in result
        assert "Agent: Fixed it." in result


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


class TestCaptureHookCli:
    """--capture-hook CLI integration tests (subprocess pattern)."""

    @pytest.mark.smoke
    def test_capture_hook_e2e(self, temp_project):
        """Pipe stdin JSON with transcript_path → verify learnings.jsonl updated."""
        transcript = temp_project / "transcript.jsonl"
        lines = [
            json.dumps({"type": "user_input",
                       "user_input": {"user_response": "no, don't use sync writes in src/db.py"}}),
            json.dumps({"type": "planner_response",
                       "planner_response": {"response": "Understood, switching to async."}}),
        ]
        transcript.write_text("\n".join(lines) + "\n")
        hook_payload = json.dumps({
            "agent_action_name": "post_cascade_response_with_transcript",
            "tool_info": {"transcript_path": str(transcript)}
        })

        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.cli", "--capture-hook"],
            cwd=temp_project, input=hook_payload, capture_output=True, text=True
        )

        assert result.returncode == 0
        learnings_path = temp_project / "memory" / "learnings.jsonl"
        lines = [line for line in learnings_path.read_text().strip().split("\n") if line]
        assert len(lines) >= 1

    def test_capture_hook_missing_transcript(self, temp_project):
        """stdin JSON with non-existent path → exits 0, stderr logged."""
        hook_payload = json.dumps({
            "agent_action_name": "post_cascade_response_with_transcript",
            "tool_info": {"transcript_path": "/nonexistent/path.jsonl"}
        })

        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.cli", "--capture-hook"],
            cwd=temp_project, input=hook_payload, capture_output=True, text=True
        )

        assert result.returncode == 0
        assert "no conversation" in result.stderr.lower() or "error" in result.stderr.lower()

    @pytest.mark.smoke
    def test_capture_hook_invalid_json(self, temp_project):
        """Invalid stdin JSON → exits 0, stderr logged."""
        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.cli", "--capture-hook"],
            cwd=temp_project, input="not json at all", capture_output=True, text=True
        )

        assert result.returncode == 0
        assert "error" in result.stderr.lower()

    def test_capture_hook_no_transcript_path(self, temp_project):
        """stdin JSON missing transcript_path → exits 0, stderr logged."""
        hook_payload = json.dumps({
            "agent_action_name": "post_cascade_response_with_transcript",
            "tool_info": {}
        })

        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.cli", "--capture-hook"],
            cwd=temp_project, input=hook_payload, capture_output=True, text=True
        )

        assert result.returncode == 0
        assert "transcript_path" in result.stderr.lower()
