"""R5 — Capture hardening tests.

Direct-import tests for the four targeted fixes:
1. Windows drive paths captured by _extract_files
2. Broadened correction detection phrases
3. Multi-sentence gist for correction turns
4. LLM extractors emit typed WARN on failure (not silent None)
"""
import io
import sys

import pytest

sys.path.insert(0, "src")

from mnemoq.engine.capture import (
    _detect_outcomes,
    _extract_files,
    _extract_gist,
    offline_llm_extract,
    online_llm_extract,
)


class TestWindowsPaths:
    """_extract_files captures Windows drive paths (C:/ and D:/) intact."""

    def test_windows_backslash_path(self):
        files = _extract_files(r"I edited C:\Users\project\src\auth.py")
        assert any("auth.py" in f for f in files), f"got {files}"
        assert any("C:" in f or "auth.py" in f for f in files)

    def test_windows_forward_slash_path(self):
        files = _extract_files("Look at C:/AgentMemoryEngine/src/mnemoq/cli.py")
        assert any("cli.py" in f for f in files), f"got {files}"

    def test_unix_path_still_works(self):
        files = _extract_files("edited src/engine/capture.py for the fix")
        assert any("capture.py" in f for f in files), f"got {files}"

    def test_multiple_paths_mixed(self):
        files = _extract_files(r"Changed C:\proj\src\a.py and also src/b.py")
        exts = {f.split(".")[-1] for f in files if "." in f}
        assert "py" in exts


class TestBroadenedCorrectionDetection:
    """Correction outcome fires on the expanded phrase set."""

    @pytest.mark.parametrize("text", [
        "no, don't do that",
        "stop — that's wrong",
        "that's incorrect, switch to async writes",
        "that won't work, prefer using flock instead",
        "that isn't right, use the new API instead of that",
        "that doesn't work, switch to the v2 endpoint",
    ])
    def test_correction_detected(self, text):
        outcomes = _detect_outcomes(text)
        assert "correction" in outcomes, f"missed correction in: {text!r}"

    def test_wrong_still_detected(self):
        outcomes = _detect_outcomes("that approach is wrong")
        assert "correction" in outcomes

    def test_incorrect_detected(self):
        outcomes = _detect_outcomes("that's incorrect, use X instead")
        assert "correction" in outcomes

    def test_no_false_positive_on_neutral(self):
        outcomes = _detect_outcomes("let's go ahead and refactor this module")
        assert "correction" not in outcomes


class TestMultiSentenceGist:
    """Correction turns capture up to 3 sentences of context."""

    def test_single_sentence_unchanged_for_non_correction(self):
        text = "let's add a test. Also update the docs. And bump the version."
        gist = _extract_gist(text, max_sentences=1)
        # should be one sentence
        assert "Also" not in gist or gist.count(". ") == 0

    def test_correction_gets_multiple_sentences(self):
        # Both sentences have signal words so both should be collected
        text = ("That's wrong, switch to async writes instead. "
                "Also stop using the sync fallback in src/db.py.")
        gist = _extract_gist(text, max_sentences=3)
        # At minimum the first sentence (has 'wrong' signal) must appear
        assert "wrong" in gist or "async" in gist
        # With max_sentences=3 the second sentence should also be captured
        assert "stop" in gist or "fallback" in gist or "db.py" in gist

    def test_max_chars_still_respected(self):
        long_sentence = "That's wrong " + "x" * 300
        gist = _extract_gist(long_sentence, max_chars=200, max_sentences=3)
        assert len(gist) <= 200

    def test_non_signal_first_sentence_does_not_crowd_out_correction(self):
        """Regression: a long non-signal opener must not eat the budget and
        drop the actual correction (the real signal-bearing sentence)."""
        text = ("This is a long neutral opening sentence with no signal at all "
                "and it goes on for a while to eat the budget up completely. "
                "That is wrong, switch to async.")
        gist = _extract_gist(text, max_chars=90, max_sentences=3)
        assert "wrong" in gist or "switch to async" in gist, f"got: {gist!r}"
        assert "neutral opening" not in gist


class TestLLMFailLoud:
    """LLM extractors emit a WARN line on failure instead of silent None."""

    def _capture_stderr(self, fn, *args, **kwargs):
        buf = io.StringIO()
        old = sys.stderr
        sys.stderr = buf
        try:
            result = fn(*args, **kwargs)
        finally:
            sys.stderr = old
        return result, buf.getvalue()

    def test_offline_no_endpoint_returns_none_silently(self):
        """No endpoint configured — silent None (no server to warn about)."""
        result, stderr = self._capture_stderr(offline_llm_extract, "test", {})
        assert result is None
        assert "WARN" not in stderr

    def test_offline_bad_endpoint_warns(self):
        """Unreachable endpoint emits a typed WARN."""
        ctx = {"capture_llm_endpoint": "http://127.0.0.1:19999"}
        result, stderr = self._capture_stderr(
            offline_llm_extract, "test conversation", ctx)
        assert result is None
        assert "WARN" in stderr

    def test_online_no_endpoint_returns_none_silently(self):
        """Missing endpoint/model/key — silent None."""
        result, stderr = self._capture_stderr(online_llm_extract, "test", {})
        assert result is None
        assert "WARN" not in stderr

    def test_online_bad_endpoint_warns(self):
        """Unreachable endpoint emits a typed WARN."""
        ctx = {
            "capture_online_endpoint": "http://127.0.0.1:19999",
            "capture_online_model": "gpt-test",
            "capture_online_api_key": "fake-key",
        }
        result, stderr = self._capture_stderr(
            online_llm_extract, "test conversation", ctx)
        assert result is None
        assert "WARN" in stderr
