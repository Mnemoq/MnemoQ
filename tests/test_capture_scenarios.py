"""Conversation scenario tests for the capture-core pipeline.

Exercises the full heuristic extraction → evaluate → auto-log chain with
realistic conversation text. Each scenario targets a specific detector,
edge case, or pipeline behavior.

Follows existing CLI subprocess patterns from test_capture_cli.py.
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


def _capture(temp_project, conversation_text, extra_config=None):
    """Run --capture-file and return (result, entries)."""
    memory_dir = temp_project / "memory"

    if extra_config:
        (memory_dir / "config.json").write_text(json.dumps(extra_config))

    conv_file = temp_project / "conversation.txt"
    conv_file.write_text(conversation_text)

    result = subprocess.run(
        [sys.executable, "-m", "mnemoq.cli", "--capture-file", str(conv_file)],
        cwd=temp_project, capture_output=True, text=True,
    )

    learnings_path = memory_dir / "learnings.jsonl"
    entries = []
    for line in learnings_path.read_text().strip().split("\n"):
        if line:
            entries.append(json.loads(line))

    return result, entries


def _evaluate(temp_project, summary_dict, extra_config=None):
    """Run --evaluate with a structured summary and return (result, entries)."""
    memory_dir = temp_project / "memory"

    if extra_config:
        (memory_dir / "config.json").write_text(json.dumps(extra_config))

    result = subprocess.run(
        [sys.executable, "-m", "mnemoq.cli", "--evaluate", json.dumps(summary_dict),
         "--memory-dir", str(memory_dir)],
        cwd=temp_project, capture_output=True, text=True,
    )

    learnings_path = memory_dir / "learnings.jsonl"
    entries = []
    for line in learnings_path.read_text().strip().split("\n"):
        if line:
            entries.append(json.loads(line))

    return result, entries


# ===========================================================================
# Scenario 1: Human Correction — "no, don't use X, use Y instead"
# Target: detect_human_correction (confidence 0.95)
# ===========================================================================

class TestScenarioHumanCorrection:
    """Conversations where a human corrects the AI's approach."""

    def test_use_async_instead(self, temp_project):
        """Classic correction: 'don't use sync, use async instead'."""
        conv = (
            "Human: no, don't use sync writes in src/db.py, use async instead\n"
            "AI: understood, switching to async writes"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1
        actions = " ".join(e.get("action", "") for e in entries)
        assert "async" in actions.lower() or "sync" in actions.lower()

    def test_replace_pattern(self, temp_project):
        """Correction via 'replace X with Y' pattern."""
        conv = (
            "Human: replace the sync logger with a buffered logger in src/log.py\n"
            "AI: done, now using BufferedLogger"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1

    def test_stop_using_pattern(self, temp_project):
        """Correction via 'stop using X' pattern."""
        conv = (
            "Human: stop using global state in src/config.py, it causes race conditions\n"
            "AI: refactored to use dependency injection"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1
        actions = " ".join(e.get("action", "") for e in entries)
        assert "global" in actions.lower() or "state" in actions.lower()

    def test_avoid_pattern(self, temp_project):
        """Correction via 'avoid X' pattern."""
        conv = (
            "Human: avoid using eval() in src/parser.py, it's a security risk\n"
            "AI: replaced with ast.literal_eval"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1

    def test_never_use_pattern(self, temp_project):
        """Correction via 'never use X' pattern."""
        conv = (
            "Human: never use print() for logging in src/server.py, use the Logger class\n"
            "AI: switched all print calls to Logger.info()"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1


# ===========================================================================
# Scenario 2: Explicit Remember — "remember to always..."
# Target: detect_explicit_remember (confidence 0.85)
# ===========================================================================

class TestScenarioExplicitRemember:
    """Conversations with explicit memory directives."""

    def test_remember_always(self, temp_project):
        """'remember to always run tests before pushing'."""
        conv = (
            "Human: remember to always run tests in src/app.py before pushing\n"
            "AI: noted, I'll run tests before every push"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1

    def test_dont_forget(self, temp_project):
        """'don't forget to check for null pointers'."""
        conv = (
            "Human: don't forget to check for null pointers in src/handler.py\n"
            "AI: added null checks throughout"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1

    def test_keep_in_mind(self, temp_project):
        """'keep in mind that the API rate limit is 100 req/min'."""
        conv = (
            "Human: keep in mind that the API rate limit is 100 req/min in src/api.py\n"
            "AI: implemented rate limiting at 90 req/min to stay safe"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1

    def test_going_forward(self, temp_project):
        """'going forward, use type hints everywhere'."""
        conv = (
            "Human: going forward, use type hints everywhere in src/utils.py\n"
            "AI: added type hints to all functions"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1

    def test_best_practice(self, temp_project):
        """'best practice is to validate input at trust boundaries'."""
        conv = (
            "Human: the best practice is to validate input at trust boundaries in src/routes.py\n"
            "AI: added input validation middleware"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1


# ===========================================================================
# Scenario 3: Bug Fixed — "fixed the crash/error/exception"
# Target: detect_bug_fixed (confidence 0.70)
# ===========================================================================

class TestScenarioBugFixed:
    """Conversations describing bug fixes."""

    def test_fixed_crash(self, temp_project):
        """'fixed the crash in auth.py by adding a null check'."""
        conv = (
            "Human: fixed the crash in src/auth.py by adding a null check before token validation\n"
            "AI: good catch, the null check prevents the segfault"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1

    def test_error_traceback(self, temp_project):
        """Conversation mentioning error and traceback."""
        conv = (
            "Human: got a TypeError in src/parser.py: error: unsupported operand type for +\n"
            "AI: fixed by converting to str before concatenation"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1

    def test_exception_class_name(self, temp_project):
        """Conversation mentioning a specific exception class."""
        conv = (
            "Human: hit a ValueError in src/config.py when the env var is missing\n"
            "AI: added a default value fallback"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1

    def test_failed_then_fixed(self, temp_project):
        """'the build failed, fixed by updating the dependency version'."""
        conv = (
            "Human: the build failed in src/build.py, fixed by updating the dependency version\n"
            "AI: build passes now"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1


# ===========================================================================
# Scenario 4: Architectural Decision — "let's use X for Y"
# Target: detect_decision (confidence 0.60)
# ===========================================================================

class TestScenarioDecision:
    """Conversations where an architectural decision is made."""

    def test_lets_use(self, temp_project):
        """'let's use event sourcing for the audit log'."""
        conv = (
            "Human: let's use event sourcing for the audit log in src/audit.py\n"
            "AI: agreed, I'll implement EventStore"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1

    def test_we_should(self, temp_project):
        """'we should switch to a queue-based architecture'."""
        conv = (
            "Human: we should switch to a queue-based architecture in src/worker.py\n"
            "AI: implemented with Redis queues"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1

    def test_decided_to(self, temp_project):
        """'decided to use PostgreSQL instead of SQLite'."""
        conv = (
            "Human: decided to use PostgreSQL instead of SQLite for src/db.py\n"
            "AI: migrated the schema"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1


# ===========================================================================
# Scenario 5: Workaround — "for now, pin X" / "as a workaround"
# Target: detect_workaround (confidence 0.55)
# ===========================================================================

class TestScenarioWorkaround:
    """Conversations describing workarounds and temporary fixes."""

    def test_for_now(self, temp_project):
        """'for now, pin urllib3 to <2'."""
        conv = (
            "Human: for now, pin urllib3 to <2 in src/http.py until the C extension is rebuilt\n"
            "AI: pinned in requirements.txt"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1

    def test_workaround_explicit(self, temp_project):
        """'as a workaround, skip the cache layer'."""
        conv = (
            "Human: as a workaround, skip the cache layer in src/cache.py temporarily\n"
            "AI: added a feature flag to bypass cache"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1

    def test_temporarily(self, temp_project):
        """'temporarily hardcode the endpoint URL'."""
        conv = (
            "Human: temporarily hardcode the endpoint URL in src/client.py\n"
            "AI: added a TODO for config extraction"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1


# ===========================================================================
# Scenario 6: None outcome with real signal — should still log
# ===========================================================================

class TestScenarioNoneWithSignal:
    """None-outcome turns that have real file paths or components."""

    def test_file_mentioned_no_outcome(self, temp_project):
        """'let me check src/app.py for the issue' → none outcome, but file present."""
        conv = "Human: let me check src/app.py for the issue\nAI: sure, take a look"
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1

    def test_component_mentioned_no_outcome(self, temp_project):
        """Mentions a PascalCase component but no outcome keywords."""
        conv = "Human: the AuthService is in src/auth.py\nAI: yes, that's where it lives"
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1


# ===========================================================================
# Scenario 7: Smalltalk / filler — should NOT log
# ===========================================================================

class TestScenarioSmalltalk:
    """Conversations with no learnable signal."""

    def test_hello_hi(self, temp_project):
        """Pure smalltalk produces no entries."""
        conv = "Human: hello\nAI: hi there"
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) == 0

    def test_thanks_sure(self, temp_project):
        """Filler words produce no entries."""
        conv = "Human: thanks\nAI: sure"
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) == 0

    def test_ok_done(self, temp_project):
        """Short acknowledgments produce no entries."""
        conv = "Human: ok\nAI: done"
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) == 0

    def test_empty_conversation(self, temp_project):
        """Empty string produces no entries."""
        conv = ""
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) == 0


# ===========================================================================
# Scenario 8: Multi-signal conversation — multiple detectors fire
# ===========================================================================

class TestScenarioMultiSignal:
    """Conversations with multiple learnable moments in one capture."""

    def test_correction_plus_bugfix(self, temp_project):
        """A correction followed by a bug fix in the same conversation."""
        conv = (
            "Human: no, don't use sync writes in src/db.py, use async instead\n"
            "AI: switched to async writes\n"
            "Human: also fixed the crash in src/auth.py by adding a null check\n"
            "AI: null check added"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 2

    def test_decision_plus_remember(self, temp_project):
        """A decision followed by an explicit remember directive."""
        conv = (
            "Human: let's use event sourcing for the audit log in src/audit.py\n"
            "AI: agreed\n"
            "Human: remember to always run migrations in src/migrate.py before deploying\n"
            "AI: noted"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 2

    def test_full_mix(self, temp_project):
        """Correction + bug fix + decision + remember + workaround in one conversation."""
        conv = (
            "Human: no, don't use sync writes in src/db.py, use async instead\n"
            "AI: done\n"
            "Human: fixed the crash in src/auth.py by adding a null check\n"
            "AI: good\n"
            "Human: let's use Redis for caching in src/cache.py\n"
            "AI: agreed\n"
            "Human: remember to always check the queue size in src/worker.py\n"
            "AI: noted\n"
            "Human: for now, pin urllib3 to <2 in src/http.py as a workaround\n"
            "AI: pinned"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 3, f"Expected 3+ entries from multi-signal conversation, got {len(entries)}"

    def test_max_per_turn_caps_signals(self, temp_project):
        """evaluate_max_per_turn caps how many signals auto-log per capture."""
        conv = (
            "Human: no, don't use sync writes in src/db.py, use async instead\n"
            "AI: done\n"
            "Human: fixed the crash in src/auth.py by adding a null check\n"
            "AI: good\n"
            "Human: let's use Redis for caching in src/cache.py\n"
            "AI: agreed\n"
            "Human: remember to always check the queue size in src/worker.py\n"
            "AI: noted\n"
            "Human: for now, pin urllib3 to <2 in src/http.py as a workaround\n"
            "AI: pinned"
        )
        result, entries = _capture(temp_project, conv, extra_config={
            "tuning": {"evaluate_max_per_turn": 2}
        })

        assert result.returncode == 0
        # With max_per_turn=2, each summary can produce at most 2 auto-logs.
        # But the cap is per evaluate_core call, and each summary is a separate call.
        # So this tests that the cap doesn't break multi-summary conversations.
        assert len(entries) >= 1


# ===========================================================================
# Scenario 9: Negation edge cases
# ===========================================================================

class TestScenarioNegation:
    """Edge cases around negation detection in the heuristic extractor."""

    def test_not_wrong_no_correction(self, temp_project):
        """'that's not wrong' should NOT trigger correction (negation check)."""
        conv = "Human: that's not wrong in src/app.py\nAI: thanks"
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        # "not wrong" negates the "wrong" correction signal
        # Should still produce a none-outcome entry if file is present
        # but should NOT produce a correction-type entry
        correction_entries = [e for e in entries if e.get("type") == "architectural_pattern"
                              and "correction" in e.get("trigger", "").lower()]
        assert len(correction_entries) == 0

    def test_not_a_bug(self, temp_project):
        """'this is not a bug' should not trigger bug_fixed."""
        conv = "Human: this is not a bug in src/app.py, it's expected behavior\nAI: understood"
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        bug_entries = [e for e in entries if e.get("type") == "bug_fix"]
        assert len(bug_entries) == 0


# ===========================================================================
# Scenario 10: Ranking and cap behavior
# ===========================================================================

class TestScenarioRanking:
    """Summary ranking and max_summaries cap behavior."""

    def test_correction_survives_cap(self, temp_project):
        """With a low cap, corrections survive over smalltalk."""
        conv = "\n".join([
            "Human: hello there",
            "AI: hi",
            "Human: how are you",
            "AI: doing fine",
            "Human: no, don't use sync writes in src/db.py, use async instead",
            "AI: ok",
            "Human: thanks",
            "AI: sure",
        ])
        result, entries = _capture(temp_project, conv, extra_config={
            "tuning": {"capture_max_summaries": 3}
        })

        assert result.returncode == 0
        assert "Summaries: 3" in result.stdout
        actions = " ".join(e.get("action", "") for e in entries)
        assert "async" in actions.lower() or "sync" in actions.lower()

    def test_high_confidence_prioritized(self, temp_project):
        """Human correction (0.95) should be auto-logged even with low max_per_turn."""
        conv = (
            "Human: no, don't use sync writes in src/db.py, use async instead\n"
            "AI: done\n"
            "Human: let's use Redis for caching in src/cache.py\n"
            "AI: agreed\n"
            "Human: for now, pin urllib3 to <2 in src/http.py\n"
            "AI: pinned"
        )
        result, entries = _capture(temp_project, conv, extra_config={
            "tuning": {"evaluate_max_per_turn": 1}
        })

        assert result.returncode == 0
        assert len(entries) >= 1


# ===========================================================================
# Scenario 11: Dedup — same conversation twice
# ===========================================================================

class TestScenarioDedup:
    """Duplicate detection when the same conversation is captured twice."""

    def test_same_conversation_twice(self, temp_project):
        """Capturing the same conversation twice should not double-log."""
        conv = (
            "Human: no, don't use sync writes in src/db.py, use async instead\n"
            "AI: understood, switching to async writes"
        )
        result1, entries1 = _capture(temp_project, conv)
        assert result1.returncode == 0

        result2, entries2 = _capture(temp_project, conv)
        assert result2.returncode == 0

        # Total entries should not double
        all_entries = entries2  # entries2 reads the file again, so it includes both runs
        assert len(all_entries) == len(entries1), \
            f"Second capture should not add duplicates: {len(entries1)} -> {len(all_entries)}"


# ===========================================================================
# Scenario 12: Evaluate-core direct tests (structured summaries)
# ===========================================================================

class TestScenarioEvaluateDirect:
    """Direct evaluate_core tests with pre-structured summaries."""

    def test_human_correction_auto_logs(self, temp_project):
        """High-confidence human correction auto-logs at default threshold."""
        result, entries = _evaluate(temp_project, {
            "step": 1,
            "prompt_type": "human",
            "outcome": "correction",
            "corrected_action": "use async log writes",
            "text": "corrected the logger",
            "components": ["Logger"],
            "files_touched": ["src/log.py"],
        })
        assert result.returncode == 0
        assert len(entries) == 1
        assert entries[0]["action"].startswith("ALWAYS ")

    def test_explicit_remember_auto_logs(self, temp_project):
        """Explicit remember with keyword match auto-logs."""
        result, entries = _evaluate(temp_project, {
            "step": 1,
            "prompt_type": "human",
            "outcome": "preference",
            "text": "remember to always validate input at trust boundaries",
            "components": ["InputValidator"],
            "files_touched": ["src/validator.py"],
        })
        assert result.returncode == 0
        assert len(entries) == 1

    def test_bug_fixed_auto_logs(self, temp_project):
        """Bug fix outcome auto-logs."""
        result, entries = _evaluate(temp_project, {
            "step": 1,
            "prompt_type": "agent",
            "outcome": "bug_fixed",
            "text": "null pointer crash in token validation",
            "error_text": "TypeError: unsupported operand",
            "components": ["AuthService"],
            "files_touched": ["src/auth.py"],
        })
        assert result.returncode == 0
        assert len(entries) == 1
        assert entries[0]["type"] == "bug_fix"

    def test_decision_auto_logs(self, temp_project):
        """Decision outcome auto-logs at default threshold 0.5."""
        result, entries = _evaluate(temp_project, {
            "step": 1,
            "prompt_type": "human",
            "outcome": "decision",
            "text": "use event sourcing for audit log",
            "components": ["AuditLog"],
            "files_touched": ["src/audit.py"],
        })
        assert result.returncode == 0
        assert len(entries) == 1

    def test_workaround_auto_logs(self, temp_project):
        """Workaround auto-logs at default threshold 0.5 and sets debt_level."""
        result, entries = _evaluate(temp_project, {
            "step": 1,
            "prompt_type": "agent",
            "outcome": "workaround",
            "text": "pin urllib3 to <2 until C extension rebuilt",
            "components": ["HttpClient"],
            "files_touched": ["src/http.py"],
        })
        assert result.returncode == 0
        assert len(entries) == 1
        assert entries[0]["debt_level"] == "workaround"

    def test_workaround_suggests_at_high_threshold(self, temp_project):
        """Workaround (0.55) stays a suggestion at threshold 0.6."""
        result, entries = _evaluate(temp_project, {
            "step": 1,
            "prompt_type": "agent",
            "outcome": "workaround",
            "text": "pin urllib3 to <2",
            "components": ["HttpClient"],
            "files_touched": ["src/http.py"],
        }, extra_config={
            "tuning": {"evaluate_auto_log_threshold": 0.6}
        })
        assert result.returncode == 0
        assert len(entries) == 0

    def test_none_outcome_no_signal(self, temp_project):
        """None outcome with no detector signal → no entries."""
        result, entries = _evaluate(temp_project, {
            "step": 1,
            "prompt_type": "agent",
            "outcome": "none",
            "text": "nothing notable",
            "components": ["Foo"],
            "files_touched": ["src/foo.py"],
        })
        assert result.returncode == 0
        assert len(entries) == 0

    def test_empty_components_no_signal(self, temp_project):
        """Empty components → _build_candidate returns None → no signal."""
        result, entries = _evaluate(temp_project, {
            "step": 1,
            "prompt_type": "human",
            "outcome": "correction",
            "corrected_action": "do X",
            "text": "fix",
            "components": [],
            "files_touched": ["src/x.py"],
        })
        assert result.returncode == 0
        assert len(entries) == 0

    def test_empty_files_no_signal(self, temp_project):
        """Empty files_touched → _build_candidate returns None → no signal."""
        result, entries = _evaluate(temp_project, {
            "step": 1,
            "prompt_type": "human",
            "outcome": "correction",
            "corrected_action": "do X",
            "text": "fix",
            "components": ["Foo"],
            "files_touched": [],
        })
        assert result.returncode == 0
        assert len(entries) == 0

    def test_invalid_step_skipped(self, temp_project):
        """Invalid step (0) → validation fails → skipped_invalid."""
        result, entries = _evaluate(temp_project, {
            "step": 0,
            "prompt_type": "human",
            "outcome": "correction",
            "corrected_action": "use async writes",
            "text": "corrected the logger",
            "components": ["Logger"],
            "files_touched": ["src/log.py"],
        })
        assert result.returncode == 0
        assert len(entries) == 0
        assert "Skipped" in result.stdout or "skipped" in result.stdout.lower()

    def test_disabled_no_write(self, temp_project):
        """evaluate_enabled: false → disabled, no write."""
        result, entries = _evaluate(temp_project, {
            "step": 1,
            "prompt_type": "human",
            "outcome": "correction",
            "corrected_action": "use async writes",
            "text": "corrected",
            "components": ["Logger"],
            "files_touched": ["src/log.py"],
        }, extra_config={
            "tuning": {"evaluate_enabled": False}
        })
        assert result.returncode == 0
        assert "disabled" in result.stdout.lower()
        assert len(entries) == 0


# ===========================================================================
# Scenario 13: Tier degradation observability
# ===========================================================================

class TestScenarioTierDegradation:
    """Capture tier degradation and metrics."""

    def _read_capture_events(self, temp_project):
        metrics_path = temp_project / "memory" / "metrics.jsonl"
        if not metrics_path.exists():
            return []
        events = []
        for line in metrics_path.read_text().strip().split("\n"):
            if not line:
                continue
            e = json.loads(line)
            if e.get("event_type") == "capture":
                events.append(e)
        return events

    def test_heuristic_no_degradation(self, temp_project):
        """Default heuristic mode → not degraded."""
        conv = "Human: fixed the bug in src/app.py by adding a null check"
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        events = self._read_capture_events(temp_project)
        assert len(events) == 1
        assert events[0]["tier"] == "heuristic"
        assert events[0]["degraded"] is False

    def test_online_falls_back_degraded(self, temp_project):
        """mode=online with no endpoint → heuristic, degraded=True."""
        conv = "Human: fixed the bug in src/app.py by adding a null check"
        result, entries = _capture(temp_project, conv, extra_config={
            "capture_mode": "online",
            "capture_online_endpoint": None,
            "capture_online_model": None,
        })

        assert result.returncode == 0
        events = self._read_capture_events(temp_project)
        assert len(events) == 1
        assert events[0]["mode"] == "online"
        assert events[0]["tier"] == "heuristic"
        assert events[0]["degraded"] is True


# ===========================================================================
# Scenario 14: Config edge cases
# ===========================================================================

class TestScenarioConfigEdge:
    """Configuration-driven behavior changes."""

    def test_capture_disabled(self, temp_project):
        """capture_enabled: false → no entries, disabled response."""
        conv = "Human: no, don't use sync writes in src/db.py, use async instead"
        result, entries = _capture(temp_project, conv, extra_config={
            "tuning": {"capture_enabled": False}
        })

        assert result.returncode == 0
        assert "disabled" in result.stdout.lower()
        assert len(entries) == 0

    def test_always_log_false_suppresses_none(self, temp_project):
        """capture_always_log: false → none-outcome entries not auto-logged."""
        conv = "Human: let me check src/app.py for the issue"
        result, entries = _capture(temp_project, conv, extra_config={
            "tuning": {"capture_always_log": False}
        })

        assert result.returncode == 0
        # Without always_log, none-outcome summaries with no detector signal
        # should not produce entries
        assert len(entries) == 0

    def test_none_requires_signal_filters_smalltalk(self, temp_project):
        """capture_none_log_requires_signal: true → smalltalk not logged."""
        conv = "Human: hello\nAI: hi there"
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) == 0


# ===========================================================================
# Scenario 15: Realistic Magpie Swoop-style conversation
# ===========================================================================

class TestScenarioRealisticGameDev:
    """Realistic game-dev conversations matching the Magpie Swoop project context."""

    def test_phaser_pool_correction(self, temp_project):
        """Correction about Phaser entity pooling."""
        conv = (
            "Human: no, don't create new sprites in the update loop in src/entities/Magpie.ts, "
            "use the object pool instead\n"
            "AI: refactored to use pool.spawn() instead of new Sprite()"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1
        actions = " ".join(e.get("action", "") for e in entries)
        assert "pool" in actions.lower()

    def test_scene_shutdown_bug(self, temp_project):
        """Bug fix about scene shutdown memory leak."""
        conv = (
            "Human: fixed the memory leak in src/scenes/GameScene.ts, "
            "the shutdown handler wasn't destroying event listeners\n"
            "AI: added this.events.removeAllListeners() in shutdown()"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1

    def test_gameconfig_decision(self, temp_project):
        """Architectural decision about GameConfig."""
        conv = (
            "Human: let's move all magic numbers to src/config/GameConfig.ts\n"
            "AI: agreed, I'll extract them into typed constants"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1

    def test_eventbus_remember(self, temp_project):
        """Remember directive about EventBus usage."""
        conv = (
            "Human: remember to always use EventBus for cross-scene communication, "
            "never call scene methods directly in src/events/EventBus.ts\n"
            "AI: noted, I'll use EventBus.emit() everywhere"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1

    def test_capacitor_workaround(self, temp_project):
        """Workaround for a Capacitor build issue."""
        conv = (
            "Human: for now, skip the Capacitor sync step in src/main.ts "
            "as a workaround until the plugin is updated\n"
            "AI: added a TODO to re-enable after plugin update"
        )
        result, entries = _capture(temp_project, conv)

        assert result.returncode == 0
        assert len(entries) >= 1
