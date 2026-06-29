"""Tests for config.json loading and enforcement."""
import json

from conftest import _make_ctx, _make_paths


class TestConfigLoad:
    """Test config.json loading."""

    def test_config_load_valid(self, temp_project):
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

        paths = _make_paths(memory_dir, temp_project)
        ctx = _make_ctx(config_path=str(config_path))
        from agent_memory.engine.handlers import stats_core
        result = stats_core(paths, ctx=ctx)

        assert result["exit_code"] == 0

    def test_config_load_missing(self, temp_project):
        """Test that missing config.json falls back to defaults."""
        paths = _make_paths(temp_project / "memory", temp_project)
        ctx = _make_ctx()
        from agent_memory.engine.handlers import stats_core
        result = stats_core(paths, ctx=ctx)

        assert result["exit_code"] == 0

    def test_config_max_step_applied(self, temp_project):
        """Test that custom max_step is actually enforced."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test Project",
            "max_step": 50
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))

        paths = _make_paths(memory_dir, temp_project)
        ctx = _make_ctx(config_path=str(config_path))
        from agent_memory.engine.handlers import log_core

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

        result = log_core(json.dumps(learning_49), paths, ctx)
        assert result["exit_code"] == 0
        assert result["status"] == "added"

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

        result = log_core(json.dumps(learning_51), paths, ctx)
        assert result["exit_code"] == 1
        assert result["status"] == "quarantined"

    def test_config_valid_domains_applied(self, temp_project):
        """Test that custom valid_domains actually rejects invalid domains."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test Project",
            "valid_domains": ["frontend", "backend"]
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))

        paths = _make_paths(memory_dir, temp_project)
        ctx = _make_ctx(config_path=str(config_path))
        from agent_memory.engine.handlers import log_core

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

        result = log_core(json.dumps(learning_valid), paths, ctx)
        assert result["exit_code"] == 0
        assert result["status"] == "added"

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

        result = log_core(json.dumps(learning_invalid), paths, ctx)
        assert result["exit_code"] == 1
        assert result["status"] == "quarantined"

    def test_config_valid_agents_applied(self, temp_project):
        """Test that custom valid_source_agents actually rejects invalid agents."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test Project",
            "valid_source_agents": ["gm", "reviewer"]
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))

        paths = _make_paths(memory_dir, temp_project)
        ctx = _make_ctx(config_path=str(config_path))
        from agent_memory.engine.handlers import log_core

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

        result = log_core(json.dumps(learning_valid), paths, ctx)
        assert result["exit_code"] == 0
        assert result["status"] == "added"

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

        result = log_core(json.dumps(learning_invalid), paths, ctx)
        assert result["exit_code"] == 1
        assert result["status"] == "quarantined"


class TestFallbackToDefaults:
    """Test fallback to defaults when config values are missing."""

    def test_fallback_partial_config(self, temp_project):
        """Test that partial config falls back to defaults for missing keys."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test Project"
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))

        paths = _make_paths(memory_dir, temp_project)
        ctx = _make_ctx(config_path=str(config_path))
        from agent_memory.engine.handlers import stats_core
        result = stats_core(paths, ctx=ctx)

        assert result["exit_code"] == 0

    def test_fallback_empty_config(self, temp_project):
        """Test that empty config.json falls back to all defaults."""
        memory_dir = temp_project / "memory"
        config_path = memory_dir / "config.json"
        config_path.write_text("{}")

        paths = _make_paths(memory_dir, temp_project)
        ctx = _make_ctx(config_path=str(config_path))
        from agent_memory.engine.handlers import stats_core
        result = stats_core(paths, ctx=ctx)

        assert result["exit_code"] == 0


class TestNullAcceptAny:
    """Test that null values accept any string."""

    def test_null_domains_accept_any(self, temp_project):
        """Test that null valid_domains accepts any domain."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test Project",
            "valid_domains": None
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))

        paths = _make_paths(memory_dir, temp_project)
        ctx = _make_ctx(config_path=str(config_path))
        from agent_memory.engine.handlers import log_core

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

        result = log_core(json.dumps(learning), paths, ctx)
        assert result["exit_code"] == 0
        assert result["status"] == "added"

    def test_null_agents_accept_any(self, temp_project):
        """Test that null valid_source_agents accepts any agent."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test Project",
            "valid_source_agents": None
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))

        paths = _make_paths(memory_dir, temp_project)
        ctx = _make_ctx(config_path=str(config_path))
        from agent_memory.engine.handlers import log_core

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

        result = log_core(json.dumps(learning), paths, ctx)
        assert result["exit_code"] == 0
        assert result["status"] == "added"


class TestMaxStepBound:
    """Test max_step bound including null."""

    def test_max_step_null_no_upper_bound(self, temp_project):
        """Test that null max_step has no upper bound."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test Project",
            "max_step": None
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))

        paths = _make_paths(memory_dir, temp_project)
        ctx = _make_ctx(config_path=str(config_path))
        from agent_memory.engine.handlers import log_core

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

        result = log_core(json.dumps(learning), paths, ctx)
        assert result["exit_code"] == 0
        assert result["status"] == "added"

    def test_max_step_enforced(self, temp_project):
        """Test that max_step is enforced when set."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test Project",
            "max_step": 10
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))

        paths = _make_paths(memory_dir, temp_project)
        ctx = _make_ctx(config_path=str(config_path))
        from agent_memory.engine.handlers import log_core

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

        result = log_core(json.dumps(learning), paths, ctx)
        assert result["exit_code"] == 1
        assert result["status"] == "quarantined"
