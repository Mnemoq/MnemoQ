# Copyright (C) 2026 Mnemoq
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Git hook installation logic for the memory engine.

Two modes:
  - Default: write hooks into the repo's resolved hooks dir (`git rev-parse
    --git-path hooks`), normally `.git/hooks`. Untracked by git.
  - --hooks-path DIR: write hooks into a tracked directory (e.g. `.githooks`)
    and set `core.hooksPath` so the hooks are shared via git.

Both modes install post-commit (runs `--auto-learn` in the background) and
pre-commit (runs `--scan-staged`, never blocks the commit). Idempotent: an
existing mnemoq hook is overwritten in place, a foreign hook is left alone.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# POSIX sh scripts: work under git-for-windows' bundled sh as well as
# Linux/macOS. Both invoke the `mnemoq` console script so a venv-installed
# engine on PATH is picked up (falls back to `python -m mnemoq.cli`).

POST_COMMIT_HOOK = """#!/bin/sh
# agent-memory-engine (mnemoq) post-commit hook — installed by `mnemoq --install-hooks`.
# Runs auto-learning in the background after each commit. Never blocks the commit.
LOG="$(git rev-parse --git-dir)/mnemoq-auto-learn.log"
(
  if command -v mnemoq >/dev/null 2>&1; then
    mnemoq --auto-learn >/dev/null 2>>"$LOG" \\
      || echo "mnemoq auto-learn failed (see $LOG)" >>"$LOG"
  else
    python -m mnemoq.cli --auto-learn >/dev/null 2>>"$LOG" \\
      || echo "mnemoq auto-learn failed (see $LOG)" >>"$LOG"
  fi
) &
exit 0
"""

PRE_COMMIT_HOOK = """#!/bin/sh
# agent-memory-engine (mnemoq) pre-commit hook — installed by `mnemoq --install-hooks`.
# Scans staged diff for new TODO/FIXME/HACK/XXX markers. Best-effort: NEVER fails the commit.
LOG="$(git rev-parse --git-dir)/mnemoq-scan-staged.log"
if command -v mnemoq >/dev/null 2>&1; then
  mnemoq --scan-staged 2>>"$LOG" || true
else
  python -m mnemoq.cli --scan-staged 2>>"$LOG" || true
fi
exit 0
"""

_HOOK_BODIES = {
    "post-commit": POST_COMMIT_HOOK,
    "pre-commit": PRE_COMMIT_HOOK,
}

_MNEMOQ_MARKERS = ("mnemoq", "mnemoq.cli")


def _is_mnemoq_hook(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(marker in text for marker in _MNEMOQ_MARKERS)


def _write_hook(hook_path: Path, body: str) -> bool:
    """Write a hook file. Returns True if written, False if a foreign hook blocks us."""
    if hook_path.exists() and not _is_mnemoq_hook(hook_path):
        print(f"WARNING: {hook_path} already exists and is not a mnemoq hook — skipping.",
              file=sys.stderr)
        print("  Remove or merge it manually, then re-run --install-hooks.", file=sys.stderr)
        return False
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text(body, encoding="utf-8", newline="\n")
    try:
        os.chmod(hook_path, 0o755)
    except OSError:
        pass  # best-effort; no-op on Windows
    return True


def _resolve_default_hooks_dir() -> Path | None:
    """Resolve the repo's effective hooks dir via git."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("ERROR: --install-hooks must be run inside a git repository.", file=sys.stderr)
        return None
    hooks_dir = Path(out.stdout.strip())
    if not hooks_dir.is_absolute():
        hooks_dir = Path.cwd() / hooks_dir
    return hooks_dir


def _set_core_hooks_path(value: str) -> bool:
    try:
        subprocess.run(
            ["git", "config", "core.hooksPath", value],
            check=True, capture_output=True, text=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"ERROR: failed to set core.hooksPath: {e}", file=sys.stderr)
        return False


def install_hooks(hooks_path: str | None = None) -> int:
    """Install post-commit + pre-commit hooks.

    Args:
        hooks_path: If provided (e.g. ".githooks"), write hooks into this
            tracked directory and configure `core.hooksPath` to point at it.
            If None, write into the repo's resolved hooks dir (`.git/hooks`).

    Returns:
        Exit code (0 = success, 1 = aborted).
    """
    if hooks_path:
        raw = Path(hooks_path)
        if raw.is_absolute():
            target_dir = raw
        else:
            # Anchor to the working-tree root, NOT cwd: a user invoking from a
            # subdirectory still gets `.githooks` created at the repo root, and
            # the absolute path we store in `core.hooksPath` matches that.
            try:
                top = subprocess.run(
                    ["git", "rev-parse", "--show-toplevel"],
                    capture_output=True, text=True, check=True,
                ).stdout.strip()
            except (subprocess.CalledProcessError, FileNotFoundError):
                print("ERROR: --hooks-path requires a git repository.", file=sys.stderr)
                return 1
            target_dir = Path(top) / raw
        target_dir.mkdir(parents=True, exist_ok=True)
        if not _set_core_hooks_path(str(target_dir)):
            return 1
        print(f"Configured core.hooksPath -> {target_dir}", file=sys.stderr)
    else:
        resolved = _resolve_default_hooks_dir()
        if resolved is None:
            return 1
        target_dir = resolved

    wrote_any = False
    refused_any = False
    for hook_name, body in _HOOK_BODIES.items():
        hook_path = target_dir / hook_name
        if _write_hook(hook_path, body):
            print(f"Installed {hook_name}: {hook_path}", file=sys.stderr)
            wrote_any = True
        else:
            refused_any = True

    if refused_any:
        return 1

    if wrote_any:
        if hooks_path:
            print(
                "Done. Commit the tracked hooks directory so collaborators pick them up:\n"
                f"  git add {hooks_path} && git commit -m 'chore: add mnemoq git hooks'",
                file=sys.stderr,
            )
        else:
            print("Done. Auto-learning + debt-marker scanning are now active for this clone.",
                  file=sys.stderr)
    return 0
