"""Regression tests for the HTTP API server endpoints.

Uses FastAPI's TestClient (requires fastapi + httpx).
Skips if fastapi is not installed.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

fastapi = pytest.importorskip("fastapi")
import httpx

from mnemoq.engine.server import create_app


def _make_client(app):
    """Create a test client compatible with httpx >= 0.28."""
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


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


def _make_paths(project_dir):
    """Build a Paths-like object matching filter.py's dataclass."""
    from mnemoq.cli import Paths
    memory_dir = str(project_dir / "memory")
    return Paths(
        memory_dir=memory_dir,
        repo_root=str(project_dir),
        config_path=os.path.join(memory_dir, "config.json"),
        learnings_path=os.path.join(memory_dir, "learnings.jsonl"),
        quarantine_path=os.path.join(memory_dir, "quarantine.jsonl"),
        archive_dir=os.path.join(memory_dir, "archive"),
        session_file=os.path.join(memory_dir, ".consolidate_session.json"),
        agents_md_path=os.path.join(str(project_dir), "AGENTS.md"),
    )


def _make_ctx():
    """Minimal ctx dict for testing."""
    return {
        "score_threshold": 0.15,
        "escalation_threshold": 30,
        "max_warnings": 5,
        "max_patterns": 15,
        "max_step": None,
        "domain_mappings": None,
        "bm25_k1": 1.5,
        "bm25_b": 0.75,
        "rrf_k": 60,
        "stop_words": set(),
        "embedding_alpha": 0.5,
        "embedding_model": "all-MiniLM-L6-v2",
        "embedding_cache_dir": "~/.agent-memory/models/",
        "reranker": "none",
        "reranker_top_n": 20,
        "valid_domains": None,
        "valid_source_agents": None,
        "valid_retrieval_only_agents": None,
        "valid_types": {"anti_pattern", "best_practice", "architectural_pattern", "meta_learning"},
        "valid_severities": {"critical", "major", "minor"},
        "valid_scopes": {"system", "module", "file"},
        "valid_debt_levels": {"proper", "workaround", "temporary"},
        "decay_rate": 0.995,
        "component_weight": 1.0,
        "file_weight": 0.7,
        "domain_weight": 0.4,
        "no_match_weight": 0.1,
        "minor_retention": 5,
        "major_retention": 20,
        "auto_learn_enabled": True,
        "auto_learn_git_scan_depth": 20,
        "auto_learn_fix_commit_threshold": 3,
        "auto_learn_under_retrieved_access": 2,
        "auto_learn_under_retrieved_reinforcement": 5,
        "auto_learn_over_injected_access": 10,
        "auto_learn_over_injected_reinforcement": 2,
        "auto_learn_staleness_threshold": 500,
        "auto_learn_max_files_per_commit": 5,
        "auto_learn_max_per_run": 20,
        "auto_learn_retrieval_failure_cap": 100,
    }


@pytest_asyncio.fixture
async def client(temp_project):
    paths = _make_paths(temp_project)
    ctx = _make_ctx()
    app = create_app(paths, ctx, api_key=None)
    async with _make_client(app) as c:
        yield c


@pytest_asyncio.fixture
async def dash_client(temp_project):
    """Dashboard-enabled client — eliminates repeated 4-line setup."""
    paths = _make_paths(temp_project)
    ctx = _make_ctx()
    app = create_app(paths, ctx, api_key=None, dashboard=True)
    async with _make_client(app) as c:
        yield c, paths, ctx


def _entry(**overrides):
    """Minimal valid log entry with overrides."""
    base = {"step": 1, "source_agent": "gm", "type": "anti_pattern",
            "domain": "backend", "components": ["api"], "files_touched": ["src/main.py"],
            "trigger": "When testing", "action": "ALWAYS test", "reason": "test reason",
            "importance": 5, "severity": "minor"}
    base.update(overrides)
    return base


def _base_config(name="test"):
    """Base config dict for config update tests."""
    return {"project_name": name, "tuning": {
        "score_threshold": 0.15, "decay_rate": 0.995,
        "component_weight": 1.0, "file_weight": 0.7,
        "domain_weight": 0.4, "no_match_weight": 0.1,
        "max_warnings": 5, "max_patterns": 15,
        "minor_retention": 5, "major_retention": 20}}


