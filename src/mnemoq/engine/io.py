# Copyright (C) 2026 Mnemoq
# SPDX-License-Identifier: AGPL-3.0-or-later

"""File I/O helpers for the memory engine.

Extracted from filter.py (Phase 1). All functions take a Paths object
instead of reading module globals.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from datetime import datetime, timezone

from mnemoq.engine.migrate import migrate_entry

# ---------------------------------------------------------------------------
# Cross-platform advisory file lock
# ---------------------------------------------------------------------------

_LOCK_SUFFIX = ".lock"
_LOCK_TIMEOUT = 5.0  # seconds before giving up and logging a warning

# Per-path threading.Lock for same-process thread safety.  The OS-level lock
# (msvcrt/fcntl) only guards against concurrent *processes*; within a single
# process threads must additionally acquire this Python lock.
import threading as _threading
_thread_locks: dict = {}
_thread_locks_mu = _threading.Lock()


def _get_thread_lock(path: str) -> _threading.Lock:
    with _thread_locks_mu:
        if path not in _thread_locks:
            _thread_locks[path] = _threading.Lock()
        return _thread_locks[path]


@contextlib.contextmanager
def file_lock(path, timeout=_LOCK_TIMEOUT):
    """Exclusive lock for read-modify-write sequences — thread AND process safe.

    Two-layer locking:
    1. A ``threading.Lock`` per path guards concurrent threads in the same process.
    2. A sibling ``<path>.lock`` file locked with platform-native calls
       (``msvcrt.locking`` on Windows, ``fcntl.flock`` on POSIX) guards
       concurrent *processes*.

    If the OS-level lock cannot be acquired within *timeout* seconds, logs a
    single WARN and continues under the thread lock only — best-effort so a
    wedged lock never hangs the engine.

    Usage::

        with file_lock(paths.learnings_path):
            entries = read_learnings(paths)
            # ... mutate ...
            write_learnings(paths, entries)
    """
    thread_lock = _get_thread_lock(path)
    thread_lock.acquire()
    try:
        # OS-level lock for cross-process safety.
        lock_path = path + _LOCK_SUFFIX
        deadline = time.monotonic() + timeout
        sleep = 0.05

        try:
            # Open append (create-if-absent, never truncate). The lock file is
            # persistent — it is NOT removed on release. Removing it would create
            # an unlink race on POSIX: after A unlinks the path, C could open a
            # fresh inode and flock() it while B still holds the old inode, so two
            # processes would hold "the lock" on different inodes at once.
            lf = open(lock_path, "a", encoding="utf-8")
        except OSError as e:
            print(f"WARN: Cannot open lock file {lock_path}: {e} — proceeding unlocked",
                  file=sys.stderr)
            yield
            return

        os_acquired = False
        try:
            if sys.platform == "win32":
                import msvcrt
                while time.monotonic() < deadline:
                    try:
                        msvcrt.locking(lf.fileno(), msvcrt.LK_NBLCK, 1)
                        os_acquired = True
                        break
                    except OSError:
                        time.sleep(sleep)
                        sleep = min(sleep * 2, 0.5)
            else:
                import fcntl
                while time.monotonic() < deadline:
                    try:
                        fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        os_acquired = True
                        break
                    except OSError:
                        time.sleep(sleep)
                        sleep = min(sleep * 2, 0.5)

            if not os_acquired:
                print(f"WARN: Could not acquire OS lock on {lock_path} within {timeout}s "
                      f"— proceeding under thread lock only (potential cross-process write)",
                      file=sys.stderr)

            yield

        finally:
            if os_acquired:
                try:
                    if sys.platform == "win32":
                        import msvcrt
                        msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            lf.close()
            # Intentionally do NOT remove lock_path — see the open() note above.
            # The persistent sidecar avoids the POSIX unlink/re-create race.
    finally:
        thread_lock.release()


def _read_raw_jsonl(path):
    """Read JSONL lines, skipping malformed/empty lines. Returns list of parsed dicts."""
    entries = []
    if not os.path.exists(path):
        return entries
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"WARNING: Skipping malformed line {i} in {os.path.basename(path)}: {e}",
                      file=sys.stderr)
    return entries


def read_learnings(paths):
    """Read all entries from learnings.jsonl, auto-migrating each entry."""
    return [migrate_entry(e) for e in _read_raw_jsonl(paths.learnings_path)]


def read_learnings_for_dashboard(paths, ctx):
    """Read learnings or fakes depending on config data_source.

    When data_source is 'fakes', reads the fake_batches manifest and merges
    all active batch files from memory/fake_batches/. Falls back to
    memory/fakes.jsonl if no manifest exists (pre-migration).
    """
    source = (ctx or {}).get("data_source", "real")
    if source != "fakes":
        return [migrate_entry(e) for e in _read_raw_jsonl(paths.learnings_path)]

    # ponytail: no _ManifestLock here — _save_manifest uses atomic
    # tmp+replace, so reads see old or new state, never corrupted
    manifest_path = os.path.join(paths.memory_dir, "fake_batches.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError):
            manifest = []
        entries = []
        for batch in manifest:
            if not batch.get("active", True):
                continue
            entries.extend(_read_raw_jsonl(batch["file"]))
        return [migrate_entry(e) for e in entries]

    # Fallback: pre-migration fakes.jsonl
    fakes_path = os.path.join(paths.memory_dir, "fakes.jsonl")
    return [migrate_entry(e) for e in _read_raw_jsonl(fakes_path)]


def append_learning(paths, entry):
    """Append a single entry to learnings.jsonl with Windows retry."""
    line = json.dumps(entry, ensure_ascii=False) + "\n"

    max_retries = 3
    for attempt in range(max_retries):
        try:
            with open(paths.learnings_path, "a", encoding="utf-8") as f:
                f.write(line)
            return
        except PermissionError:
            if attempt < max_retries - 1:
                time.sleep(0.1 * (2 ** attempt))
            else:
                print(f"ERROR: Cannot write to {paths.learnings_path} after {max_retries} attempts", file=sys.stderr)
                print("File may be locked by antivirus or another process.", file=sys.stderr)
                raise


def write_learnings(paths, entries):
    """Rewrite learnings.jsonl atomically via temp file with Windows retry."""
    tmp_path = paths.learnings_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    max_retries = 3
    for attempt in range(max_retries):
        try:
            os.replace(tmp_path, paths.learnings_path)
            return
        except PermissionError:
            if attempt < max_retries - 1:
                time.sleep(0.1 * (2 ** attempt))
            else:
                print(f"WARNING: Atomic write failed after {max_retries} attempts, using fallback", file=sys.stderr)
                try:
                    with open(paths.learnings_path, "w", encoding="utf-8") as f:
                        for entry in entries:
                            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    os.remove(tmp_path)
                except PermissionError:
                    print("ERROR: Fallback write also failed. File may be locked.", file=sys.stderr)
                    print(f"Temp file preserved at: {tmp_path}", file=sys.stderr)
                    raise
                except OSError as e:
                    print(f"ERROR: Fallback write failed: {e}", file=sys.stderr)
                    print(f"Temp file preserved at: {tmp_path}", file=sys.stderr)
                    raise


def quarantine(paths, raw_input, reason):
    """Append a malformed/rejected entry to quarantine.jsonl with Windows retry."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {"raw": raw_input, "reason": reason, "ts": ts}
    line = json.dumps(record, ensure_ascii=False) + "\n"

    max_retries = 3
    for attempt in range(max_retries):
        try:
            with open(paths.quarantine_path, "a", encoding="utf-8") as f:
                f.write(line)
            return
        except PermissionError:
            if attempt < max_retries - 1:
                time.sleep(0.1 * (2 ** attempt))
            else:
                print(f"ERROR: Cannot write to {paths.quarantine_path} after {max_retries} attempts",
                      file=sys.stderr)
                raise
