"""Tests for scaffold, shim, and update hygiene."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class TestScaffoldIntegration:
    """Test scaffold.py integration."""

    def test_scaffold_creates_memory(self, fresh_project):
        """Test that scaffold creates memory directory structure."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.scaffold", str(fresh_project), "--defaults"],
            capture_output=True, text=True,
            cwd=str(source_dir)
        )

        assert result.returncode == 0

        memory_dir = fresh_project / "memory"
        assert memory_dir.exists()
        assert (memory_dir / "filter.py").exists()
        assert (memory_dir / "config.json").exists()
        assert (memory_dir / "learnings.jsonl").exists()
        assert (memory_dir / "quarantine.jsonl").exists()

    def test_scaffold_opencode_wiring(self, fresh_project):
        """Test that --opencode flag wires opencode.json."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.scaffold", str(fresh_project), "--defaults", "--opencode"],
            capture_output=True, text=True,
            cwd=str(source_dir)
        )

        assert result.returncode == 0

        opencode_path = fresh_project / "opencode.json"
        assert opencode_path.exists()

        opencode = json.loads(opencode_path.read_text())
        assert "instructions" in opencode
        assert "memory/SYSTEM_INVARIANTS.md" in opencode["instructions"]
        assert "memory/HANDOFF.md" in opencode["instructions"]

        assert "agent" in opencode
        assert "gm" in opencode["agent"]
        assert "code-reviewer" in opencode["agent"]

    def test_scaffold_ide_flag_opencode(self, fresh_project):
        """Test that --ide opencode produces same results as --opencode."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.scaffold", str(fresh_project), "--defaults", "--ide", "opencode"],
            capture_output=True, text=True,
            cwd=str(source_dir)
        )

        assert result.returncode == 0
        assert (fresh_project / "opencode.json").exists()
        assert (fresh_project / ".opencode" / "prompts" / "gm.md").exists()
        assert (fresh_project / ".opencode" / "prompts" / "docs-writer.md").exists()

    def test_scaffold_opencode_backward_compat(self, fresh_project):
        """Test that --opencode hidden alias still works."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.scaffold", str(fresh_project), "--defaults", "--opencode"],
            capture_output=True, text=True,
            cwd=str(source_dir)
        )

        assert result.returncode == 0
        assert (fresh_project / "opencode.json").exists()

    def test_scaffold_windsurf_wiring(self, fresh_project):
        """Test that --ide windsurf creates workflows, Plans dir, and AGENTS.md."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.scaffold", str(fresh_project), "--defaults", "--ide", "windsurf"],
            capture_output=True, text=True,
            cwd=str(source_dir)
        )

        assert result.returncode == 0
        assert (fresh_project / ".windsurf" / "workflows" / "gm.md").exists()
        assert (fresh_project / ".windsurf" / "workflows" / "code-reviewer.md").exists()
        assert (fresh_project / ".windsurf" / "workflows" / "docs-writer.md").exists()
        assert (fresh_project / ".windsurf" / "Plans").exists()
        assert (fresh_project / ".windsurf" / "Plans" / ".gitkeep").exists()
        assert (fresh_project / "AGENTS.md").exists()
        agents_content = (fresh_project / "AGENTS.md").read_text()
        assert "## Memory" in agents_content

    def test_scaffold_cursor_wiring(self, fresh_project):
        """Test that --ide cursor creates .cursor/rules/*.mdc and AGENTS.md."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.scaffold", str(fresh_project), "--defaults", "--ide", "cursor"],
            capture_output=True, text=True,
            cwd=str(source_dir)
        )

        assert result.returncode == 0
        assert (fresh_project / ".cursor" / "rules" / "memory-protocol.mdc").exists()
        assert (fresh_project / ".cursor" / "rules" / "gm.mdc").exists()
        assert (fresh_project / ".cursor" / "rules" / "code-reviewer.mdc").exists()
        assert (fresh_project / ".cursor" / "rules" / "docs-writer.mdc").exists()
        assert (fresh_project / "AGENTS.md").exists()
        agents_content = (fresh_project / "AGENTS.md").read_text()
        assert "## Memory" in agents_content

    def test_scaffold_claude_code_wiring(self, fresh_project):
        """Test that --ide claude-code creates CLAUDE.md with memory protocol."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.scaffold", str(fresh_project), "--defaults", "--ide", "claude-code"],
            capture_output=True, text=True,
            cwd=str(source_dir)
        )

        assert result.returncode == 0
        assert (fresh_project / "CLAUDE.md").exists()
        claude_content = (fresh_project / "CLAUDE.md").read_text()
        assert "## Memory" in claude_content
        assert "filter.py" in claude_content

    def test_scaffold_copilot_wiring(self, fresh_project):
        """Test that --ide copilot creates .github/copilot-instructions.md and AGENTS.md."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.scaffold", str(fresh_project), "--defaults", "--ide", "copilot"],
            capture_output=True, text=True,
            cwd=str(source_dir)
        )

        assert result.returncode == 0
        assert (fresh_project / ".github" / "copilot-instructions.md").exists()
        copilot_content = (fresh_project / ".github" / "copilot-instructions.md").read_text()
        assert "## Memory" in copilot_content
        assert (fresh_project / "AGENTS.md").exists()
        agents_content = (fresh_project / "AGENTS.md").read_text()
        assert "## Memory" in agents_content

    def test_scaffold_multi_ide(self, fresh_project):
        """Test that --ide windsurf,cursor wires both platforms."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.scaffold", str(fresh_project),
             "--defaults", "--ide", "windsurf,cursor"],
            capture_output=True, text=True,
            cwd=str(source_dir)
        )

        assert result.returncode == 0
        assert (fresh_project / ".windsurf" / "workflows" / "gm.md").exists()
        assert (fresh_project / ".cursor" / "rules" / "gm.mdc").exists()
        assert (fresh_project / "AGENTS.md").exists()

    def test_scaffold_ide_invalid_platform(self, fresh_project):
        """Test that unknown IDE platform exits with error."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.scaffold", str(fresh_project), "--defaults", "--ide", "foo"],
            capture_output=True, text=True,
            cwd=str(source_dir)
        )

        assert result.returncode != 0
        assert "Unknown IDE platform" in result.stderr or "Unknown IDE platform" in result.stdout

    def test_scaffold_ide_list_platforms(self):
        """Test that --ide ? lists available platforms and exits 0."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.scaffold", "--ide", "?"],
            capture_output=True, text=True,
            cwd=str(source_dir)
        )

        assert result.returncode == 0
        assert "Available IDE platforms" in result.stdout
        assert "windsurf" in result.stdout
        assert "cursor" in result.stdout
        assert "claude-code" in result.stdout
        assert "copilot" in result.stdout
        assert "all" in result.stdout

    def test_scaffold_ide_all(self, fresh_project):
        """Test that --ide all wires every platform."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.scaffold", str(fresh_project), "--defaults", "--ide", "all"],
            capture_output=True, text=True,
            cwd=str(source_dir)
        )

        assert result.returncode == 0
        assert (fresh_project / ".windsurf" / "workflows" / "gm.md").exists()
        assert (fresh_project / ".cursor" / "rules" / "gm.mdc").exists()
        assert (fresh_project / "CLAUDE.md").exists()
        assert (fresh_project / ".github" / "copilot-instructions.md").exists()
        assert (fresh_project / "AGENTS.md").exists()
        assert "Wired:" in result.stdout

    def test_memory_protocol_single_source_propagation(self, tmp_path, monkeypatch):
        """The shared memory protocol is single-sourced from agents-memory-section.md.

        Hermetic: points scaffold.ENGINE_DIR at the repo tree (not the deployed
        ~/.agent-memory/engine) so the assertion is about the repo's generation
        logic, independent of deploy state. The canonical body must reach every
        generated IDE target — AGENTS.md, CLAUDE.md, copilot-instructions.md, and
        cursor's generated memory-protocol.mdc (which must keep its frontmatter)."""
        import mnemoq.scaffold as scaffold

        repo_root = Path(__file__).parent.parent
        monkeypatch.setattr(scaffold, "ENGINE_DIR", repo_root)

        target = tmp_path / "proj"
        target.mkdir()

        scaffold.wire_claude_code(target)
        scaffold.wire_copilot(target)
        scaffold.wire_cursor(target)

        canonical = (repo_root / "templates" / "agents-memory-section.md").read_text(encoding="utf-8")
        assert "--install-hooks" in canonical, "test sentinel missing from canonical source"

        targets = [
            target / "CLAUDE.md",
            target / ".github" / "copilot-instructions.md",
            target / "AGENTS.md",
            target / ".cursor" / "rules" / "memory-protocol.mdc",
        ]
        for t in targets:
            assert t.exists(), f"{t} was not generated"
            assert "--install-hooks" in t.read_text(encoding="utf-8"), \
                f"{t.name} missing single-sourced protocol content"

        mdc = (target / ".cursor" / "rules" / "memory-protocol.mdc").read_text(encoding="utf-8")
        assert mdc.startswith("---"), "cursor memory-protocol.mdc missing frontmatter"
        assert "alwaysApply: true" in mdc

    def test_templates_are_platform_agnostic(self):
        """Test that shared templates contain no opencode-specific bias."""
        templates_dir = Path(__file__).parent.parent / "templates"
        shared_files = list((templates_dir / "prompts").glob("*.md"))
        shared_files.append(templates_dir / "agents-memory-section.md")
        shared_files.extend((templates_dir / "windsurf" / "workflows").glob("*.md"))
        shared_files.extend((templates_dir / "cursor-rules").glob("*.mdc"))
        biased_patterns = [".opencode/", "opencode-go", "via opencode.json"]
        for f in shared_files:
            content = f.read_text(encoding='utf-8').lower()
            for pattern in biased_patterns:
                assert pattern not in content, f"Template {f.name} contains biased pattern '{pattern}'"


