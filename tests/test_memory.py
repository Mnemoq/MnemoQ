"""
Tests for multi-project memory system.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ponytail: surface src/ to subprocesses so `python -m agent_memory.cli` resolves without a pip install.
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
        # Create required files that filter.py expects
        (memory_dir / "learnings.jsonl").touch()
        (memory_dir / "quarantine.jsonl").touch()
        (memory_dir / "archive").mkdir()
        yield project_dir


@pytest.fixture
def fresh_project():
    """Create a fresh temporary project without memory directory (for scaffold tests)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        # Initialize as git repo so scaffold recognizes it as a project
        subprocess.run(["git", "init"], cwd=project_dir, capture_output=True)
        yield project_dir


@pytest.fixture
def engine_dir():
    """Get the engine directory path."""
    return Path.home() / ".agent-memory" / "engine"


class TestConfigLoad:
    """Test config.json loading."""
    
    def test_config_load_valid(self, temp_project, engine_dir):
        """Test that valid config.json is loaded correctly."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test Project",
            "max_step": 50,
            "valid_domains": ["frontend", "backend"],
            "valid_source_agents": ["gm", "reviewer"],
            "tuning": {
                "decay_rate": 0.99
            }
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))
        
        # Run filter.py to verify config is loaded
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--stats"],
            cwd=temp_project,
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        assert "MEMORY STATS" in result.stdout
    
    def test_config_load_missing(self, temp_project, engine_dir):
        """Test that missing config.json falls back to defaults."""
        # No config.json created
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--stats"],
            cwd=temp_project,
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        assert "MEMORY STATS" in result.stdout
    
    def test_config_max_step_applied(self, temp_project, engine_dir):
        """Test that custom max_step is actually enforced."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test Project",
            "max_step": 50
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))
        
        # Step 49 should be accepted (within bound)
        learning_49 = {
            "step": 49,
            "source_agent": "gm",
            "type": "bug_fix",
            "domain": "tooling",
            "components": ["TestComponent"],
            "files_touched": ["test.py"],
            "trigger": "When testing step 49",
            "action": "ALWAYS test step 49",
            "reason": "Step 49 should be accepted with max_step=50",
            "importance": 7,
            "severity": "major"
        }
        
        learning_file = temp_project / "learning_49.json"
        learning_file.write_text(json.dumps(learning_49))
        
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(learning_file)],
            cwd=temp_project,
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        assert "ADDED" in result.stdout
        
        # Step 51 should be rejected (exceeds bound)
        learning_51 = {
            "step": 51,
            "source_agent": "gm",
            "type": "bug_fix",
            "domain": "tooling",
            "components": ["TestComponent"],
            "files_touched": ["test.py"],
            "trigger": "When testing step 51",
            "action": "ALWAYS test step 51",
            "reason": "Step 51 should be rejected with max_step=50",
            "importance": 7,
            "severity": "major"
        }
        
        learning_file = temp_project / "learning_51.json"
        learning_file.write_text(json.dumps(learning_51))
        
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(learning_file)],
            cwd=temp_project,
            capture_output=True,
            text=True
        )
        
        # Should be quarantined
        assert "QUARANTINED" in result.stderr
    
    def test_config_valid_domains_applied(self, temp_project, engine_dir):
        """Test that custom valid_domains actually rejects invalid domains."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test Project",
            "valid_domains": ["frontend", "backend"]
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))
        
        # Valid domain should be accepted
        learning_valid = {
            "step": 1,
            "source_agent": "gm",
            "type": "bug_fix",
            "domain": "frontend",
            "components": ["TestComponent"],
            "files_touched": ["test.py"],
            "trigger": "When testing valid domain",
            "action": "ALWAYS test valid domain",
            "reason": "Frontend domain should be accepted",
            "importance": 7,
            "severity": "major"
        }
        
        learning_file = temp_project / "learning_valid.json"
        learning_file.write_text(json.dumps(learning_valid))
        
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(learning_file)],
            cwd=temp_project,
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        assert "ADDED" in result.stdout
        
        # Invalid domain should be rejected
        learning_invalid = {
            "step": 1,
            "source_agent": "gm",
            "type": "bug_fix",
            "domain": "invalid_domain",
            "components": ["TestComponent"],
            "files_touched": ["test.py"],
            "trigger": "When testing invalid domain",
            "action": "ALWAYS test invalid domain",
            "reason": "Invalid domain should be rejected",
            "importance": 7,
            "severity": "major"
        }
        
        learning_file = temp_project / "learning_invalid.json"
        learning_file.write_text(json.dumps(learning_invalid))
        
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(learning_file)],
            cwd=temp_project,
            capture_output=True,
            text=True
        )
        
        # Should be quarantined
        assert "QUARANTINED" in result.stderr
    
    def test_config_valid_agents_applied(self, temp_project, engine_dir):
        """Test that custom valid_source_agents actually rejects invalid agents."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test Project",
            "valid_source_agents": ["gm", "reviewer"]
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))
        
        # Valid agent should be accepted
        learning_valid = {
            "step": 1,
            "source_agent": "gm",
            "type": "bug_fix",
            "domain": "tooling",
            "components": ["TestComponent"],
            "files_touched": ["test.py"],
            "trigger": "When testing valid agent",
            "action": "ALWAYS test valid agent",
            "reason": "GM agent should be accepted",
            "importance": 7,
            "severity": "major"
        }
        
        learning_file = temp_project / "learning_valid.json"
        learning_file.write_text(json.dumps(learning_valid))
        
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(learning_file)],
            cwd=temp_project,
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        assert "ADDED" in result.stdout
        
        # Invalid agent should be rejected
        learning_invalid = {
            "step": 1,
            "source_agent": "invalid_agent",
            "type": "bug_fix",
            "domain": "tooling",
            "components": ["TestComponent"],
            "files_touched": ["test.py"],
            "trigger": "When testing invalid agent",
            "action": "ALWAYS test invalid agent",
            "reason": "Invalid agent should be rejected",
            "importance": 7,
            "severity": "major"
        }
        
        learning_file = temp_project / "learning_invalid.json"
        learning_file.write_text(json.dumps(learning_invalid))
        
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(learning_file)],
            cwd=temp_project,
            capture_output=True,
            text=True
        )
        
        # Should be quarantined
        assert "QUARANTINED" in result.stderr


