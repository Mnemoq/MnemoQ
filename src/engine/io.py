"""File I/O helpers for the memory engine.

Extracted from filter.py (Phase 1). All functions take a Paths object
instead of reading module globals.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

from engine.migrate import migrate_entry


def _read_raw_jsonl(path):
    """Read JSONL lines, skipping malformed/empty lines. Returns list of parsed dicts."""
    entries = []
    if not os.path.exists(path):
        return entries
    with open(path, "r", encoding="utf-8") as f:
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
                    print(f"ERROR: Fallback write also failed. File may be locked.", file=sys.stderr)
                    print(f"Temp file preserved at: {tmp_path}", file=sys.stderr)
                    raise
                except OSError as e:
                    print(f"ERROR: Fallback write failed: {e}", file=sys.stderr)
                    print(f"Temp file preserved at: {tmp_path}", file=sys.stderr)
                    raise


def quarantine(paths, raw_input, reason):
    """Append a malformed/rejected entry to quarantine.jsonl."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "raw": raw_input,
        "reason": reason,
        "ts": ts
    }
    with open(paths.quarantine_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
