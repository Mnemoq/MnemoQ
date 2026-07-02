"""R8 — Deploy & scaffold robustness: template-resolution fallback chain and
opt-in new-IDE wiring on mnemoq-update.

Direct-import style (per the test_triggers/test_config exception in AGENTS.md).
"""
import subprocess
import sys
from pathlib import Path

import pytest

import mnemoq.scaffold as scaffold
from mnemoq.update import wire_new_ides, _has_managed_block


REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# Change 1 — template resolution fallback chain
# --------------------------------------------------------------------------

def test_resolve_prefers_source_checkout():
    # Running from the repo, the resolver must find the repo-root templates/.
    resolved = scaffold.resolve_template_dir()
    assert resolved == REPO_ROOT / "templates"
    assert (resolved / "config.json").exists()


def test_resolve_honors_override(tmp_path):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    assert scaffold.resolve_template_dir(str(tmp_path)) == tmp_path


def test_resolve_override_without_config_errors(tmp_path):
    with pytest.raises(SystemExit):
        scaffold.resolve_template_dir(str(tmp_path))


def test_scaffold_runs_without_deploy(tmp_path, monkeypatch):
    """Scaffold must work from a source checkout with no deployed ENGINE_DIR."""
    # Point ENGINE_DIR at an empty dir so only the repo fallback can satisfy it.
    fake_engine = tmp_path / "empty-engine"
    fake_engine.mkdir()
    monkeypatch.setattr(scaffold, "ENGINE_DIR", fake_engine)

    project = tmp_path / "proj"
    project.mkdir()
    (project / ".git").mkdir()  # look like a project

    result = subprocess.run(
        [sys.executable, "-m", "mnemoq.scaffold", str(project), "--defaults"],
        capture_output=True, text=True, cwd=str(REPO_ROOT / "src"),
    )
    assert result.returncode == 0, result.stderr
    assert (project / "memory" / "config.json").exists()
    assert (project / "memory" / "filter.py").exists()


def test_template_dir_override_global(monkeypatch, tmp_path):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(scaffold, "_TEMPLATE_DIR_OVERRIDE", tmp_path)
    assert scaffold.template_dir() == tmp_path


# --------------------------------------------------------------------------
# Change 2 — opt-in new-IDE wiring
# --------------------------------------------------------------------------

def test_wire_new_ide_wires_detected_surface(tmp_path):
    project = tmp_path / "proj"
    (project / ".cursor").mkdir(parents=True)  # user adopted Cursor post-scaffold
    results = wire_new_ides(project, "1.22.7", dry_run=False)
    assert results.get("cursor") == "wired"
    mdc = project / ".cursor" / "rules" / "memory-protocol.mdc"
    assert mdc.exists()
    assert _has_managed_block(mdc)


def test_wire_new_ide_idempotent(tmp_path):
    project = tmp_path / "proj"
    (project / ".cursor").mkdir(parents=True)
    first = wire_new_ides(project, "1.22.7", dry_run=False)
    assert first.get("cursor") == "wired"
    # Second run: cursor already carries the managed block -> no re-wire.
    second = wire_new_ides(project, "1.22.7", dry_run=False)
    assert second.get("cursor") == "already-wired"


def test_wire_new_ide_dry_run_writes_nothing(tmp_path):
    project = tmp_path / "proj"
    (project / ".cursor").mkdir(parents=True)
    results = wire_new_ides(project, "1.22.7", dry_run=True)
    assert results.get("cursor") == "would-wire"
    assert not (project / ".cursor" / "rules" / "memory-protocol.mdc").exists()


def test_wire_new_ide_no_surfaces(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    assert wire_new_ides(project, "1.22.7", dry_run=False) == {}