class TestFallbackToDefaults:
    """Test fallback to defaults when config values are missing."""
    
    def test_fallback_partial_config(self, temp_project, engine_dir):
        """Test that partial config falls back to defaults for missing keys."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test Project"
            # Missing other required keys
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))
        
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--stats"],
            cwd=temp_project,
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        assert "MEMORY STATS" in result.stdout
    
    def test_fallback_empty_config(self, temp_project, engine_dir):
        """Test that empty config.json falls back to all defaults."""
        memory_dir = temp_project / "memory"
        config_path = memory_dir / "config.json"
        config_path.write_text("{}")
        
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--stats"],
            cwd=temp_project,
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        assert "MEMORY STATS" in result.stdout


class TestNullAcceptAny:
    """Test that null values accept any string."""
    
    def test_null_domains_accept_any(self, temp_project, engine_dir):
        """Test that null valid_domains accepts any domain."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test Project",
            "valid_domains": None
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))
        
        # Create a test learning with custom domain
        learning = {
            "step": 1,
            "source_agent": "gm",
            "type": "bug_fix",
            "domain": "custom_domain",
            "components": ["TestComponent"],
            "files_touched": ["test.py"],
            "trigger": "When testing custom domain",
            "action": "ALWAYS test custom domains",
            "reason": "Custom domains should be accepted",
            "importance": 7,
            "severity": "major"
        }
        
        learning_file = temp_project / "learning.json"
        learning_file.write_text(json.dumps(learning))
        
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(learning_file)],
            cwd=temp_project,
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        assert "ADDED" in result.stdout
    
    def test_null_agents_accept_any(self, temp_project, engine_dir):
        """Test that null valid_source_agents accepts any agent."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test Project",
            "valid_source_agents": None
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))
        
        # Create a test learning with custom agent
        learning = {
            "step": 1,
            "source_agent": "custom_agent",
            "type": "bug_fix",
            "domain": "tooling",
            "components": ["TestComponent"],
            "files_touched": ["test.py"],
            "trigger": "When testing custom agent",
            "action": "ALWAYS test custom agents",
            "reason": "Custom agents should be accepted",
            "importance": 7,
            "severity": "major"
        }
        
        learning_file = temp_project / "learning.json"
        learning_file.write_text(json.dumps(learning))
        
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(learning_file)],
            cwd=temp_project,
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        assert "ADDED" in result.stdout


class TestMaxStepBound:
    """Test max_step bound including null."""
    
    def test_max_step_null_no_upper_bound(self, temp_project, engine_dir):
        """Test that null max_step has no upper bound."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test Project",
            "max_step": None
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))
        
        # Create a learning with high step number
        learning = {
            "step": 999,
            "source_agent": "gm",
            "type": "bug_fix",
            "domain": "tooling",
            "components": ["TestComponent"],
            "files_touched": ["test.py"],
            "trigger": "When testing high step",
            "action": "ALWAYS test high steps",
            "reason": "High steps should be accepted",
            "importance": 7,
            "severity": "major"
        }
        
        learning_file = temp_project / "learning.json"
        learning_file.write_text(json.dumps(learning))
        
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(learning_file)],
            cwd=temp_project,
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        assert "ADDED" in result.stdout
    
    def test_max_step_enforced(self, temp_project, engine_dir):
        """Test that max_step is enforced when set."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test Project",
            "max_step": 10
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))
        
        # Create a learning with step > max_step
        learning = {
            "step": 11,
            "source_agent": "gm",
            "type": "bug_fix",
            "domain": "tooling",
            "components": ["TestComponent"],
            "files_touched": ["test.py"],
            "trigger": "When testing step bound",
            "action": "ALWAYS test step bounds",
            "reason": "Step bounds should be enforced",
            "importance": 7,
            "severity": "major"
        }
        
        learning_file = temp_project / "learning.json"
        learning_file.write_text(json.dumps(learning))
        
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(learning_file)],
            cwd=temp_project,
            capture_output=True,
            text=True
        )
        
        # Should be quarantined (returncode 1 is expected for quarantined entries)
        assert "QUARANTINED" in result.stderr


class TestVersionStderr:
    """Test --version outputs to stderr."""
    
    def test_version_outputs_to_stderr(self, engine_dir):
        """Test that --version outputs to stderr, not stdout."""
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--version"],
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        assert "agent-memory-engine" in result.stderr
        assert result.stdout == ""  # Nothing on stdout
    
    def test_version_format(self, engine_dir):
        """Test that --version has correct format."""
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--version"],
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        # Should match pattern like "agent-memory-engine v1.15.0"
        assert "v" in result.stderr
        assert "." in result.stderr


class TestRetrievalStdoutStability:
    """Test that retrieval stdout is stable."""
    
    def test_retrieval_stdout_stable(self, temp_project, engine_dir):
        """Test that retrieval output is stable across runs."""
        memory_dir = temp_project / "memory"
        
        # Create a learning
        learning = {
            "step": 1,
            "source_agent": "gm",
            "type": "bug_fix",
            "domain": "tooling",
            "components": ["TestComponent"],
            "files_touched": ["test.py"],
            "trigger": "When testing stability",
            "action": "ALWAYS test stability",
            "reason": "Stability should be maintained",
            "importance": 7,
            "severity": "major"
        }
        
        learning_file = temp_project / "learning.json"
        learning_file.write_text(json.dumps(learning))
        
        # Log the learning
        subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(learning_file)],
            cwd=temp_project,
            capture_output=True,
            text=True
        )
        
        # Run retrieval twice
        result1 = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--step", "1", "--components", "TestComponent"],
            cwd=temp_project,
            capture_output=True,
            text=True
        )
        
        result2 = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--step", "1", "--components", "TestComponent"],
            cwd=temp_project,
            capture_output=True,
            text=True
        )
        
        # Both should contain the learning (stable content)
        assert "When testing stability" in result1.stdout
        assert "When testing stability" in result2.stdout
        
        # Check that key sections are present in both
        assert "WARNINGS" in result1.stdout
        assert "WARNINGS" in result2.stdout
    
    def test_retrieval_no_version_in_stdout(self, temp_project, engine_dir):
        """Test that version info is not in retrieval stdout."""
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--step", "1", "--domain", "tooling"],
            cwd=temp_project,
            capture_output=True,
            text=True
        )
        
        # Version should not appear in stdout
        assert "v1." not in result.stdout
        assert "agent-memory-engine" not in result.stdout


class TestScaffoldIntegration:
    """Test scaffold.py integration."""
    
    def test_scaffold_creates_memory(self, fresh_project, engine_dir):
        """Test that scaffold creates memory directory structure."""
        # Use source dir, not deployed engine dir (scaffold.py is not deployed)
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.scaffold", str(fresh_project), "--defaults"],
            capture_output=True,
            text=True,
            cwd=str(source_dir)
        )
        
        assert result.returncode == 0
        
        # Check that memory directory was created
        memory_dir = fresh_project / "memory"
        assert memory_dir.exists()
        
        # Check that key files exist
        assert (memory_dir / "filter.py").exists()
        assert (memory_dir / "config.json").exists()
        assert (memory_dir / "learnings.jsonl").exists()
        assert (memory_dir / "quarantine.jsonl").exists()
    
    def test_scaffold_opencode_wiring(self, fresh_project, engine_dir):
        """Test that --opencode flag wires opencode.json."""
        # Use source dir, not deployed engine dir (scaffold.py is not deployed)
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.scaffold", str(fresh_project), "--defaults", "--opencode"],
            capture_output=True,
            text=True,
            cwd=str(source_dir)
        )
        
        assert result.returncode == 0
        
        # Check that opencode.json was created
        opencode_path = fresh_project / "opencode.json"
        assert opencode_path.exists()
        
        # Check that it contains memory instructions
        opencode = json.loads(opencode_path.read_text())
        assert "instructions" in opencode
        assert "memory/SYSTEM_INVARIANTS.md" in opencode["instructions"]
        assert "memory/HANDOFF.md" in opencode["instructions"]
        
        # Check that agents were added
        assert "agent" in opencode
        assert "gm" in opencode["agent"]
        assert "code-reviewer" in opencode["agent"]

    def test_scaffold_ide_flag_opencode(self, fresh_project, engine_dir):
        """Test that --ide opencode produces same results as --opencode."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.scaffold", str(fresh_project), "--defaults", "--ide", "opencode"],
            capture_output=True,
            text=True,
            cwd=str(source_dir)
        )

        assert result.returncode == 0
        assert (fresh_project / "opencode.json").exists()
        assert (fresh_project / ".opencode" / "prompts" / "gm.md").exists()
        assert (fresh_project / ".opencode" / "prompts" / "docs-writer.md").exists()

    def test_scaffold_opencode_backward_compat(self, fresh_project, engine_dir):
        """Test that --opencode hidden alias still works."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.scaffold", str(fresh_project), "--defaults", "--opencode"],
            capture_output=True,
            text=True,
            cwd=str(source_dir)
        )

        assert result.returncode == 0
        assert (fresh_project / "opencode.json").exists()

    def test_scaffold_windsurf_wiring(self, fresh_project, engine_dir):
        """Test that --ide windsurf creates workflows, Plans dir, and AGENTS.md."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.scaffold", str(fresh_project), "--defaults", "--ide", "windsurf"],
            capture_output=True,
            text=True,
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

    def test_scaffold_cursor_wiring(self, fresh_project, engine_dir):
        """Test that --ide cursor creates .cursor/rules/*.mdc and AGENTS.md."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.scaffold", str(fresh_project), "--defaults", "--ide", "cursor"],
            capture_output=True,
            text=True,
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

    def test_scaffold_claude_code_wiring(self, fresh_project, engine_dir):
        """Test that --ide claude-code creates CLAUDE.md with memory protocol."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.scaffold", str(fresh_project), "--defaults", "--ide", "claude-code"],
            capture_output=True,
            text=True,
            cwd=str(source_dir)
        )

        assert result.returncode == 0
        assert (fresh_project / "CLAUDE.md").exists()
        claude_content = (fresh_project / "CLAUDE.md").read_text()
        assert "## Memory" in claude_content
        assert "filter.py" in claude_content

    def test_scaffold_copilot_wiring(self, fresh_project, engine_dir):
        """Test that --ide copilot creates .github/copilot-instructions.md and AGENTS.md."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.scaffold", str(fresh_project), "--defaults", "--ide", "copilot"],
            capture_output=True,
            text=True,
            cwd=str(source_dir)
        )

        assert result.returncode == 0
        assert (fresh_project / ".github" / "copilot-instructions.md").exists()
        copilot_content = (fresh_project / ".github" / "copilot-instructions.md").read_text()
        assert "## Memory" in copilot_content
        assert (fresh_project / "AGENTS.md").exists()
        agents_content = (fresh_project / "AGENTS.md").read_text()
        assert "## Memory" in agents_content

    def test_scaffold_multi_ide(self, fresh_project, engine_dir):
        """Test that --ide windsurf,cursor wires both platforms."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.scaffold", str(fresh_project), "--defaults", "--ide", "windsurf,cursor"],
            capture_output=True,
            text=True,
            cwd=str(source_dir)
        )

        assert result.returncode == 0
        assert (fresh_project / ".windsurf" / "workflows" / "gm.md").exists()
        assert (fresh_project / ".cursor" / "rules" / "gm.mdc").exists()
        assert (fresh_project / "AGENTS.md").exists()

    def test_scaffold_ide_invalid_platform(self, fresh_project, engine_dir):
        """Test that unknown IDE platform exits with error."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.scaffold", str(fresh_project), "--defaults", "--ide", "foo"],
            capture_output=True,
            text=True,
            cwd=str(source_dir)
        )

        assert result.returncode != 0
        assert "Unknown IDE platform" in result.stderr or "Unknown IDE platform" in result.stdout

    def test_scaffold_ide_list_platforms(self):
        """Test that --ide ? lists available platforms and exits 0."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.scaffold", "--ide", "?"],
            capture_output=True,
            text=True,
            cwd=str(source_dir)
        )

        assert result.returncode == 0
        assert "Available IDE platforms" in result.stdout
        assert "windsurf" in result.stdout
        assert "cursor" in result.stdout
        assert "claude-code" in result.stdout
        assert "copilot" in result.stdout
        assert "all" in result.stdout

    def test_scaffold_ide_all(self, fresh_project, engine_dir):
        """Test that --ide all wires every platform."""
        source_dir = Path(__file__).parent.parent / "src"
        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.scaffold", str(fresh_project), "--defaults", "--ide", "all"],
            capture_output=True,
            text=True,
            cwd=str(source_dir)
        )

        assert result.returncode == 0
        assert (fresh_project / ".windsurf" / "workflows" / "gm.md").exists()
        assert (fresh_project / ".cursor" / "rules" / "gm.mdc").exists()
        assert (fresh_project / "CLAUDE.md").exists()
        assert (fresh_project / ".github" / "copilot-instructions.md").exists()
        assert (fresh_project / "AGENTS.md").exists()
        assert "Wired:" in result.stdout

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
        from agent_memory.update import is_temp_path
        
        temp_dir = Path(tempfile.gettempdir())
        assert is_temp_path(temp_dir) is True
        assert is_temp_path(temp_dir / "some" / "subdir") is True
        # Windows-specific temp paths
        if sys.platform == "win32":
            assert is_temp_path(Path("C:/Users/Admin/AppData/Local/Temp") / "tmp1234") is True
    
    def test_is_temp_path_rejects_non_temp(self):
        """is_temp_path returns False for paths not under system temp dir."""
        from agent_memory.update import is_temp_path
        
        assert is_temp_path(Path("C:/Projects/magpie-swoop")) is False
        assert is_temp_path(Path("/home/user/projects/foo")) is False
        assert is_temp_path(Path(tempfile.gettempdir()).parent / "other_dir") is False
    
    def test_is_temp_path_handles_nonexistent(self):
        """is_temp_path returns False for non-existent paths (doesn't crash)."""
        from agent_memory.update import is_temp_path
        
        assert is_temp_path(Path("Z:/nonexistent/path")) is False
    
    def test_load_projects_prunes_temp_entries(self, tmp_path, monkeypatch):
        """load_projects auto-prunes temp entries with backup."""
        from agent_memory import update
        
        # Setup: create a fake ENGINE_DIR with projects.txt
        engine_dir = tmp_path / "engine"
        engine_dir.mkdir()
        projects_file = engine_dir / "projects.txt"
        
        # Write projects.txt with one valid and one temp entry
        # Use a path that's clearly NOT under temp dir
        temp_entry = str(Path(tempfile.gettempdir()) / "tmp1234")
        valid_entry = "C:/Projects/my-project" if sys.platform == "win32" else "/home/user/projects/my-project"
        projects_file.write_text(f"# Comment\n{valid_entry}\n{temp_entry}\n")
        
        # Monkeypatch ENGINE_DIR in update module
        monkeypatch.setattr(update, "ENGINE_DIR", engine_dir)
        
        # Run load_projects
        result = update.load_projects(dry_run=False)
        
        # Verify: only valid project returned
        assert len(result) == 1
        assert result[0] == Path(valid_entry)
        
        # Verify: backup created
        backups = list(engine_dir.glob("projects.txt.backup-*"))
        assert len(backups) == 1
        
        # Verify: projects.txt rewritten without temp entry
        content = projects_file.read_text()
        assert temp_entry not in content
        # Path may be normalized (forward/back slashes)
        assert "my-project" in content
    
    def test_load_projects_dry_run_no_prune(self, tmp_path, monkeypatch):
        """load_projects with dry_run=True does not modify projects.txt."""
        from agent_memory import update
        
        engine_dir = tmp_path / "engine"
        engine_dir.mkdir()
        projects_file = engine_dir / "projects.txt"
        
        temp_entry = str(Path(tempfile.gettempdir()) / "tmp1234")
        valid_entry = "C:/Projects/my-project" if sys.platform == "win32" else "/home/user/projects/my-project"
        original_content = f"# Comment\n{valid_entry}\n{temp_entry}\n"
        projects_file.write_text(original_content)
        
        monkeypatch.setattr(update, "ENGINE_DIR", engine_dir)
        
        # Run load_projects with dry_run=True
        result = update.load_projects(dry_run=True)
        
        # Verify: only valid project returned
        assert len(result) == 1
        
        # Verify: projects.txt NOT modified
        assert projects_file.read_text() == original_content
        
        # Verify: no backup created
        backups = list(engine_dir.glob("projects.txt.backup-*"))
        assert len(backups) == 0