class TestUpdateHygiene:
    """Test update.py hygiene features (temp path detection, auto-prune)."""

    def test_is_temp_path_detects_temp(self):
        """is_temp_path returns True for paths under system temp dir."""
        from mnemoq.update import is_temp_path

        temp_dir = Path(tempfile.gettempdir())
        assert is_temp_path(temp_dir) is True
        assert is_temp_path(temp_dir / "some" / "subdir") is True
        if sys.platform == "win32":
            assert is_temp_path(Path("C:/Users/Admin/AppData/Local/Temp") / "tmp1234") is True

    def test_is_temp_path_rejects_non_temp(self):
        """is_temp_path returns False for paths not under system temp dir."""
        from mnemoq.update import is_temp_path

        assert is_temp_path(Path("C:/Projects/magpie-swoop")) is False
        assert is_temp_path(Path("/home/user/projects/foo")) is False
        assert is_temp_path(Path(tempfile.gettempdir()).parent / "other_dir") is False

    def test_is_temp_path_handles_nonexistent(self):
        """is_temp_path returns False for non-existent paths (doesn't crash)."""
        from mnemoq.update import is_temp_path

        assert is_temp_path(Path("Z:/nonexistent/path")) is False

    def test_load_projects_prunes_temp_entries(self, tmp_path, monkeypatch):
        """load_projects auto-prunes temp entries with backup."""
        from mnemoq import update

        engine_dir = tmp_path / "engine"
        engine_dir.mkdir()
        projects_file = engine_dir / "projects.txt"

        temp_entry = str(Path(tempfile.gettempdir()) / "tmp1234")
        valid_entry = "C:/Projects/my-project" if sys.platform == "win32" else "/home/user/projects/my-project"
        projects_file.write_text(f"# Comment\n{valid_entry}\n{temp_entry}\n")

        monkeypatch.setattr(update, "ENGINE_DIR", engine_dir)

        result = update.load_projects(dry_run=False)

        assert len(result) == 1
        assert result[0] == Path(valid_entry)

        backups = list(engine_dir.glob("projects.txt.backup-*"))
        assert len(backups) == 1

        content = projects_file.read_text()
        assert temp_entry not in content
        assert "my-project" in content

    def test_load_projects_dry_run_no_prune(self, tmp_path, monkeypatch):
        """load_projects with dry_run=True does not modify projects.txt."""
        from mnemoq import update

        engine_dir = tmp_path / "engine"
        engine_dir.mkdir()
        projects_file = engine_dir / "projects.txt"

        temp_entry = str(Path(tempfile.gettempdir()) / "tmp1234")
        valid_entry = "C:/Projects/my-project" if sys.platform == "win32" else "/home/user/projects/my-project"
        original_content = f"# Comment\n{valid_entry}\n{temp_entry}\n"
        projects_file.write_text(original_content)

        monkeypatch.setattr(update, "ENGINE_DIR", engine_dir)

        result = update.load_projects(dry_run=True)

        assert len(result) == 1
        assert projects_file.read_text() == original_content

        backups = list(engine_dir.glob("projects.txt.backup-*"))
        assert len(backups) == 0


