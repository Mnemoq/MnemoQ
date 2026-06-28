"""Git hook installation logic for the memory engine.

Extracted from cli.py to keep cli.py a thin dispatcher.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Canonical post-commit hook body, written by --install-hooks. Mirrored for
# reference in scripts/hooks/post-commit (keep in sync). Backgrounded with `&`
# and `exit 0` so it never adds latency to or blocks a commit. POSIX sh: works
# under git-for-windows' bundled sh as well as Linux/macOS.
POST_COMMIT_HOOK = """#!/bin/sh
# agent-memory-engine (mnemoq) post-commit hook -- installed by `mnemoq --install-hooks`.
# Runs auto-learning in the background after each commit. Never blocks the commit.
LOG="$(git rev-parse --git-dir)/mnemoq-auto-learn.log"
(
  python -m agent_memory.cli --auto-learn >/dev/null 2>>"$LOG" \\
    || echo "mnemoq auto-learn failed (see $LOG)" >>"$LOG"
) &
exit 0
"""


def install_hooks():
    """Install the post-commit hook into the current repo's git hooks dir.

    Returns an exit code. Resolves the hooks dir via `git rev-parse --git-path
    hooks` so it works inside worktrees and with custom core.hooksPath. chmod is
    best-effort: it is a no-op on Windows, where git-for-windows runs hooks via
    its bundled sh regardless of the executable bit.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("ERROR: --install-hooks must be run inside a git repository.", file=sys.stderr)
        return 1

    hooks_dir = Path(out.stdout.strip())
    if not hooks_dir.is_absolute():
        hooks_dir = Path.cwd() / hooks_dir
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "post-commit"

    if hook_path.exists():
        existing = hook_path.read_text(encoding="utf-8", errors="replace")
        if "mnemoq" not in existing and "agent_memory.cli --auto-learn" not in existing:
            print(f"ERROR: {hook_path} already exists and is not a mnemoq hook.", file=sys.stderr)
            print("Refusing to overwrite. Remove or merge it manually, then re-run.", file=sys.stderr)
            return 1

    # newline="\n" forces LF endings so the shebang/script is valid for sh.
    hook_path.write_text(POST_COMMIT_HOOK, encoding="utf-8", newline="\n")
    try:
        os.chmod(hook_path, 0o755)
    except OSError:
        pass  # best-effort; no-op / unsupported on Windows

    print(f"Installed post-commit hook: {hook_path}", file=sys.stderr)
    print("Auto-learning now runs in the background after each commit.", file=sys.stderr)
    return 0
