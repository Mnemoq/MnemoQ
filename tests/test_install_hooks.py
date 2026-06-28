"""Integration test for `mnemoq --install-hooks`.

Creates a throwaway git repo, runs the installer via the CLI, and verifies the
post-commit hook is written. The executable-bit assertion is skipped on Windows,
where git-for-windows runs hooks via its bundled sh regardless of the bit.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

GIT = "git"


def _has_git():
    try:
        subprocess.run([GIT, "--version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _run_install_hooks(cwd):
    src_dir = str(Path(__file__).parent.parent / "src")
    env = dict(os.environ, PYTHONPATH=src_dir)
    return subprocess.run(
        [sys.executable, "-m", "agent_memory.cli", "--install-hooks"],
        capture_output=True, text=True, env=env, cwd=str(cwd),
    )


@pytest.mark.skipif(not _has_git(), reason="git not available")
def test_install_hooks_creates_post_commit():
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([GIT, "init"], cwd=tmp, capture_output=True, check=True)

        result = _run_install_hooks(tmp)
        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

        hook = Path(tmp) / ".git" / "hooks" / "post-commit"
        assert hook.exists(), "post-commit hook was not created"

        body = hook.read_text(encoding="utf-8")
        assert "agent_memory.cli --auto-learn" in body
        assert body.startswith("#!/bin/sh")
        # LF line endings so sh can parse the script.
        assert "\r\n" not in body

        if os.name != "nt":
            assert os.access(hook, os.X_OK), "hook should be executable on POSIX"


@pytest.mark.skipif(not _has_git(), reason="git not available")
def test_install_hooks_refuses_foreign_hook():
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([GIT, "init"], cwd=tmp, capture_output=True, check=True)
        hook = Path(tmp) / ".git" / "hooks" / "post-commit"
        hook.write_text("#!/bin/sh\necho someone-elses-hook\n", encoding="utf-8")

        result = _run_install_hooks(tmp)
        assert result.returncode == 1, "should refuse to overwrite a non-mnemoq hook"
        assert "someone-elses-hook" in hook.read_text(encoding="utf-8"), \
            "existing hook must be left untouched"


def test_install_hooks_outside_git_repo():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_install_hooks(tmp)
        assert result.returncode == 1
        assert "git repository" in result.stderr.lower()
