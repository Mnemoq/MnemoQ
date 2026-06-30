"""Shared fixtures and helpers for the test suite."""
import os
import subprocess
import sys
import tempfile
from collections import namedtuple
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ponytail: surface src/ to subprocesses so `python -m mnemoq.cli` resolves without a pip install.
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


@pytest.fixture
def fresh_project():
    """Create a fresh temporary project without memory directory (for scaffold tests)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        subprocess.run(["git", "init"], cwd=project_dir, capture_output=True)
        yield project_dir


_P = namedtuple("_P", ["memory_dir", "repo_root", "config_path",
                       "learnings_path", "quarantine_path",
                       "archive_dir", "session_file", "agents_md_path"])


def _make_paths(memory_dir, repo_root):
    """Build a Paths-like namedtuple for direct-import tests.

    Creates a fresh namedtuple per call — no cli.PATHS singleton mutation.
    Safe under pytest-xdist.
    """
    return _P(
        memory_dir=str(memory_dir),
        repo_root=str(repo_root),
        config_path=str(Path(memory_dir) / "config.json"),
        learnings_path=str(Path(memory_dir) / "learnings.jsonl"),
        quarantine_path=str(Path(memory_dir) / "quarantine.jsonl"),
        archive_dir=str(Path(memory_dir) / "archive"),
        session_file=str(Path(memory_dir) / ".consolidate_session.json"),
        agents_md_path=str(Path(repo_root) / "AGENTS.md"),
    )


def _load_config(config_path):
    """Call cli.load_config() with a temporary PATHS, restoring the singleton after.

    Encapsulates the save/restore pattern so callers can't forget the finally block.
    """
    from mnemoq import cli
    memory_dir = Path(config_path).parent
    repo_root = memory_dir.parent
    paths = _make_paths(memory_dir, repo_root)
    old_paths = cli.PATHS
    try:
        cli.PATHS = paths
        return cli.load_config()
    finally:
        cli.PATHS = old_paths


def _make_ctx(config_path=None, **overrides):
    """Build default ctx dict from DEFAULTS constants, with optional config overlay.

    If config_path is provided, reads config.json via _load_config (which
    encapsulates the cli.PATHS save/restore).
    Safe under pytest-xdist (separate processes, no shared state).
    """
    from mnemoq.engine.constants import DEFAULTS
    ctx = {k.lower(): v for k, v in DEFAULTS.items()}

    if config_path is not None:
        config = _load_config(config_path)
        if config:
            ctx.update({k.lower(): v for k, v in config.items()})

    ctx.update(overrides)
    return ctx
