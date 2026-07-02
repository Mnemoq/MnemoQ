# Copyright (C) 2026 Mnemoq
# SPDX-License-Identifier: AGPL-3.0-or-later

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


def staleness_tier(lines_changed, ctx=None):
    """Classify churn since an entry's commit into a graded staleness tier.

    Returns one of 'none' | 'minor' | 'moderate' | 'severe'. The 'minor' floor
    re-uses the existing auto_learn_staleness_threshold so it agrees with
    check_staleness()'s binary is_stale (is_stale == tier != 'none').
    """
    ctx = ctx or {}
    minor = ctx.get("auto_learn_staleness_threshold", 500)
    moderate = ctx.get("staleness_moderate_threshold", 1500)
    severe = ctx.get("staleness_severe_threshold", 5000)
    if lines_changed >= severe:
        return "severe"
    if lines_changed >= moderate:
        return "moderate"
    if lines_changed > minor:
        return "minor"
    return "none"


def check_staleness(entry, repo_root, ctx=None):
    """Check if an entry is stale based on git diff.

    Returns (is_stale, lines_changed, error_message).
    error_message is None on success, or a string describing the failure.
    """
    ctx = ctx or {}
    threshold = ctx.get("auto_learn_staleness_threshold", 500)
    if "commit" not in entry or not entry.get("files_touched"):
        return False, 0, None

    commit = entry["commit"]
    files = entry["files_touched"]

    try:
        # Use repo root as cwd so git can find files by relative path
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

        is_stale = lines_changed > threshold

        return is_stale, lines_changed, None

    except FileNotFoundError as e:
        return False, 0, f"git not found: {str(e)[:100]}"