class TestShim:
    """Test shim functionality."""

    def test_shim_delegates_to_engine(self, temp_project):
        """Test that shim correctly delegates to central engine."""
        from mnemoq.shim import SHIM_TEMPLATE

        memory_dir = temp_project / "memory"
        shim_path = memory_dir / "filter.py"
        shim_path.write_text(SHIM_TEMPLATE)

        result = subprocess.run(
            [sys.executable, str(shim_path), "--version"],
            capture_output=True, text=True,
            cwd=temp_project
        )

        assert result.returncode == 0
        assert "agent-memory-engine" in result.stderr

    def test_shim_memory_dir_override(self, temp_project, tmp_path):
        """Test that --memory-dir CLI flag overrides AGENT_MEMORY_DIR set by shim."""
        from mnemoq.shim import SHIM_TEMPLATE

        memory_dir = temp_project / "memory"
        shim_path = memory_dir / "filter.py"
        shim_path.write_text(SHIM_TEMPLATE)

        alt_memory = tmp_path / "alt_memory"
        alt_memory.mkdir()
        (alt_memory / "learnings.jsonl").touch()
        (alt_memory / "quarantine.jsonl").touch()
        (alt_memory / "archive").mkdir()

        result = subprocess.run(
            [sys.executable, str(shim_path), "--memory-dir", str(alt_memory), "--stats"],
            capture_output=True, text=True,
            cwd=temp_project
        )

        assert result.returncode == 0
        assert "MEMORY STATS" in result.stdout

    def test_migrate_to_shim(self, temp_project):
        """Test --migrate-to-shim converts full copy to shim."""
        from mnemoq.shim import is_shim
        from mnemoq.update import migrate_to_shim

        memory_dir = temp_project / "memory"
        engine_filter = Path.home() / ".agent-memory" / "engine" / "filter.py"
        if engine_filter.exists():
            shutil.copy2(engine_filter, memory_dir / "filter.py")
        else:
            # Fallback: write a non-shim filter.py so migrate has something to replace
            (memory_dir / "filter.py").write_text("# full engine copy\nprint('hello')\n")

        success, msg = migrate_to_shim(temp_project)

        assert success
        assert "Migrated" in msg

        assert is_shim(memory_dir / "filter.py")

        shim_content = (memory_dir / "filter.py").read_text(encoding="utf-8")
        assert len(shim_content.splitlines()) < 50
        assert "AGENT_MEMORY_DIR" in shim_content
        backups = list((memory_dir / "backups").glob("*"))
        assert len(backups) == 1

    def test_migrate_to_shim_idempotent(self, temp_project):
        """Test running --migrate-to-shim twice is safe."""
        from mnemoq.update import migrate_to_shim

        memory_dir = temp_project / "memory"
        engine_filter = Path.home() / ".agent-memory" / "engine" / "filter.py"
        if engine_filter.exists():
            shutil.copy2(engine_filter, memory_dir / "filter.py")
        else:
            (memory_dir / "filter.py").write_text("# full engine copy\nprint('hello')\n")

        success1, msg1 = migrate_to_shim(temp_project)
        assert success1
        assert "Migrated" in msg1

        backups_after_first = list((memory_dir / "backups").glob("*"))

        success2, msg2 = migrate_to_shim(temp_project)
        assert success2
        assert "Already a shim" in msg2

        backups_after_second = list((memory_dir / "backups").glob("*"))
        assert len(backups_after_first) == len(backups_after_second)

    def test_shim_missing_engine(self, temp_project, tmp_path):
        """Test shim handles missing engine gracefully."""
        from mnemoq.shim import SHIM_TEMPLATE

        missing_engine = tmp_path / "nonexistent" / "engine" / "filter.py"
        custom_shim = SHIM_TEMPLATE.replace(
            'engine_path = os.path.expanduser("~/.agent-memory/engine/filter.py")',
            f'engine_path = r"{missing_engine}"'
        )

        memory_dir = temp_project / "memory"
        shim_path = memory_dir / "filter.py"
        shim_path.write_text(custom_shim)

        result = subprocess.run(
            [sys.executable, str(shim_path), "--stats"],
            capture_output=True, text=True,
            cwd=temp_project
        )

        assert result.returncode == 1
        assert "Engine not found" in result.stderr
        assert "deploy script" in result.stderr

    def test_profile_loads_post_migration(self, temp_project):
        """Test that profile.py loads from central location after migration."""
        from mnemoq.update import migrate_to_shim

        memory_dir = temp_project / "memory"
        engine_filter = Path.home() / ".agent-memory" / "engine" / "filter.py"
        if engine_filter.exists():
            shutil.copy2(engine_filter, memory_dir / "filter.py")
        else:
            (memory_dir / "filter.py").write_text("# full engine copy\nprint('hello')\n")

        migrate_to_shim(temp_project)

        result = subprocess.run(
            [sys.executable, str(memory_dir / "filter.py"), "--step", "1",
             "--components", "Tooling", "--domain", "tooling"],
            capture_output=True, text=True,
            cwd=temp_project
        )

        assert result.returncode == 0

    def test_scaffold_force_overwrites_old_copy(self, fresh_project):
        """Test that scaffold.py --force overwrites old full copy with shim."""
        from mnemoq.shim import is_shim

        source_dir = Path(__file__).parent.parent / "src"

        memory_dir = fresh_project / "memory"
        memory_dir.mkdir()
        engine_filter = Path.home() / ".agent-memory" / "engine" / "filter.py"
        if engine_filter.exists():
            shutil.copy2(engine_filter, memory_dir / "filter.py")
        else:
            (memory_dir / "filter.py").write_text("# full engine copy\nprint('hello')\n")

        assert not is_shim(memory_dir / "filter.py")

        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.scaffold", str(fresh_project), "--defaults", "--force"],
            capture_output=True, text=True,
            cwd=str(source_dir)
        )

        assert result.returncode == 0
        assert is_shim(memory_dir / "filter.py")

    def test_scaffold_idempotent(self, fresh_project):
        """Test that scaffold.py is idempotent when already a shim."""
        from mnemoq.shim import SHIM_TEMPLATE, is_shim

        memory_dir = fresh_project / "memory"
        memory_dir.mkdir()
        shim_path = memory_dir / "filter.py"
        shim_path.write_text(SHIM_TEMPLATE)

        mtime_before = shim_path.stat().st_mtime_ns

        from mnemoq.scaffold import copy_engine_files

        copy_engine_files(memory_dir, force=False)

        mtime_after = shim_path.stat().st_mtime_ns
        assert mtime_before == mtime_after

        assert is_shim(shim_path)