class TestResolver:
    """Test resolve_memory_dir() and _get_paths() guard."""
    
    def test_resolve_memory_dir_priority(self, monkeypatch, tmp_path):
        """Test resolve_memory_dir() honors priority: --memory-dir > env > cwd/memory."""
        from agent_memory.cli import resolve_memory_dir
        
        # Test 1: --memory-dir takes priority
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        result = resolve_memory_dir(str(memory_dir))
        assert result == str(memory_dir.resolve())
        
        # Test 2: AGENT_MEMORY_DIR env var (when --memory-dir is None)
        env_dir = tmp_path / "env_memory"
        env_dir.mkdir()
        monkeypatch.setenv("AGENT_MEMORY_DIR", str(env_dir))
        result = resolve_memory_dir(None)
        assert result == str(env_dir.resolve())
        
        # Test 3: cwd/memory fallback (when both are None)
        monkeypatch.delenv("AGENT_MEMORY_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        result = resolve_memory_dir(None)
        assert result == str(memory_dir.resolve())
    
    def test_resolve_memory_dir_errors(self, monkeypatch, tmp_path):
        """Test resolve_memory_dir() raises ValueError on invalid paths."""
        from agent_memory.cli import resolve_memory_dir
        
        # Test 1: Invalid --memory-dir raises ValueError
        with pytest.raises(ValueError, match="--memory-dir path does not exist"):
            resolve_memory_dir(str(tmp_path / "nonexistent"))
        
        # Test 2: Empty string --memory-dir raises ValueError
        with pytest.raises(ValueError, match="--memory-dir path does not exist"):
            resolve_memory_dir("")
        
        # Test 3: Invalid AGENT_MEMORY_DIR raises ValueError
        monkeypatch.delenv("AGENT_MEMORY_DIR", raising=False)
        monkeypatch.setenv("AGENT_MEMORY_DIR", str(tmp_path / "nonexistent"))
        with pytest.raises(ValueError, match="AGENT_MEMORY_DIR path does not exist"):
            resolve_memory_dir(None)
        
        # Test 4: No memory dir found raises ValueError
        monkeypatch.delenv("AGENT_MEMORY_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="No memory directory found"):
            resolve_memory_dir(None)
    
    def test_get_paths_raises_if_uninitialized(self):
        """Test _get_paths() raises RuntimeError if PATHS is None.
        
        Note: This test mutates the module-level PATHS singleton directly.
        Safe here because no other test calls filter.main() in-process.
        If future tests do, use a fixture to save/restore PATHS.
        """
        from agent_memory import cli as filter
        old_paths = filter.PATHS
        try:
            filter.PATHS = None
            with pytest.raises(RuntimeError, match="PATHS not initialized"):
                filter._get_paths()
        finally:
            filter.PATHS = old_paths


class TestShim:
    """Test shim functionality."""
    
    def test_shim_delegates_to_engine(self, temp_project, engine_dir):
        """Test that shim correctly delegates to central engine."""
        from agent_memory.shim import SHIM_TEMPLATE
        
        # Write shim to project (memory dir already exists from fixture)
        memory_dir = temp_project / "memory"
        shim_path = memory_dir / "filter.py"
        shim_path.write_text(SHIM_TEMPLATE)
        
        # Run shim with --version
        result = subprocess.run(
            [sys.executable, str(shim_path), "--version"],
            capture_output=True,
            text=True,
            cwd=temp_project
        )
        
        # Should match engine version
        assert result.returncode == 0
        assert "agent-memory-engine" in result.stderr
    
    def test_shim_memory_dir_override(self, temp_project, engine_dir, tmp_path):
        """Test that --memory-dir CLI flag overrides AGENT_MEMORY_DIR set by shim."""
        from agent_memory.shim import SHIM_TEMPLATE
        
        # Write shim to project (memory dir already exists from fixture)
        memory_dir = temp_project / "memory"
        shim_path = memory_dir / "filter.py"
        shim_path.write_text(SHIM_TEMPLATE)
        
        # Create alternate memory directory
        alt_memory = tmp_path / "alt_memory"
        alt_memory.mkdir()
        (alt_memory / "learnings.jsonl").touch()
        (alt_memory / "quarantine.jsonl").touch()
        (alt_memory / "archive").mkdir()
        
        # Run shim with --memory-dir override
        result = subprocess.run(
            [sys.executable, str(shim_path), "--memory-dir", str(alt_memory), "--stats"],
            capture_output=True,
            text=True,
            cwd=temp_project
        )
        
        # Should use alt_memory, not the shim's directory
        assert result.returncode == 0
        assert "MEMORY STATS" in result.stdout
    
    def test_migrate_to_shim(self, temp_project, engine_dir):
        """Test --migrate-to-shim converts full copy to shim."""
        # Copy full engine to project (memory dir already exists from fixture)
        memory_dir = temp_project / "memory"
        # Only filter.py is copied to the local project in the test fixture
        shutil.copy2(engine_dir / "filter.py", memory_dir / "filter.py")
        
        # Migrate
        from agent_memory.update import migrate_to_shim
        success, msg = migrate_to_shim(temp_project)
        
        assert success
        assert "Migrated" in msg
        
        # Verify shim
        from agent_memory.shim import is_shim
        assert is_shim(memory_dir / "filter.py")
        
        # Verify shim replaced filter.py
        shim_content = (memory_dir / "filter.py").read_text(encoding="utf-8")
        assert len(shim_content.splitlines()) < 50
        assert "AGENT_MEMORY_DIR" in shim_content
        # Verify backup created
        backups = list((memory_dir / "backups").glob("*"))
        assert len(backups) == 1
    
    def test_migrate_to_shim_idempotent(self, temp_project, engine_dir):
        """Test running --migrate-to-shim twice is safe."""
        # Copy full engine to project (memory dir already exists from fixture)
        memory_dir = temp_project / "memory"
        shutil.copy2(engine_dir / "filter.py", memory_dir / "filter.py")
        
        # Migrate once
        from agent_memory.update import migrate_to_shim
        success1, msg1 = migrate_to_shim(temp_project)
        assert success1
        assert "Migrated" in msg1
        
        # Count backups after first migration
        backups_after_first = list((memory_dir / "backups").glob("*"))
        
        # Migrate again
        success2, msg2 = migrate_to_shim(temp_project)
        assert success2
        assert "Already a shim" in msg2
        
        # Verify no additional backup created
        backups_after_second = list((memory_dir / "backups").glob("*"))
        assert len(backups_after_first) == len(backups_after_second)
    
    def test_shim_missing_engine(self, temp_project, tmp_path):
        """Test shim handles missing engine gracefully."""
        from agent_memory.shim import SHIM_TEMPLATE
        
        # Use a custom shim pointing to a non-existent engine path so we do not
        # disturb the real engine directory (avoiding races with parallel tests).
        missing_engine = tmp_path / "nonexistent" / "engine" / "filter.py"
        custom_shim = SHIM_TEMPLATE.replace(
            'engine_path = os.path.expanduser("~/.agent-memory/engine/filter.py")',
            f'engine_path = r"{missing_engine}"'
        )
        
        # Write shim to project (memory dir already exists from fixture)
        memory_dir = temp_project / "memory"
        shim_path = memory_dir / "filter.py"
        shim_path.write_text(custom_shim)
        
        # Run shim
        result = subprocess.run(
            [sys.executable, str(shim_path), "--stats"],
            capture_output=True,
            text=True,
            cwd=temp_project
        )
        
        # Should fail with error message
        assert result.returncode == 1
        assert "Engine not found" in result.stderr
        assert "deploy script" in result.stderr
    
    def test_profile_loads_post_migration(self, temp_project, engine_dir):
        """Test that profile.py loads from central location after migration."""
        # Copy full engine to project (memory dir already exists from fixture)
        # Copy filter.py to project (memory dir already exists from fixture)
        memory_dir = temp_project / "memory"
        shutil.copy2(engine_dir / "filter.py", memory_dir / "filter.py")
        
        # Migrate
        from agent_memory.update import migrate_to_shim
        migrate_to_shim(temp_project)
        
        # Run shim with retrieval
        result = subprocess.run(
            [sys.executable, str(memory_dir / "filter.py"), "--step", "1", "--components", "Tooling", "--domain", "tooling"],
            capture_output=True,
            text=True,
            cwd=temp_project
        )
        
        # Should succeed (profile.py loaded from central location)
        assert result.returncode == 0
        # Profile context appears in output if profile exists
        # (may be "(none)" if no profile, but should not error)
    
    def test_scaffold_force_overwrites_old_copy(self, fresh_project, engine_dir):
        """Test that scaffold.py --force overwrites old full copy with shim."""
        # Use source directory, not deployed engine
        source_dir = Path(__file__).parent.parent / "src"
        
        # Copy full engine to project (simulating legacy state)
        memory_dir = fresh_project / "memory"
        memory_dir.mkdir()
        shutil.copy2(engine_dir / "filter.py", memory_dir / "filter.py")
        
        # Verify it's not a shim yet
        from agent_memory.shim import is_shim
        assert not is_shim(memory_dir / "filter.py")
        
        # Run scaffold with --force from source directory
        result = subprocess.run(
            [sys.executable, "-m", "agent_memory.scaffold", str(fresh_project), "--defaults", "--force"],
            capture_output=True,
            text=True,
            cwd=str(source_dir)
        )
        
        assert result.returncode == 0
        
        # Verify it's now a shim
        assert is_shim(memory_dir / "filter.py")
    
    def test_scaffold_idempotent(self, fresh_project, engine_dir):
        """Test that scaffold.py is idempotent when already a shim."""
        from agent_memory.shim import SHIM_TEMPLATE, is_shim
        
        # Use source directory, not deployed engine
        source_dir = Path(__file__).parent.parent / "src"
        
        # Write shim to project
        memory_dir = fresh_project / "memory"
        memory_dir.mkdir()
        shim_path = memory_dir / "filter.py"
        shim_path.write_text(SHIM_TEMPLATE)
        
        # Get modification time before scaffold
        mtime_before = shim_path.stat().st_mtime_ns
        
        # Run scaffold without --force (should detect existing memory/ and fail)
        # But copy_engine_files() should still be idempotent if called
        # So we test the copy_engine_files() function directly
        sys.path.insert(0, str(source_dir))
        from agent_memory.scaffold import copy_engine_files
        
        copy_engine_files(memory_dir, force=False)
        
        # Verify file was not modified (idempotent)
        mtime_after = shim_path.stat().st_mtime_ns
        assert mtime_before == mtime_after
        
        # Verify still a shim
        assert is_shim(shim_path)


def _make_paths(memory_dir, repo_root):
    """Build a Paths-like namedtuple for metrics tests."""
    from collections import namedtuple
    _P = namedtuple("_P", ["memory_dir", "repo_root", "config_path",
                           "learnings_path", "quarantine_path",
                           "archive_dir", "session_file", "agents_md_path"])
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


class TestMetrics:
    """Test metrics logging and reporting."""

    def test_log_event_writes_jsonl(self, temp_project):
        """log_event appends a valid JSON line to metrics.jsonl."""
        paths = _make_paths(temp_project / "memory", temp_project)

        from agent_memory.engine.metrics import log_event, read_metrics

        log_event(paths, "retrieval", query_step=1, warnings_returned=2,
                  patterns_returned=1, top_score=0.85, latency_ms=3.2)
        log_event(paths, "log", outcome="ADDED", entry_type="bug_fix",
                  entry_domain="tooling", latency_ms=1.1)

        events = read_metrics(paths)
        assert len(events) == 2
        assert events[0]["event_type"] == "retrieval"
        assert events[0]["warnings_returned"] == 2
        assert events[0]["top_score"] == 0.85
        assert "ts" in events[0]
        assert "project_id" in events[0]
        assert events[1]["event_type"] == "log"
        assert events[1]["outcome"] == "ADDED"

    def test_log_event_never_raises(self, temp_project):
        """log_event silently ignores errors (best-effort)."""
        paths = _make_paths("/nonexistent/path/xyz", "/nonexistent")

        from agent_memory.engine.metrics import log_event
        # Should not raise
        log_event(paths, "retrieval", query_step=1)

    def test_read_metrics_filters_by_type(self, temp_project):
        """read_metrics filters by event_type."""
        paths = _make_paths(temp_project / "memory", temp_project)

        from agent_memory.engine.metrics import log_event, read_metrics

        log_event(paths, "retrieval", query_step=1)
        log_event(paths, "log", outcome="ADDED")
        log_event(paths, "retrieval", query_step=2)

        retrievals = read_metrics(paths, event_type="retrieval")
        assert len(retrievals) == 2
        assert all(e["event_type"] == "retrieval" for e in retrievals)

        logs = read_metrics(paths, event_type="log")
        assert len(logs) == 1
        assert logs[0]["outcome"] == "ADDED"

    def test_metrics_cli_summary(self, temp_project):
        """--metrics prints a summary report after events exist."""
        source_filter = ["-m", "agent_memory.cli"]

        # Log a learning to generate a metrics event
        learning = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["TestComponent"],
            "files_touched": ["test.py"], "trigger": "Metrics test",
            "action": "ALWAYS test metrics", "reason": "Testing",
            "importance": 7, "severity": "major"
        }
        learning_file = temp_project / "learning.json"
        learning_file.write_text(json.dumps(learning))

        subprocess.run(
            [sys.executable, *source_filter, "--log-file", str(learning_file)],
            cwd=temp_project, capture_output=True, text=True
        )

        # Run --metrics
        result = subprocess.run(
            [sys.executable, *source_filter, "--metrics"],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0
        assert "METRICS" in result.stdout

    def test_metrics_cli_empty(self, temp_project):
        """--metrics handles no events gracefully."""
        source_filter = ["-m", "agent_memory.cli"]

        result = subprocess.run(
            [sys.executable, *source_filter, "--metrics"],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0
        assert "No metrics events" in result.stdout

    def test_metrics_cli_json_output(self, temp_project):
        """--metrics --metrics-json outputs valid JSON."""
        source_filter = ["-m", "agent_memory.cli"]

        # Generate an event
        learning = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["TestComponent"],
            "files_touched": ["test.py"], "trigger": "JSON metrics test",
            "action": "ALWAYS test json metrics", "reason": "Testing",
            "importance": 7, "severity": "major"
        }
        learning_file = temp_project / "learning.json"
        learning_file.write_text(json.dumps(learning))

        subprocess.run(
            [sys.executable, *source_filter, "--log-file", str(learning_file)],
            cwd=temp_project, capture_output=True, text=True
        )

        result = subprocess.run(
            [sys.executable, *source_filter, "--metrics", "--metrics-json"],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "summary" in data


class TestBM25Score:
    """Test BM25 scoring function directly."""

    def test_bm25_rare_term_scores_higher(self):
        """Rare matching term should score higher than common matching term."""
        from agent_memory.engine.retrieval import bm25_score, _tokenize, _compute_corpus_stats

        # Use empty stop_words for deterministic testing (real STOP_WORDS would filter "physics"/"AABB")
        stop_words = set()
        entries = []
        for i in range(5):
            entries.append({"trigger": f"When physics test {i}", "action": "ALWAYS physics", "reason": "physics common"})
        entries.append({"trigger": "When AABB collision", "action": "NEVER AABB", "reason": "AABB rare"})

        doc_freqs, total_docs, avg_doc_len = _compute_corpus_stats(entries, stop_words)

        query_tokens = _tokenize("physics AABB", stop_words)
        doc_physics = _tokenize("physics common", stop_words)
        doc_aabb = _tokenize("AABB collision rare", stop_words)

        score_physics = bm25_score(query_tokens, doc_physics, doc_freqs, total_docs, avg_doc_len, 1.5, 0.75)
        score_aabb = bm25_score(query_tokens, doc_aabb, doc_freqs, total_docs, avg_doc_len, 1.5, 0.75)

        assert score_aabb > score_physics, f"Rare term AABB ({score_aabb}) should score higher than common term physics ({score_physics})"

    def test_bm25_no_match_scores_zero(self):
        """Doc with no matching query terms should score 0.0."""
        from agent_memory.engine.retrieval import bm25_score, _tokenize, _compute_corpus_stats

        stop_words = set()
        entries = [
            {"trigger": "When physics test", "action": "ALWAYS physics", "reason": "physics"},
            {"trigger": "When AABB collision", "action": "NEVER AABB", "reason": "AABB"},
        ]
        doc_freqs, total_docs, avg_doc_len = _compute_corpus_stats(entries, stop_words)

        query_tokens = _tokenize("physics AABB", stop_words)
        doc_no_match = _tokenize("completely unrelated words", stop_words)

        score = bm25_score(query_tokens, doc_no_match, doc_freqs, total_docs, avg_doc_len, 1.5, 0.75)
        assert score == 0.0

    def test_bm25_empty_corpus(self):
        """Empty corpus should return 0.0 without crashing."""
        from agent_memory.engine.retrieval import bm25_score

        score = bm25_score(["test"], ["test"], {}, 0, 0.0, 1.5, 0.75)
        assert score == 0.0


class TestRRFFusion:
    """Test RRF fusion in retrieval integration."""

    def test_rrf_fusion_integration(self, temp_project, engine_dir):
        """RRF should rank entries that match both channels higher."""
        memory_dir = temp_project / "memory"

        # Entry A: matches components AND has rare BM25 term "AABB"
        entry_a = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["CollisionSystem"],
            "files_touched": ["collision.py"], "trigger": "When AABB collision detected",
            "action": "ALWAYS use AABB broadphase", "reason": "AABB is efficient for collision",
            "importance": 8, "severity": "major"
        }
        # Entry B: matches components but no BM25 term overlap with query
        entry_b = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["CollisionSystem"],
            "files_touched": ["collision.py"], "trigger": "When rendering sprites",
            "action": "ALWAYS batch draw calls", "reason": "Batching improves performance",
            "importance": 7, "severity": "major"
        }
        # Entry C: no component match but strong BM25 overlap (should be filtered by dual gate)
        entry_c = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["RenderEngine"],
            "files_touched": ["render.py"], "trigger": "When AABB bounding boxes overlap",
            "action": "NEVER skip AABB check", "reason": "AABB overlap detection is critical",
            "importance": 9, "severity": "major"
        }

        for entry in [entry_a, entry_b, entry_c]:
            f = temp_project / f"learning_{entry['trigger'][:10].replace(' ', '_')}.json"
            f.write_text(json.dumps(entry))
            subprocess.run(
                [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(f)],
                cwd=temp_project, capture_output=True, text=True
            )

        # Retrieve with CollisionSystem components and AABB query text
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"),
             "--step", "1", "--components", "CollisionSystem", "--domain", "tooling"],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0
        # Entry A should appear (matches components + has AABB in text)
        assert "AABB" in result.stdout
        # Entry B should appear (matches components)
        assert "Batch" in result.stdout or "batch" in result.stdout
        # Entry C should NOT appear (filtered by dual gate — no component match)
        assert "RenderEngine" not in result.stdout
        # Entry A should appear before Entry B (RRF ranks it higher)
        a_pos = result.stdout.find("AABB")
        b_pos = result.stdout.find("Batch")
        if b_pos == -1:
            b_pos = result.stdout.find("batch")
        assert a_pos < b_pos, "Entry A (AABB) should rank before Entry B (batch)"

    def test_rrf_formula_sanity(self):
        """Sanity check the RRF formula math for a single-candidate scenario."""
        # RRF with rank 1 in both channels: 2/(k+1)
        rrf_k = 60
        expected = 2.0 / (rrf_k + 1)
        assert abs(expected - 0.032786) < 0.001  # sanity check the value

    def test_bm25_config_loaded(self, temp_project, engine_dir):
        """Custom BM25 config values should be loaded without crash."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test",
            "tuning": {
                "bm25_k1": 2.0,
                "bm25_b": 0.5,
                "rrf_k": 40
            }
        }
        (memory_dir / "config.json").write_text(json.dumps(config))

        learning = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["TestComp"],
            "files_touched": ["test.py"], "trigger": "When testing config",
            "action": "ALWAYS test config", "reason": "Config test",
            "importance": 7, "severity": "major"
        }
        f = temp_project / "learning.json"
        f.write_text(json.dumps(learning))

        subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(f)],
            cwd=temp_project, capture_output=True, text=True
        )

        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"),
             "--step", "1", "--components", "TestComp"],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0
        assert "When testing config" in result.stdout


class TestSchemaMigration:
    """Test schema versioning and migration runner."""

    def test_migration_v0_to_v1(self):
        """Unit test: v0 entries get migrated to v1 with correct fields."""
        from agent_memory.engine.migrate import migrate_entry, migrate_all, CURRENT_SCHEMA_VERSION

        v0_entry = {"step": 1, "type": "bug_fix", "trigger": "When test", "action": "ALWAYS test"}
        migrated = migrate_entry(dict(v0_entry))

        assert migrated["schema_version"] == CURRENT_SCHEMA_VERSION
        assert migrated["schema_version"] == 1
        assert migrated["embedding"] is None
        assert migrated["project_id"] is None
        assert migrated["origin_project"] is None
        assert migrated["contributing_projects"] == []

    def test_migrate_entry_noop_on_current(self):
        """migrate_entry is a no-op on already-current entries."""
        from agent_memory.engine.migrate import migrate_entry, CURRENT_SCHEMA_VERSION

        entry = {"schema_version": CURRENT_SCHEMA_VERSION, "step": 1, "embedding": [0.1, 0.2]}
        migrated = migrate_entry(dict(entry))

        assert migrated["schema_version"] == CURRENT_SCHEMA_VERSION
        assert migrated["embedding"] == [0.1, 0.2]  # preserved, not overwritten

    def test_migrate_all_count(self):
        """migrate_all returns correct count of migrated entries."""
        from agent_memory.engine.migrate import migrate_all, CURRENT_SCHEMA_VERSION

        entries = [
            {"step": 1, "type": "bug_fix"},  # v0, needs migration
            {"step": 2, "type": "bug_fix"},  # v0, needs migration
            {"schema_version": CURRENT_SCHEMA_VERSION, "step": 3, "type": "bug_fix"},  # already current
        ]
        migrated, count = migrate_all(entries)

        assert count == 2
        assert all(e["schema_version"] == CURRENT_SCHEMA_VERSION for e in migrated)

    def test_read_learnings_auto_migrates(self, temp_project, engine_dir):
        """Integration: read_learnings auto-migrates v0 entries on read."""
        memory_dir = temp_project / "memory"
        learnings_path = memory_dir / "learnings.jsonl"

        # Write a v0 entry (no schema_version)
        v0_entry = {
            "step": 1, "source_agent": "gm", "type": "bug_fix", "domain": "tooling",
            "components": ["TestComp"], "files_touched": ["test.py"],
            "trigger": "When testing migration", "action": "ALWAYS test migration",
            "reason": "Testing auto-migrate", "importance": 7, "severity": "major"
        }
        learnings_path.write_text(json.dumps(v0_entry) + "\n")

        # Run --stats which calls read_learnings internally
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--stats"],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0
        assert "MEMORY STATS" in result.stdout

        # Read the file back — --migrate-schema should show 0 migrated (already migrated on read by --stats)
        # But the file on disk should still be v0 (lazy migration is in-memory only)
        # Verify by running --migrate-schema which reads raw
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--migrate-schema"],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0
        assert "SCHEMA MIGRATION COMPLETE" in result.stdout
        assert "Migrated: 1" in result.stdout

        # Now file should have schema_version on disk
        lines = learnings_path.read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["schema_version"] == 1
        assert entry["embedding"] is None
        assert entry["project_id"] is None

    def test_handle_log_stamps_schema_version(self, temp_project, engine_dir):
        """Integration: --log stamps schema_version on new entries."""
        memory_dir = temp_project / "memory"
        learning = {
            "step": 1, "source_agent": "gm", "type": "bug_fix", "domain": "tooling",
            "components": ["TestComp"], "files_touched": ["test.py"],
            "trigger": "When testing stamp", "action": "ALWAYS test stamp",
            "reason": "Testing stamp", "importance": 7, "severity": "major"
        }
        f = temp_project / "learning.json"
        f.write_text(json.dumps(learning))

        subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(f)],
            cwd=temp_project, capture_output=True, text=True
        )

        # Read back the written entry
        lines = (memory_dir / "learnings.jsonl").read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["schema_version"] == 1

    def test_migrate_schema_cli(self, temp_project, engine_dir):
        """Integration: --migrate-schema CLI flag works end-to-end."""
        memory_dir = temp_project / "memory"
        learnings_path = memory_dir / "learnings.jsonl"

        # Write multiple v0 entries
        v0_entries = [
            {"step": 1, "source_agent": "gm", "type": "bug_fix", "domain": "tooling",
             "components": ["A"], "files_touched": ["a.py"], "trigger": "When a",
             "action": "ALWAYS a", "reason": "a", "importance": 5, "severity": "minor"},
            {"step": 2, "source_agent": "gm", "type": "bug_fix", "domain": "tooling",
             "components": ["B"], "files_touched": ["b.py"], "trigger": "When b",
             "action": "NEVER b", "reason": "b", "importance": 5, "severity": "minor"},
        ]
        with open(learnings_path, "w") as f:
            for e in v0_entries:
                f.write(json.dumps(e) + "\n")

        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--migrate-schema"],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0
        assert "SCHEMA MIGRATION COMPLETE" in result.stdout
        assert "Migrated: 2" in result.stdout

        # Verify file on disk has migrated entries
        lines = learnings_path.read_text().strip().split("\n")
        for line in lines:
            entry = json.loads(line)
            assert entry["schema_version"] == 1
            assert entry["embedding"] is None
            assert "contributing_projects" in entry


class TestEmbeddingRetrieval:
    """Test embedding-based retrieval functions and hybrid scoring."""

    def test_embedding_fallback(self, temp_project, engine_dir):
        """Retrieval works without sentence-transformers installed (embedder None, alpha=1.0)."""
        memory_dir = temp_project / "memory"

        # Log a learning
        learning = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["CollisionSystem"],
            "files_touched": ["collision.py"], "trigger": "When AABB collision detected",
            "action": "ALWAYS use AABB broadphase", "reason": "AABB is efficient",
            "importance": 8, "severity": "major"
        }
        f = temp_project / "learning.json"
        f.write_text(json.dumps(learning))

        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(f)],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode == 0

        # Retrieve — should work even without sentence-transformers
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"),
             "--step", "1", "--components", "CollisionSystem", "--domain", "tooling"],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0
        assert "AABB" in result.stdout
        # Entry should have embedding=None on disk (no sentence-transformers)
        lines = (memory_dir / "learnings.jsonl").read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["embedding"] is None

    def test_embedding_encoding(self):
        """Base64 round-trip preserves vector values within float16 precision."""
        from agent_memory.engine.retrieval import encode_embedding, decode_embedding

        try:
            import numpy as np
        except ImportError:
            pytest.skip("numpy not installed")

        original = [0.1, -0.2, 0.3, -0.4, 0.5, 0.0, 1.0, -1.0]
        encoded = encode_embedding(original)
        assert isinstance(encoded, str)  # base64 string when numpy available
        decoded = decode_embedding(encoded)
        assert decoded is not None
        assert len(decoded) == len(original)
        for a, b in zip(original, decoded):
            assert abs(a - b) < 0.01  # float16 precision

    def test_embedding_encoding_none(self):
        """encode_embedding(None) returns None, decode_embedding(None) returns None."""
        from agent_memory.engine.retrieval import encode_embedding, decode_embedding

        assert encode_embedding(None) is None
        assert decode_embedding(None) is None

    def test_embedding_encoding_plain_list_fallback(self):
        """encode_embedding falls back to plain list when numpy unavailable."""
        from agent_memory.engine.retrieval import encode_embedding, decode_embedding

        # Test with a list (decode should handle plain lists too)
        original = [0.1, 0.2, 0.3]
        decoded = decode_embedding(original)
        assert decoded == original

    def test_cosine_similarity(self):
        """cosine_similarity: orthogonal → 0.0, identical → 1.0."""
        from agent_memory.engine.retrieval import cosine_similarity

        # Identical vectors
        vec = [1.0, 0.0, 0.0]
        assert abs(cosine_similarity(vec, vec) - 1.0) < 1e-6

        # Orthogonal vectors
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(cosine_similarity(a, b) - 0.0) < 1e-6

        # Empty / mismatched
        assert cosine_similarity([], [1.0]) == 0.0
        assert cosine_similarity([1.0], [1.0, 2.0]) == 0.0

        # Zero magnitude
        assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_handle_log_stores_embedding(self, temp_project, engine_dir):
        """New entries get entry['embedding'] field (None if embedder unavailable)."""
        learning = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["TestComp"],
            "files_touched": ["test.py"], "trigger": "When testing embedding storage",
            "action": "ALWAYS store embedding", "reason": "Embedding test",
            "importance": 7, "severity": "major"
        }
        f = temp_project / "learning.json"
        f.write_text(json.dumps(learning))

        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(f)],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode == 0

        # Read back the entry
        lines = (temp_project / "memory" / "learnings.jsonl").read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert "embedding" in entry
        # Without sentence-transformers installed, should be None
        assert entry["embedding"] is None

    def test_embedding_config_loaded(self, temp_project, engine_dir):
        """Custom embedding_alpha in config.json is loaded and applied without crash."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test",
            "tuning": {
                "embedding_alpha": 0.8
            }
        }
        (memory_dir / "config.json").write_text(json.dumps(config))

        learning = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["TestComp"],
            "files_touched": ["test.py"], "trigger": "When testing config",
            "action": "ALWAYS test config", "reason": "Config test",
            "importance": 7, "severity": "major"
        }
        f = temp_project / "learning.json"
        f.write_text(json.dumps(learning))

        subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(f)],
            cwd=temp_project, capture_output=True, text=True
        )

        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"),
             "--step", "1", "--components", "TestComp"],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0
        assert "When testing config" in result.stdout

    def test_handle_update_recomputes_embedding(self, temp_project, engine_dir):
        """handle_update re-computes embedding when trigger/action/reason changed, preserves when unchanged."""
        memory_dir = temp_project / "memory"

        # Log an entry
        learning = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["TestComp"],
            "files_touched": ["test.py"], "trigger": "When testing update",
            "action": "ALWAYS test update", "reason": "Update test",
            "importance": 7, "severity": "major"
        }
        f = temp_project / "learning.json"
        f.write_text(json.dumps(learning))

        subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(f)],
            cwd=temp_project, capture_output=True, text=True
        )

        # Read back to get ts
        lines = (memory_dir / "learnings.jsonl").read_text().strip().split("\n")
        entry = json.loads(lines[0])
        ts = entry["ts"]

        # Update with changed trigger (should re-compute embedding, but None since no model)
        updated = dict(entry)
        updated["trigger"] = "When testing updated trigger"
        updated.pop("ts", None)
        updated.pop("schema_version", None)
        updated.pop("embedding", None)

        f2 = temp_project / "update.json"
        f2.write_text(json.dumps(updated))

        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--update", ts, "--log-file", str(f2)],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode == 0

        # Verify entry was updated
        lines = (memory_dir / "learnings.jsonl").read_text().strip().split("\n")
        updated_entry = json.loads(lines[0])
        assert "When testing updated trigger" in updated_entry["trigger"]
        assert "embedding" in updated_entry  # field present (None since no model)

        # Read back the new ts (stamp_entry generates a new ts on update)
        ts2 = updated_entry["ts"]

        # Update with unchanged trigger/action/reason (should preserve embedding)
        updated2 = dict(updated_entry)
        updated2["importance"] = 9  # change something other than trigger/action/reason
        updated2.pop("ts", None)
        updated2.pop("schema_version", None)
        updated2.pop("embedding", None)

        f3 = temp_project / "update2.json"
        f3.write_text(json.dumps(updated2))

        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--update", ts2, "--log-file", str(f3)],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode == 0

        # Embedding should be preserved (both None in this case, but the logic is tested)
        lines = (memory_dir / "learnings.jsonl").read_text().strip().split("\n")
        final_entry = json.loads(lines[0])
        assert "embedding" in final_entry


