"""Unit tests for the managed-section sync used by scaffold/update to keep the
engine-owned Memory block current in shared agent files without clobbering user
content."""

from agent_memory.managed_section import sync_managed_section

SECTION = "## Memory\n\nDo the memory thing. --install-hooks\n"
SECTION_V2 = "## Memory\n\nDo the memory thing, revised. --install-hooks\n"


def _read(p):
    return p.read_text(encoding="utf-8")


def test_create_from_absent(tmp_path):
    f = tmp_path / "AGENTS.md"
    status = sync_managed_section(f, SECTION, "1.0.0")
    assert status == "created"
    body = _read(f)
    assert body.startswith("# Agent Rules")
    assert "BEGIN agent-memory:managed v=1.0.0" in body
    assert "## Memory" in body
    assert "END agent-memory:managed" in body


def test_absent_when_create_disabled(tmp_path):
    f = tmp_path / "AGENTS.md"
    status = sync_managed_section(f, SECTION, "1.0.0", create_if_absent=False)
    assert status == "absent"
    assert not f.exists()


def test_append_when_no_section(tmp_path):
    f = tmp_path / "AGENTS.md"
    f.write_text("# My project\n\nMy own rules.\n", encoding="utf-8")
    status = sync_managed_section(f, SECTION, "1.0.0")
    assert status == "appended"
    body = _read(f)
    assert "My own rules." in body  # user content preserved
    assert "BEGIN agent-memory:managed" in body


def test_current_is_idempotent(tmp_path):
    f = tmp_path / "AGENTS.md"
    sync_managed_section(f, SECTION, "1.0.0")
    before = _read(f)
    status = sync_managed_section(f, SECTION, "1.0.0")
    assert status == "current"
    assert _read(f) == before


def test_updated_on_version_bump(tmp_path):
    f = tmp_path / "AGENTS.md"
    sync_managed_section(f, SECTION, "1.0.0")
    status = sync_managed_section(f, SECTION, "1.1.0")
    assert status == "updated"
    assert "v=1.1.0" in _read(f)


def test_updated_on_body_change_same_version(tmp_path):
    f = tmp_path / "AGENTS.md"
    sync_managed_section(f, SECTION, "1.0.0")
    status = sync_managed_section(f, SECTION_V2, "1.0.0")
    assert status == "updated"
    assert "revised" in _read(f)


def test_preserves_user_content_outside_block(tmp_path):
    f = tmp_path / "AGENTS.md"
    f.write_text("# Mine\n\nKEEP ME\n", encoding="utf-8")
    sync_managed_section(f, SECTION, "1.0.0")
    sync_managed_section(f, SECTION_V2, "2.0.0")
    assert "KEEP ME" in _read(f)


def test_drift_skips_and_backs_up(tmp_path):
    f = tmp_path / "AGENTS.md"
    sync_managed_section(f, SECTION, "1.0.0")
    # Hand-edit inside the managed block.
    tampered = _read(f).replace("Do the memory thing.", "Do the memory thing. USER EDIT.")
    f.write_text(tampered, encoding="utf-8")
    status = sync_managed_section(f, SECTION_V2, "2.0.0")
    assert status == "drift"
    assert "USER EDIT." in _read(f)          # not clobbered
    assert "revised" not in _read(f)         # template not applied
    assert (tmp_path / "AGENTS.md.bak").exists()


def test_force_overrides_drift(tmp_path):
    f = tmp_path / "AGENTS.md"
    sync_managed_section(f, SECTION, "1.0.0")
    f.write_text(_read(f).replace("thing.", "thing. USER EDIT."), encoding="utf-8")
    status = sync_managed_section(f, SECTION_V2, "2.0.0", force=True)
    assert status == "updated"
    assert "revised" in _read(f)
    assert "USER EDIT." not in _read(f)


def test_migrate_legacy_unmarked_section(tmp_path):
    f = tmp_path / "AGENTS.md"
    f.write_text(
        "# Project\n\n## Memory\n\nOld unmarked protocol.\n\n## Other\n\nKeep this.\n",
        encoding="utf-8",
    )
    status = sync_managed_section(f, SECTION, "1.0.0")
    assert status == "migrated"
    body = _read(f)
    assert "BEGIN agent-memory:managed" in body
    assert "Old unmarked protocol." not in body   # legacy replaced
    assert "## Other" in body and "Keep this." in body  # later section preserved
    assert (tmp_path / "AGENTS.md.bak").exists()


def test_update_sync_instructions_existing_only(tmp_path, monkeypatch):
    """update.sync_instructions refreshes present files and never creates new ones."""
    import agent_memory.update as update

    monkeypatch.setattr(update, "read_memory_section", lambda: SECTION_V2)

    proj = tmp_path / "proj"
    proj.mkdir()
    # Only AGENTS.md exists, seeded with an old managed block.
    agents = proj / "AGENTS.md"
    sync_managed_section(agents, SECTION, "1.0.0")

    results = update.sync_instructions(proj, "2.0.0")

    assert results == {"AGENTS.md": "updated"}
    assert "revised" in _read(agents)
    assert not (proj / "CLAUDE.md").exists()      # not created
    assert not (proj / ".github" / "copilot-instructions.md").exists()
