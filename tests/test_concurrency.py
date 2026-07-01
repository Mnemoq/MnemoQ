"""R2 — concurrency-safe write tests.

Validates that concurrent log_core calls produce zero lost updates.
Uses threads (same-process, sufficient to expose the read-modify-write race
without OS-level locking, because CPython's GIL releases around file I/O).

Also tests the file_lock context manager directly and the hardened quarantine().
"""
import json
import threading

import pytest
from conftest import _make_ctx, _make_paths

from mnemoq.engine.handlers import log_core
from mnemoq.engine.io import file_lock, quarantine, read_learnings


_DOMAINS = ["backend", "frontend", "data", "tooling", "testing",
            "security", "api", "performance", "deployment", "ui",
            "database", "documentation"]

# Deliberately distinct in all semantic dimensions so the embedding dedup
# (threshold 0.85) treats them as unrelated. Each entry lives in a unique
# domain, touches a unique component tree, and uses semantically different
# trigger/action text so cosine similarity stays well below 0.85.
_DISTINCT_ENTRIES = [
    ("backend",    "AuthService",  "src/auth/service.py",     "When validating JWT tokens",                "ALWAYS validate expiry claims before accepting tokens"),
    ("frontend",   "ThemeToggle",  "src/ui/theme.py",         "When rendering user-provided theme values", "NEVER inject unsanitized CSS variables into the document"),
    ("data",       "BatchLoader",  "src/etl/loader.py",       "When running ETL aggregations",             "ALWAYS coerce NULL to sentinel before aggregation"),
    ("tooling",    "LintRunner",   "scripts/lint.py",         "When linting with conflicting configs",     "ALWAYS prefer pyproject.toml over .pylintrc when both exist"),
    ("testing",    "MockDB",       "tests/fixtures/db.py",    "When tearing down test fixtures",           "ALWAYS call teardown in reverse setup order to avoid deadlock"),
    ("security",   "CSRFGuard",   "src/middleware/csrf.py",   "When handling state-changing requests",     "ALWAYS pair the CSRF cookie with a matching request header"),
    ("api",        "RateLimit",    "src/api/throttle.py",     "When responding to throttled requests",     "ALWAYS set X-RateLimit-Remaining even on 429 responses"),
    ("performance","CacheLayer",   "src/cache/store.py",      "When sizing an LRU cache for production",   "ALWAYS size the LRU cache to 2x peak concurrent requests"),
    ("deployment", "K8sProbe",    "helm/values.yaml",         "When configuring Kubernetes health probes",  "ALWAYS configure readiness before liveness probe in k8s"),
    ("ui",         "FormValidator","src/forms/validate.py",   "When triggering async field validators",    "ALWAYS debounce async validators to avoid request stampede"),
    ("database",   "MigRunner",   "migrations/runner.py",     "When writing database migration scripts",   "ALWAYS wrap DDL in IF NOT EXISTS to keep migrations idempotent"),
    ("documentation","DocGen",    "scripts/gendocs.py",       "When regenerating API documentation",       "ALWAYS bust the doc cache after a schema change"),
]


def _base_entry(n):
    dom, comp, f, trigger, action = _DISTINCT_ENTRIES[n % len(_DISTINCT_ENTRIES)]
    return json.dumps({
        "step": n + 1,
        "source_agent": "gm",
        "type": "bug_fix",
        "domain": dom,
        "components": [comp],
        "files_touched": [f],
        "trigger": trigger,
        "action": action,
        "reason": f"Discovered during concurrent write stress test, worker {n}",
        "importance": 5,
        "severity": "minor",
    })


class TestFileLock:
    def test_basic_acquire_release(self, tmp_path):
        """Lock acquires and releases without error on a real file."""
        target = str(tmp_path / "learnings.jsonl")
        open(target, "w").close()
        with file_lock(target):
            assert (tmp_path / "learnings.jsonl.lock").exists()
        # Lock file is intentionally PERSISTENT (not removed) to avoid the POSIX
        # unlink/re-create race that would break cross-process exclusion.
        assert (tmp_path / "learnings.jsonl.lock").exists()

    def test_sequential_reacquire(self, tmp_path):
        """Lock can be acquired multiple times in sequence (not reentrant test)."""
        target = str(tmp_path / "x.jsonl")
        open(target, "w").close()
        for _ in range(5):
            with file_lock(target):
                pass

    def test_graceful_on_missing_parent(self, tmp_path):
        """file_lock degrades to unlocked (WARN) when parent dir doesn't exist."""
        import sys
        from io import StringIO
        buf = StringIO()
        old = sys.stderr
        sys.stderr = buf
        try:
            with file_lock(str(tmp_path / "nonexistent" / "x.jsonl")):
                pass  # must not raise
        finally:
            sys.stderr = old
        assert "WARN" in buf.getvalue()


class TestConcurrentLogCore:
    """N threads each write a unique entry; all N must survive."""

    N = 12  # enough to expose the race without being slow

    def _run(self, temp_project, n_threads):
        paths = _make_paths(temp_project / "memory", temp_project)
        ctx = _make_ctx()
        results = [None] * n_threads
        errors = []

        def worker(i):
            try:
                results[i] = log_core(_base_entry(i), paths, ctx)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return paths, results, errors

    @pytest.mark.smoke
    def test_no_entries_lost(self, temp_project):
        """All N concurrent log_core calls produce N distinct surviving entries."""
        paths, results, errors = self._run(temp_project, self.N)
        assert not errors, f"Worker exceptions: {errors}"
        # Every call should have succeeded
        assert all(r is not None for r in results)
        assert all(r["exit_code"] == 0 for r in results)
        # All N entries must be on disk — none silently dropped
        entries = read_learnings(paths)
        # entries may be ADDED or DUPLICATE (similarity-matched); the important
        # guarantee is that the file is not corrupted and all writes landed.
        assert len(entries) == self.N, (
            f"Expected {self.N} entries, got {len(entries)} — "
            f"lost-update race condition detected"
        )

    def test_file_not_corrupted(self, temp_project):
        """learnings.jsonl parses cleanly after concurrent writes (no partial lines)."""
        paths, _, _ = self._run(temp_project, self.N)
        raw = (temp_project / "memory" / "learnings.jsonl").read_text(encoding="utf-8")
        for i, line in enumerate(raw.strip().split("\n"), 1):
            try:
                json.loads(line)
            except json.JSONDecodeError as e:
                pytest.fail(f"Corrupted line {i} in learnings.jsonl: {e!r}")


class TestHardenedQuarantine:
    def test_quarantine_writes_record(self, temp_project):
        """quarantine() appends a valid JSON record to quarantine.jsonl."""
        paths = _make_paths(temp_project / "memory", temp_project)
        quarantine(paths, '{"bad": true}', "test reason")
        lines = [l for l in (temp_project / "memory" / "quarantine.jsonl")
                 .read_text().strip().split("\n") if l]
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["reason"] == "test reason"
        assert "ts" in rec

    def test_quarantine_concurrent_writes(self, temp_project):
        """Concurrent quarantine calls don't corrupt the file."""
        paths = _make_paths(temp_project / "memory", temp_project)
        N = 10
        threads = [threading.Thread(target=quarantine,
                                    args=(paths, f"raw{i}", f"reason{i}"))
                   for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        lines = [l for l in (temp_project / "memory" / "quarantine.jsonl")
                 .read_text().strip().split("\n") if l]
        assert len(lines) == N
        for line in lines:
            json.loads(line)  # must parse cleanly