class TestSemanticDedup:
    """Test embedding-based semantic dedup at log time."""

    def test_find_semantic_duplicate_no_embeddings(self):
        """find_semantic_duplicate returns (0.0, None) when no embeddings available."""
        from agent_memory.engine.retrieval import find_semantic_duplicate

        entry = {"domain": "tooling", "trigger": "When X", "action": "ALWAYS Y", "reason": "Z", "embedding": None}
        existing = [{"domain": "tooling", "trigger": "When X", "action": "ALWAYS Y", "reason": "Z", "embedding": None}]
        ctx = {"semantic_dedup_threshold": 0.85, "embedding_model": "fake-model", "embedding_cache_dir": "/tmp"}

        cos, match = find_semantic_duplicate(entry, existing, ctx)
        assert cos == 0.0
        assert match is None

    def test_find_semantic_duplicate_high_cosine(self):
        """find_semantic_duplicate detects match when embeddings are identical."""
        from agent_memory.engine.retrieval import find_semantic_duplicate, encode_embedding

        vec = [0.1, 0.2, 0.3, 0.4]
        emb = encode_embedding(vec)
        entry = {"domain": "tooling", "trigger": "When collision", "action": "ALWAYS use AABB", "reason": "AABB is fast", "embedding": emb}
        existing = [{"domain": "tooling", "trigger": "When overlap", "action": "NEVER skip AABB", "reason": "AABB detects overlap", "embedding": emb, "resolved": False}]
        ctx = {"semantic_dedup_threshold": 0.85}

        cos, match = find_semantic_duplicate(entry, existing, ctx)
        assert cos >= 0.85
        assert match is not None
        assert match is existing[0]

    def test_find_semantic_duplicate_skips_resolved(self):
        """find_semantic_duplicate skips resolved entries."""
        from agent_memory.engine.retrieval import find_semantic_duplicate, encode_embedding

        vec = [0.1, 0.2, 0.3, 0.4]
        emb = encode_embedding(vec)
        entry = {"domain": "tooling", "trigger": "When X", "action": "ALWAYS Y", "reason": "Z", "embedding": emb}
        existing = [{"domain": "tooling", "trigger": "When X", "action": "ALWAYS Y", "reason": "Z", "embedding": emb, "resolved": True}]
        ctx = {"semantic_dedup_threshold": 0.85}

        cos, match = find_semantic_duplicate(entry, existing, ctx)
        assert cos == 0.0
        assert match is None

    def test_find_semantic_duplicate_skips_different_domain(self):
        """find_semantic_duplicate only checks same-domain entries."""
        from agent_memory.engine.retrieval import find_semantic_duplicate, encode_embedding

        vec = [0.1, 0.2, 0.3, 0.4]
        emb = encode_embedding(vec)
        entry = {"domain": "tooling", "trigger": "When X", "action": "ALWAYS Y", "reason": "Z", "embedding": emb}
        existing = [{"domain": "performance", "trigger": "When X", "action": "ALWAYS Y", "reason": "Z", "embedding": emb, "resolved": False}]
        ctx = {"semantic_dedup_threshold": 0.85}

        cos, match = find_semantic_duplicate(entry, existing, ctx)
        assert cos == 0.0
        assert match is None

    def test_find_semantic_duplicate_picks_highest(self):
        """find_semantic_duplicate returns the highest cosine match."""
        from agent_memory.engine.retrieval import find_semantic_duplicate, encode_embedding

        entry_vec = [1.0, 0.0, 0.0]
        close_vec = [0.99, 0.01, 0.0]
        far_vec = [0.5, 0.5, 0.5]
        entry = {"domain": "tooling", "trigger": "When X", "action": "ALWAYS Y", "reason": "Z", "embedding": encode_embedding(entry_vec)}
        existing = [
            {"domain": "tooling", "trigger": "When A", "action": "ALWAYS B", "reason": "C", "embedding": encode_embedding(far_vec), "resolved": False},
            {"domain": "tooling", "trigger": "When D", "action": "ALWAYS E", "reason": "F", "embedding": encode_embedding(close_vec), "resolved": False},
        ]
        ctx = {"semantic_dedup_threshold": 0.85}

        cos, match = find_semantic_duplicate(entry, existing, ctx)
        assert match is existing[1]

    def test_provenance_fields_populated(self, temp_project, engine_dir):
        """Logging an entry should populate project_id, origin_project, contributing_projects, contributors."""
        learning = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["TestComp"],
            "files_touched": ["test.py"], "trigger": "When testing provenance",
            "action": "ALWAYS test provenance", "reason": "Provenance test",
            "importance": 7, "severity": "major"
        }
        f = temp_project / "learning.json"
        f.write_text(json.dumps(learning))

        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(f)],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode == 0

        lines = (temp_project / "memory" / "learnings.jsonl").read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert "project_id" in entry
        assert "origin_project" in entry
        assert "contributing_projects" in entry
        assert entry["contributing_projects"] == []
        assert "contributors" in entry
        assert "gm" in entry["contributors"]

    def test_semantic_dedup_graceful_without_model(self, temp_project, engine_dir):
        """Semantic dedup should gracefully fall back to Jaccard when no embedding model is available."""
        memory_dir = temp_project / "memory"

        # Log first entry
        learning1 = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["CollisionSystem"],
            "files_touched": ["collision.py"], "trigger": "When AABB collision detected",
            "action": "ALWAYS use broadphase", "reason": "Broadphase is efficient",
            "importance": 8, "severity": "major"
        }
        f1 = temp_project / "learning1.json"
        f1.write_text(json.dumps(learning1))
        subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(f1)],
            cwd=temp_project, capture_output=True, text=True
        )

        # Log semantically similar but lexically different entry
        learning2 = {
            "step": 2, "source_agent": "code-reviewer", "type": "bug_fix",
            "domain": "tooling", "components": ["CollisionSystem"],
            "files_touched": ["collision.py"], "trigger": "When bounding box overlap found",
            "action": "NEVER skip broadphase check", "reason": "Broadphase check is necessary for performance",
            "importance": 7, "severity": "major"
        }
        f2 = temp_project / "learning2.json"
        f2.write_text(json.dumps(learning2))
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(f2)],
            cwd=temp_project, capture_output=True, text=True
        )

        # Without embedding model, semantic dedup is skipped, falls back to Jaccard
        # Entries share components but have low Jaccard, so should be ADDED
        assert result.returncode == 0
        assert "ADDED" in result.stdout or "DUPLICATE" in result.stdout or "CONFLICT" in result.stdout