class TestHealth:
    @pytest.mark.smoke
    async def test_health(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestStats:
    @pytest.mark.smoke
    async def test_stats_empty(self, client):
        resp = await client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["unresolved"] == 0


class TestLog:
    async def test_log_valid(self, client):
        entry = {
            "step": 1,
            "source_agent": "gm",
            "type": "anti_pattern",
            "domain": "backend",
            "components": ["api"],
            "files_touched": ["src/main.py"],
            "trigger": "When building APIs",
            "action": "ALWAYS validate input",
            "reason": "Prevents injection attacks",
            "importance": 7,
            "severity": "major",
        }
        resp = await client.post("/api/log", json={"entry": entry})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("added", "duplicate", "semantic_duplicate")

    @pytest.mark.smoke
    async def test_log_invalid_json(self, client):
        resp = await client.post("/api/log", json={"entry": {"not": "valid"}})
        assert resp.status_code == 422

    async def test_log_missing_field(self, client):
        resp = await client.post("/api/log", json={"entry": {"step": 1}})
        assert resp.status_code == 422


class TestRetrieve:
    @pytest.mark.smoke
    async def test_retrieve_empty(self, client):
        resp = await client.get("/api/retrieve", params={"step": 1, "components": "api"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["warnings"] == []
        assert data["patterns"] == []
        assert data["total_entries"] == 0


class TestResolve:
    async def test_resolve_not_found(self, client):
        resp = await client.post("/api/resolve", json={"ts": "2025-01-01T00:00:00Z"})
        assert resp.status_code == 404

    async def test_resolve_invalid_ts(self, client):
        resp = await client.post("/api/resolve", json={"ts": "not-a-timestamp"})
        assert resp.status_code == 422  # pydantic validation fails


class TestConsolidate:
    async def test_consolidate_no_entries(self, client):
        resp = await client.post("/api/consolidate", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "no_entries"


class TestLearningsFilters:
    async def test_learnings_domain_filter(self, dash_client):
        c, _, _ = dash_client
        for i, domain in enumerate(("backend", "frontend")):
            await c.post("/api/log", json={"entry": _entry(
                step=i + 1, domain=domain,
                trigger=f"When testing {domain} domain filters",
                action="ALWAYS verify domain")})
        resp = await c.get("/api/learnings", params={"domain": "backend"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["entries"][0]["domain"] == "backend"

    async def test_learnings_severity_and_step_filter(self, dash_client):
        c, _, _ = dash_client
        for sev, step, comp, file in [("minor", 1, "Logger", "src/log.py"),
                                       ("major", 5, "Router", "src/router.py"),
                                       ("critical", 10, "Auth", "src/auth.py")]:
            await c.post("/api/log", json={"entry": _entry(
                step=step, severity=sev, components=[comp], files_touched=[file],
                trigger=f"When {comp} raises {sev} error at step {step}",
                action=f"ALWAYS handle {comp} {sev} errors",
                reason=f"{comp} {sev} errors cause distinct failures")})
        assert (await c.get("/api/learnings", params={"severity": "major"})).json()["count"] == 1
        assert (await c.get("/api/learnings", params={"step_min": 5})).json()["count"] == 2
        assert (await c.get("/api/learnings", params={"step_min": 3, "step_max": 7})).json()["count"] == 1

    async def test_learnings_q_filter(self, dash_client):
        c, _, _ = dash_client
        await c.post("/api/log", json={"entry": _entry(
            trigger="When searching for unique_keyword_here",
            action="ALWAYS use q filter", reason="because")})
        assert (await c.get("/api/learnings", params={"q": "unique_keyword"})).json()["count"] == 1
        assert (await c.get("/api/learnings", params={"q": "nonexistent"})).json()["count"] == 0


class TestQuarantine:
    async def test_quarantine_empty(self, dash_client):
        c, _, _ = dash_client
        resp = await c.get("/api/metrics/quarantine")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


class TestArchive:
    async def test_archive_empty(self, dash_client):
        c, _, _ = dash_client
        resp = await c.get("/api/metrics/archive")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


class TestMetrics:
    async def test_metrics_empty(self, client):
        resp = await client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_events" in data

    async def test_metrics_bad_since(self, client):
        resp = await client.get("/api/metrics", params={"since": "not-a-date"})
        assert resp.status_code == 400

    @pytest.mark.parametrize("metric_type,expect_key,exclude_key", [
        ("retrieval", "retrieval", "logging"),
        ("log", "logging", "retrieval"),
        ("stats", "events", "retrieval"),
        ("review_agents", "events", "retrieval"),
    ])
    async def test_metrics_type_filter(self, client, metric_type, expect_key, exclude_key):
        resp = await client.get("/api/metrics", params={"type": metric_type})
        assert resp.status_code == 200
        data = resp.json()
        assert expect_key in data
        assert exclude_key not in data


class TestConsolidationState:
    async def test_consolidation_state(self, dash_client):
        c, _, _ = dash_client
        resp = await c.post("/api/log", json={"entry": _entry(
            step=5, type="architectural_pattern", importance=7, severity="major",
            trigger="When building APIs", action="ALWAYS validate input",
            reason="This replaces the old approach and supersedes prior guidance")})
        assert resp.status_code == 200

        resp = await c.get("/api/consolidation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["unresolved"] >= 1
        assert data["total_entries"] >= 1
        assert isinstance(data["promotion_candidates"], list)
        assert isinstance(data["contradictions"], list)
        assert len(data["contradictions"]) >= 1
        assert isinstance(data["stale_entries"], list)
        assert "quarantine" in data
        assert "archive_history" in data


class TestFleet:
    async def test_fleet_enhanced(self, temp_project, monkeypatch):
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        # Stub project discovery to return the temp project only
        monkeypatch.setattr("mnemoq.engine.dashboard_api._load_project_paths", lambda: [temp_project])
        app = create_app(paths, ctx, api_key=None, dashboard=True)
        async with _make_client(app) as c:
            resp = await c.get("/api/fleet")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1
            proj = data["projects"][0]
            assert "health" in proj
            assert "total_entries" in proj
            assert "unresolved" in proj
            assert "last_consolidation" in proj
            assert "domains" in proj
            assert "trends" in proj
            assert "domain_heatmap" in data
            assert "fleet_trends" in data

    async def test_project_metrics_summary(self, temp_project, monkeypatch):
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        monkeypatch.setattr("mnemoq.engine.dashboard_api._load_project_paths", lambda: [temp_project])
        app = create_app(paths, ctx, api_key=None, dashboard=True)
        async with _make_client(app) as c:
            fleet_resp = await c.get("/api/fleet")
            assert fleet_resp.status_code == 200
            fleet_data = fleet_resp.json()
            assert fleet_data["count"] == 1
            project_id = fleet_data["projects"][0]["project"]

            resp = await c.get(f"/api/projects/{project_id}/metrics/summary")
            assert resp.status_code == 200
            summary = resp.json()
            assert summary["project"] == project_id
            assert "total_events" in summary
            assert "retrieval" in summary
            assert "logging" in summary
            assert "consolidation" in summary


class TestDashboard:
    async def test_dashboard_static(self, dash_client):
        c, _, _ = dash_client
        resp = await c.get("/")
        assert resp.status_code == 200
        assert "Agent Memory Engine" in resp.text
        for js_file in ["api.js", "app.js", "dashboard.js", "learnings.js",
                        "retrieval.js", "metrics.js", "consolidation.js",
                        "fleet.js", "settings.js", "events.js"]:
            resp = await c.get(f"/js/{js_file}")
            assert resp.status_code == 200, f"/js/{js_file} should be served"


class TestAPIKey:
    async def test_api_key_required(self, temp_project):
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        app = create_app(paths, ctx, api_key="secret123")
        async with _make_client(app) as c:
            # No key -> 401
            resp = await c.get("/api/stats")
            assert resp.status_code == 401
            # With key -> 200
            resp = await c.get("/api/stats", headers={"X-API-Key": "secret123"})
            assert resp.status_code == 200


class TestAlertBroadcast:
    async def test_alert_check_no_crash(self, temp_project):
        """Verify _check_and_broadcast_alerts runs without crashing on empty state."""
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        app = create_app(paths, ctx, api_key=None, dashboard=True)
        async with _make_client(app) as c:
            # Log an entry — should not crash even with no prior alert state
            entry = {
                "step": 1, "source_agent": "gm", "type": "anti_pattern",
                "domain": "backend", "components": ["api"], "files_touched": ["src/main.py"],
                "trigger": "When testing alert broadcast", "action": "ALWAYS verify no crash", "reason": "test reason",
                "importance": 5, "severity": "minor",
            }
            resp = await c.post("/api/log", json={"entry": entry})
            assert resp.status_code == 200

    async def test_alerts_endpoint(self, temp_project):
        """Verify /api/metrics/alerts works with dashboard enabled."""
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        app = create_app(paths, ctx, api_key=None, dashboard=True)
        async with _make_client(app) as c:
            resp = await c.get("/api/metrics/alerts")
            assert resp.status_code == 200
            data = resp.json()
            assert "alerts" in data


class TestHealthVersion:
    async def test_health_returns_version(self, temp_project):
        """Verify /api/health returns the version from the VERSION file."""
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        app = create_app(paths, ctx, api_key=None)
        async with _make_client(app) as c:
            resp = await c.get("/api/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            version_file = Path(__file__).parent.parent / "VERSION"
            expected = version_file.read_text().strip().splitlines()[0]
            assert data["version"] == expected


class TestWebSocketDedup:
    async def test_eventhub_dedup(self):
        """Verify EventHub skips duplicate keys and broadcasts distinct ones."""
        from mnemoq.engine.server import EventHub
        hub = EventHub()
        received = []

        class FakeWS:
            async def send_json(self, event):
                received.append(event)

        ws = FakeWS()
        hub._clients.append(ws)
        await hub.broadcast({"event": "log", "status": "added", "entry": {"ts": "2026-01-01T00:00:00Z"}})
        await hub.broadcast({"event": "log", "status": "added", "entry": {"ts": "2026-01-01T00:00:00Z"}})
        await hub.broadcast({"event": "log", "status": "added", "entry": {"ts": "2026-01-02T00:00:00Z"}})
        assert len(received) == 2

    async def test_watcher_dedup_against_api_hook(self):
        """File watcher re-read of a metrics.jsonl line must not produce a second WS broadcast."""
        from mnemoq.engine.server import EventHub
        hub = EventHub()
        received = []

        class FakeWS:
            async def send_json(self, event):
                received.append(event)

        ws = FakeWS()
        hub._clients.append(ws)

        # API hook broadcast (what /api/log does)
        await hub.broadcast({
            "event": "log",
            "status": "added",
            "entry": {"ts": "2026-01-01T00:00:00Z"},
        })

        # Same data as a metrics.jsonl line read by the file watcher
        await hub.broadcast({
            "event": "log",
            "outcome": "ADDED",
            "entry_ts": "2026-01-01T00:00:00Z",
            "source": "file_watcher",
        })

        assert len(received) == 1


class TestMetricsFields:
    async def test_lifecycle_fields(self, temp_project):
        """Verify /api/metrics/lifecycle returns new visualization fields."""
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        app = create_app(paths, ctx, api_key=None, dashboard=True)
        async with _make_client(app) as c:
            entry = {
                "step": 1, "source_agent": "gm", "type": "anti_pattern",
                "domain": "backend", "components": ["api"], "files_touched": ["src/main.py"],
                "trigger": "When testing lifecycle fields", "action": "ALWAYS test", "reason": "test reason",
                "importance": 5, "severity": "major",
            }
            await c.post("/api/log", json={"entry": entry})
            resp = await c.get("/api/metrics/lifecycle")
            assert resp.status_code == 200
            data = resp.json()
            assert "age_distribution" in data
            assert "access_distribution" in data
            assert "zombie_entries" in data
            assert "zombie_count" in data

    async def test_dedup_fields(self, temp_project):
        """Verify /api/metrics/dedup returns daily trend and conflicts list."""
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        app = create_app(paths, ctx, api_key=None, dashboard=True)
        async with _make_client(app) as c:
            resp = await c.get("/api/metrics/dedup")
            assert resp.status_code == 200
            data = resp.json()
            assert "daily" in data
            assert "conflicts_list" in data

    async def test_dedup_conflict_source_agents(self, temp_project):
        """A conflict between two entries must expose both source agents."""
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        app = create_app(paths, ctx, api_key=None, dashboard=True)
        async with _make_client(app) as c:
            entry1 = {
                "step": 1, "source_agent": "alpha", "type": "anti_pattern",
                "domain": "backend", "components": ["api"], "files_touched": ["src/main.py"],
                "trigger": "When using global state",
                "action": "ALWAYS use EventBus or scene-local variables",
                "reason": "test reason",
                "importance": 5, "severity": "major",
            }
            entry2 = {
                "step": 1, "source_agent": "beta", "type": "anti_pattern",
                "domain": "backend", "components": ["api"], "files_touched": ["src/main.py"],
                "trigger": "When using global state",
                "action": "NEVER use global mutable state",
                "reason": "test reason",
                "importance": 5, "severity": "major",
            }
            await c.post("/api/log", json={"entry": entry1})
            await c.post("/api/log", json={"entry": entry2})
            resp = await c.get("/api/metrics/dedup")
            assert resp.status_code == 200
            data = resp.json()
            assert data["conflicts"] == 1
            assert len(data["conflicts_list"]) == 1
            assert data["conflicts_list"][0]["source_agent"] == "beta"
            assert data["conflicts_list"][0]["matched_source_agent"] == "alpha"

    async def test_consolidation_fields(self, temp_project):
        """Verify /api/metrics/consolidation-quality returns daily trend."""
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        app = create_app(paths, ctx, api_key=None, dashboard=True)
        async with _make_client(app) as c:
            resp = await c.get("/api/metrics/consolidation-quality")
            assert resp.status_code == 200
            data = resp.json()
            assert "daily" in data

    async def test_agents_fields(self, temp_project):
        """Verify /api/metrics/agents returns severity counts and trend."""
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        app = create_app(paths, ctx, api_key=None, dashboard=True)
        async with _make_client(app) as c:
            entry = {
                "step": 1, "source_agent": "gm", "type": "anti_pattern",
                "domain": "backend", "components": ["api"], "files_touched": ["src/main.py"],
                "trigger": "When testing agent fields", "action": "ALWAYS test", "reason": "test reason",
                "importance": 5, "severity": "major",
            }
            await c.post("/api/log", json={"entry": entry})
            resp = await c.get("/api/metrics/agents")
            assert resp.status_code == 200
            data = resp.json()
            assert "agents" in data
            assert data["agents"]
            assert "severity_counts" in data["agents"][0]
            assert "trend" in data["agents"][0]


class TestNoApproveButton:
    async def test_consolidation_js_has_no_approve_button(self, temp_project):
        """Regression: promotion candidates must not render an Approve button."""
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        app = create_app(paths, ctx, api_key=None, dashboard=True)
        async with _make_client(app) as c:
            resp = await c.get("/js/consolidation.js")
            assert resp.status_code == 200
            assert "approve-btn" not in resp.text


class TestConfigUpdate:
    async def test_config_update_invalid_tuning(self, temp_project):
        """PUT /api/config with invalid tuning returns 400."""
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        app = create_app(paths, ctx, api_key=None, dashboard=True)
        async with _make_client(app) as c:
            resp = await c.put("/api/config", json={"tuning": {"score_threshold": 1.5}})
            assert resp.status_code == 400

    async def test_config_update_valid(self, temp_project):
        """PUT /api/config with valid config persists changes."""
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        # Seed config with valid shape
        base_config = {
            "project_name": "test",
            "tuning": {
                "score_threshold": 0.15,
                "decay_rate": 0.995,
                "component_weight": 1.0,
                "file_weight": 0.7,
                "domain_weight": 0.4,
                "no_match_weight": 0.1,
                "max_warnings": 5,
                "max_patterns": 15,
                "minor_retention": 5,
                "major_retention": 20,
            },
        }
        with open(paths.config_path, "w", encoding="utf-8") as f:
            json.dump(base_config, f)
        app = create_app(paths, ctx, api_key=None, dashboard=True)
        async with _make_client(app) as c:
            update = {
                "project_name": "test",
                "tuning": {
                    "score_threshold": 0.5,
                    "decay_rate": 0.99,
                    "component_weight": 1.0,
                    "file_weight": 0.7,
                    "domain_weight": 0.4,
                    "no_match_weight": 0.1,
                    "max_warnings": 5,
                    "max_patterns": 15,
                    "minor_retention": 5,
                    "major_retention": 20,
                },
                "valid_domains": ["backend"],
                "valid_source_agents": ["gm"],
            }
            resp = await c.put("/api/config", json=update)
            assert resp.status_code == 200
            resp = await c.get("/api/config")
            assert resp.status_code == 200
            data = resp.json()
            assert data["tuning"]["score_threshold"] == 0.5
            assert data["valid_domains"] == ["backend"]
            assert data["valid_source_agents"] == ["gm"]

    async def test_config_update_preserves_unknown_fields(self, temp_project):
        """Unknown top-level fields are preserved by PUT /api/config."""
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        base_config = {
            "project_name": "test",
            "embedding_model": "all-MiniLM-L6-v2",
            "tuning": {
                "score_threshold": 0.15,
                "decay_rate": 0.995,
                "component_weight": 1.0,
                "file_weight": 0.7,
                "domain_weight": 0.4,
                "no_match_weight": 0.1,
                "max_warnings": 5,
                "max_patterns": 15,
                "minor_retention": 5,
                "major_retention": 20,
            },
        }
        with open(paths.config_path, "w", encoding="utf-8") as f:
            json.dump(base_config, f)
        app = create_app(paths, ctx, api_key=None, dashboard=True)
        async with _make_client(app) as c:
            update = {
                "project_name": "test",
                "embedding_model": "all-MiniLM-L6-v2",
                "tuning": {"score_threshold": 0.2, "decay_rate": 0.995,
                           "component_weight": 1.0, "file_weight": 0.7,
                           "domain_weight": 0.4, "no_match_weight": 0.1,
                           "max_warnings": 5, "max_patterns": 15,
                           "minor_retention": 5, "major_retention": 20},
                "custom_field": "kept",
            }
            resp = await c.put("/api/config", json=update)
            assert resp.status_code == 200
            resp = await c.get("/api/config")
            data = resp.json()
            assert data["embedding_model"] == "all-MiniLM-L6-v2"
            assert data["custom_field"] == "kept"

    async def test_config_update_atomic_failed_write(self, temp_project, monkeypatch):
        """A failed atomic write must leave the original config.json unchanged."""
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        base_config = {"project_name": "original",
                        "tuning": {"score_threshold": 0.15, "decay_rate": 0.995,
                                   "component_weight": 1.0, "file_weight": 0.7,
                                   "domain_weight": 0.4, "no_match_weight": 0.1,
                                   "max_warnings": 5, "max_patterns": 15,
                                   "minor_retention": 5, "major_retention": 20}}
        with open(paths.config_path, "w", encoding="utf-8") as f:
            json.dump(base_config, f)

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("os.replace", boom)

        tmp_path = os.path.join(os.path.dirname(paths.config_path), "config.json.tmp")
        app = create_app(paths, ctx, api_key=None, dashboard=True)
        async with _make_client(app) as c:
            update = {"project_name": "new",
                      "tuning": {"score_threshold": 0.2, "decay_rate": 0.995,
                                 "component_weight": 1.0, "file_weight": 0.7,
                                 "domain_weight": 0.4, "no_match_weight": 0.1,
                                 "max_warnings": 5, "max_patterns": 15,
                                 "minor_retention": 5, "major_retention": 20}}
            resp = await c.put("/api/config", json=update)
            assert resp.status_code == 500
            with open(paths.config_path, encoding="utf-8") as f:
                saved = json.load(f)
            assert saved["project_name"] == "original"
            assert not os.path.exists(tmp_path), "Temporary config file should be removed after failed write"


class TestAutoLearn:
    async def test_auto_learn_endpoint(self, temp_project):
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        app = create_app(paths, ctx, api_key=None)
        async with _make_client(app) as c:
            resp = await c.post("/api/auto-learn")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "scanned" in data
            assert "generated" in data
            assert "deduped" in data
            assert "skipped" in data


class TestMCPEvaluatePromptSchema:
    def test_evaluate_prompt_in_tools(self):
        """MCP TOOLS list must include evaluate_prompt with expected required fields."""
        from mnemoq.engine.mcp_server import TOOLS
        tool = next((t for t in TOOLS if t["name"] == "evaluate_prompt"), None)
        assert tool is not None, "evaluate_prompt not found in TOOLS"
        required = set(tool["inputSchema"].get("required", []))
        expected = {"step", "prompt_type", "outcome", "components", "files_touched"}
        assert expected.issubset(required), f"Missing required fields: {expected - required}"
