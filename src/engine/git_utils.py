"""Git utilities for the memory engine.

Extracted from filter.py.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone


def stamp_entry(entry, repo_root):
    """Auto-inject commit and ts fields if not provided."""
    if "commit" not in entry or not entry["commit"]:
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL,
                cwd=repo_root
            ).decode().strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            commit = "unknown"
        entry["commit"] = commit

    if "ts" not in entry or not entry["ts"]:
        entry["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if "access_count" not in entry:
        entry["access_count"] = 0

    if "reinforcement_count" not in entry:
        entry["reinforcement_count"] = 0

    return entry


def check_staleness(entry, repo_root):
    """Check if an entry is stale based on git diff.

    Returns (is_stale, lines_changed, error_message).
    error_message is None on success, or a string describing the failure.
    """
    if "commit" not in entry or not entry.get("files_touched"):
        return False, 0, None

    commit = entry["commit"]
    files = entry["files_touched"]

    try:
        # Use repo root as cwd so git can find files like src/entities/GroundScenery.ts
        result = subprocess.run(
            ["git", "diff", "--stat", f"{commit}..HEAD", "--"] + files,
            capture_output=True, text=True, cwd=repo_root
        )

        if result.returncode != 0:
            return False, 0, f"git diff failed: {result.stderr.strip()[:100]}"

        # Parse diff stat output to count lines changed
        lines_changed = 0
        for line in result.stdout.strip().split("\n"):
            if "|" in line:
                parts = line.split("|")
                if len(parts) >= 2:
                    changes_str = parts[1].strip().split()[0]
                    try:
                        lines_changed += int(changes_str)
                    except ValueError:
                        pass

        # Staleness threshold: >500 lines changed
        is_stale = lines_changed > 500

        return is_stale, lines_changed, None

    except FileNotFoundError as e:
        return False, 0, f"git not found: {str(e)[:100]}"