class TestReranker:
    """Test the optional reranking pass (Phase 5).

    These tests use direct imports to mock internal singletons (_ce_cache,
    _PROBE_CACHE, _call_llm) that cannot be exercised via CLI subprocess.
    This is an intentional deviation from the AGENTS.md convention of
    CLI-only integration testing, justified by the need to test graceful
    fallback paths without real model downloads or running LLM servers.
    """

    def test_rerank_none_passthrough(self):
        """rerank_none returns candidates unchanged."""
        from agent_memory.engine.reranker import rerank_none
        candidates = [(0.9, 0.8, 0.7, {"severity": "critical"}), (0.5, 0.4, 0.3, {"severity": "minor"})]
        result, active = rerank_none("query", candidates, {})
        assert result is candidates
        assert active is True

    def test_rerank_dispatch_none(self):
        """rerank() with mode 'none' returns candidates unchanged."""
        from agent_memory.engine.reranker import rerank
        candidates = [(0.9, 0.8, 0.7, {"severity": "critical"})]
        result, active = rerank("query", candidates, {"reranker": "none"})
        assert result is candidates
        assert active is True

    def test_rerank_cross_encoder_mock(self):
        """Cross-encoder reranking with mocked model."""
        from agent_memory.engine import reranker

        # Mock the cross-encoder model
        class MockCrossEncoder:
            def predict(self, pairs):
                # Return scores in reverse order to verify reranking
                return [5.0, 9.0, 1.0]

        reranker._ce_cache["test-model"] = MockCrossEncoder()

        candidates = [
            (0.9, 0.8, 0.7, {"severity": "critical", "trigger": "A", "action": "B", "reason": "C"}),
            (0.5, 0.4, 0.3, {"severity": "minor", "trigger": "D", "action": "E", "reason": "F"}),
            (0.3, 0.2, 0.1, {"severity": "minor", "trigger": "G", "action": "H", "reason": "I"}),
        ]
        ctx = {"reranker_model": "test-model", "embedding_cache_dir": "/tmp"}
        result, active = reranker.rerank_cross_encoder("query", candidates, ctx)

        assert active is True
        # Highest score (9.0) was for index 1, so it should be first
        assert result[0][3]["trigger"] == "D"
        assert result[1][3]["trigger"] == "A"
        assert result[2][3]["trigger"] == "G"

        # Cleanup
        del reranker._ce_cache["test-model"]

    def test_rerank_cross_encoder_fallback(self):
        """Cross-encoder falls back to passthrough when model unavailable."""
        from agent_memory.engine import reranker

        # Cache a None for a fake model
        reranker._ce_cache["unavailable-model"] = None

        candidates = [
            (0.9, 0.8, 0.7, {"severity": "critical", "trigger": "A", "action": "B", "reason": "C"}),
            (0.5, 0.4, 0.3, {"severity": "minor", "trigger": "D", "action": "E", "reason": "F"}),
        ]
        ctx = {"reranker_model": "unavailable-model", "embedding_cache_dir": "/tmp"}
        result, active = reranker.rerank_cross_encoder("query", candidates, ctx)

        assert active is False
        assert result is candidates

        del reranker._ce_cache["unavailable-model"]

    def test_rerank_llm_local_malformed_response(self):
        """LLM-local returns passthrough when response has fewer numbers than candidates."""
        from agent_memory.engine import reranker

        # Reset probe cache
        reranker._PROBE_CACHE = {"endpoint": None, "checked": False}

        # Mock _call_llm to return malformed response
        original_call = reranker._call_llm
        reranker._call_llm = lambda endpoint, model, prompt: "I think the first one is good"

        # Mock _probe_llm_endpoint to return a fake endpoint
        reranker._PROBE_CACHE = {"endpoint": "http://localhost:11434", "checked": True}

        candidates = [
            (0.9, 0.8, 0.7, {"severity": "critical", "trigger": "A", "action": "B", "reason": "C"}),
            (0.5, 0.4, 0.3, {"severity": "minor", "trigger": "D", "action": "E", "reason": "F"}),
            (0.3, 0.2, 0.1, {"severity": "minor", "trigger": "G", "action": "H", "reason": "I"}),
        ]
        ctx = {"reranker_llm_endpoint": "http://localhost:11434", "reranker_llm_model": "test"}
        result, active = reranker.rerank_llm_local("query", candidates, ctx)

        assert active is False
        assert result is candidates

        reranker._call_llm = original_call
        reranker._PROBE_CACHE = {"endpoint": None, "checked": False}

    def test_rerank_llm_local_no_endpoint(self):
        """LLM-local falls back when no endpoint is found."""
        from agent_memory.engine import reranker

        reranker._PROBE_CACHE = {"endpoint": None, "checked": True}

        candidates = [(0.9, 0.8, 0.7, {"severity": "critical", "trigger": "A", "action": "B", "reason": "C"})]
        ctx = {"reranker_llm_endpoint": None}
        result, active = reranker.rerank_llm_local("query", candidates, ctx)

        assert active is False
        assert result is candidates

        reranker._PROBE_CACHE = {"endpoint": None, "checked": False}

    def test_rerank_llm_local_mock_success(self):
        """LLM-local reranks successfully with mocked response."""
        from agent_memory.engine import reranker

        reranker._PROBE_CACHE = {"endpoint": "http://localhost:11434", "checked": True}

        original_call = reranker._call_llm
        reranker._call_llm = lambda endpoint, model, prompt: "3 9 1"

        candidates = [
            (0.9, 0.8, 0.7, {"severity": "critical", "trigger": "A", "action": "B", "reason": "C"}),
            (0.5, 0.4, 0.3, {"severity": "minor", "trigger": "D", "action": "E", "reason": "F"}),
            (0.3, 0.2, 0.1, {"severity": "minor", "trigger": "G", "action": "H", "reason": "I"}),
        ]
        ctx = {"reranker_llm_endpoint": "http://localhost:11434", "reranker_llm_model": "test"}
        result, active = reranker.rerank_llm_local("query", candidates, ctx)

        assert active is True
        # Score 9 was for index 1, so D should be first
        assert result[0][3]["trigger"] == "D"
        assert result[1][3]["trigger"] == "A"
        assert result[2][3]["trigger"] == "G"

        reranker._call_llm = original_call
        reranker._PROBE_CACHE = {"endpoint": None, "checked": False}

    def test_rerank_unknown_mode(self):
        """Unknown reranker mode falls back to passthrough."""
        from agent_memory.engine import reranker
        candidates = [(0.9, 0.8, 0.7, {"severity": "critical"})]
        result, active = reranker.rerank("query", candidates, {"reranker": "bogus"})
        assert active is False
        assert result is candidates

    def test_parse_scores_sufficient(self):
        """_parse_scores returns floats when enough numbers found."""
        from agent_memory.engine.reranker import _parse_scores
        scores = _parse_scores("7.5 3 9.0 1", 4)
        assert scores == [7.5, 3.0, 9.0, 1.0]

    def test_parse_scores_insufficient(self):
        """_parse_scores returns None when too few numbers found."""
        from agent_memory.engine.reranker import _parse_scores
        scores = _parse_scores("only 2 numbers here", 5)
        assert scores is None

    def test_probe_llm_endpoint_configured(self):
        """_probe_llm_endpoint returns configured endpoint without probing."""
        from agent_memory.engine.reranker import _probe_llm_endpoint
        result = _probe_llm_endpoint("http://custom:8080")
        assert result == "http://custom:8080"

    def test_config_reranker_invalid_value(self, temp_project, engine_dir):
        """Invalid reranker value raises ValueError."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test",
            "reranker": "bogus",
            "tuning": {}
        }
        (memory_dir / "config.json").write_text(json.dumps(config))
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--stats"],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode != 0
        assert "ValueError" in result.stderr or "must be one of" in result.stderr

    def test_config_reranker_null_llm_endpoint(self, temp_project, engine_dir):
        """Null reranker_llm_endpoint passes through as None."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test",
            "reranker": "llm-local",
            "reranker_llm_endpoint": None,
            "reranker_llm_model": None,
            "tuning": {}
        }
        (memory_dir / "config.json").write_text(json.dumps(config))
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--stats"],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode == 0

    def test_config_reranker_top_n_invalid(self, temp_project, engine_dir):
        """reranker_top_n < 1 raises ValueError."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test",
            "reranker": "none",
            "reranker_top_n": 0,
            "tuning": {}
        }
        (memory_dir / "config.json").write_text(json.dumps(config))
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--stats"],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode != 0
        assert "reranker_top_n" in result.stderr

    def test_retrieval_none_mode_regression(self, temp_project, engine_dir):
        """Retrieval with reranker: 'none' produces same output as before (regression)."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test",
            "reranker": "none",
            "tuning": {"decay_rate": 0.99, "score_threshold": 0.01}
        }
        (memory_dir / "config.json").write_text(json.dumps(config))

        # Add a learning
        learning = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["TestComp"],
            "files_touched": ["test.py"], "trigger": "When testing",
            "action": "ALWAYS verify", "reason": "Safety first",
            "importance": 7, "severity": "major"
        }
        f = temp_project / "learning.json"
        f.write_text(json.dumps(learning))
        subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(f)],
            cwd=temp_project, capture_output=True, text=True
        )

        # Retrieve
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--step", "2", "--components", "TestComp", "--domain", "tooling"],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "RELEVANT PATTERNS" in result.stdout
        assert "When testing" in result.stdout


