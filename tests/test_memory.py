"""
Tests for multi-project memory system.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


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
        result = subprocess.run(
            [sys.executable, str(engine_dir / "scaffold.py"), str(fresh_project), "--defaults"],
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        
        # Check that memory directory was created
        memory_dir = fresh_project / "memory"
        assert memory_dir.exists()
        
        # Check that key files exist
        assert (memory_dir / "filter.py").exists()
        assert (memory_dir / "profile.py").exists()
        assert (memory_dir / "config.json").exists()
        assert (memory_dir / "learnings.jsonl").exists()
        assert (memory_dir / "quarantine.jsonl").exists()
    
    def test_scaffold_opencode_wiring(self, fresh_project, engine_dir):
        """Test that --opencode flag wires opencode.json."""
        result = subprocess.run(
            [sys.executable, str(engine_dir / "scaffold.py"), str(fresh_project), "--defaults", "--opencode"],
            capture_output=True,
            text=True
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