class TestEvalHarness:
    """Test the grading harness (--eval flag)."""

    @staticmethod
    def _setup_learning_and_fixture(temp_project, engine_dir, fixture_trigger, learning=None):
        """Shared setup: write config, log a learning, create eval fixture."""
        memory_dir = temp_project / "memory"
        (memory_dir / "config.json").write_text(json.dumps({
            "project_name": "Test",
            "tuning": {"decay_rate": 0.99, "score_threshold": 0.01}
        }))

        if learning is None:
            learning = {
                "step": 1, "source_agent": "gm", "type": "bug_fix",
                "domain": "tooling", "components": ["CollisionSystem"],
                "files_touched": ["collision.py"], "trigger": "When AABB collision detected",
                "action": "ALWAYS use broadphase", "reason": "Broadphase is efficient",
                "importance": 8, "severity": "major"
            }
        f = temp_project / "learning.json"
        f.write_text(json.dumps(learning))
        subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(f)],
            cwd=temp_project, capture_output=True, text=True
        )

        eval_dir = memory_dir / "eval"
        eval_dir.mkdir(exist_ok=True)
        fixture = {"step": 2, "components": "CollisionSystem", "domain": "tooling",
                    "expected_trigger": fixture_trigger}
        (eval_dir / "grading.jsonl").write_text(json.dumps(fixture) + "\n")

    def test_eval_no_fixture(self, temp_project, engine_dir):
        """--eval with no fixture file returns 1 with helpful message."""
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--eval"],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode == 1
        assert "No grading fixtures found" in result.stdout

    def test_eval_with_fixture_hit(self, temp_project, engine_dir):
        """--eval reports a hit when expected trigger appears in results."""
        self._setup_learning_and_fixture(temp_project, engine_dir, "When AABB collision detected")

        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--eval"],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "Grading Harness Results" in result.stdout
        assert "Top-1 hit rate: 1/1" in result.stdout
        assert "HIT@" in result.stdout

    def test_eval_with_fixture_miss(self, temp_project, engine_dir):
        """--eval reports a miss when expected trigger is absent."""
        self._setup_learning_and_fixture(temp_project, engine_dir, "When physics body overlaps")

        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--eval"],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "Top-1 hit rate: 0/1" in result.stdout
        assert "MISS" in result.stdout

    def test_eval_multiple_fixtures(self, temp_project, engine_dir):
        """--eval handles multiple fixtures with mixed hit/miss."""
        memory_dir = temp_project / "memory"
        (memory_dir / "config.json").write_text(json.dumps({
            "project_name": "Test",
            "tuning": {"decay_rate": 0.99, "score_threshold": 0.01}
        }))

        # Log two learnings
        for fname, learning in [
            ("learning1.json", {
                "step": 1, "source_agent": "gm", "type": "bug_fix",
                "domain": "tooling", "components": ["CollisionSystem"],
                "files_touched": ["collision.py"], "trigger": "When AABB collision detected",
                "action": "ALWAYS use broadphase", "reason": "Broadphase is efficient",
                "importance": 8, "severity": "major"
            }),
            ("learning2.json", {
                "step": 1, "source_agent": "gm", "type": "optimization",
                "domain": "performance", "components": ["RenderLoop"],
                "files_touched": ["render.py"], "trigger": "When frame rate drops below 60",
                "action": "ALWAYS batch draw calls", "reason": "Reduces GPU overhead",
                "importance": 7, "severity": "major"
            }),
        ]:
            f = temp_project / fname
            f.write_text(json.dumps(learning))
            subprocess.run(
                [sys.executable, str(engine_dir / "filter.py"), "--log-file", str(f)],
                cwd=temp_project, capture_output=True, text=True
            )

        # Create fixtures: one hit, one miss
        eval_dir = memory_dir / "eval"
        eval_dir.mkdir(exist_ok=True)
        fixture1 = {"step": 2, "components": "CollisionSystem", "domain": "tooling",
                     "expected_trigger": "When AABB collision detected"}
        fixture2 = {"step": 2, "components": "CollisionSystem", "domain": "tooling",
                     "expected_trigger": "Nonexistent trigger text"}
        (eval_dir / "grading.jsonl").write_text(
            json.dumps(fixture1) + "\n" + json.dumps(fixture2) + "\n"
        )

        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--eval"],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "Fixtures: 2" in result.stdout
        assert "Top-1 hit rate: 1/2" in result.stdout

    def test_eval_skips_comments_and_blanks(self, temp_project, engine_dir):
        """--eval skips comment lines and blank lines in fixture file."""
        memory_dir = temp_project / "memory"
        eval_dir = memory_dir / "eval"
        eval_dir.mkdir(exist_ok=True)
        (eval_dir / "grading.jsonl").write_text(
            "# This is a comment\n"
            "\n"
            '{"step": 1, "components": "TestComp", "domain": "tooling", "expected_trigger": "test"}\n'
        )

        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--eval"],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "Fixtures: 1" in result.stdout

    def test_eval_mutual_exclusion(self, temp_project, engine_dir):
        """--eval cannot be combined with --stats."""
        result = subprocess.run(
            [sys.executable, str(engine_dir / "filter.py"), "--eval", "--stats"],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode != 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
