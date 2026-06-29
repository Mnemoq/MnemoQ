"""Integration tests for the Fake Generator dashboard API endpoints.

Uses FastAPI's TestClient pattern from test_server.py.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

fastapi = pytest.importorskip("fastapi")
import httpx

import agent_memory.engine.dashboard_api as dashboard_api
from agent_memory.engine.server import create_app


def _make_paths(project_dir):
    from agent_memory.cli import Paths
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
        "evaluate_enabled": True,
        "evaluate_max_per_turn": 3,
    }


def _make_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def temp_project():
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        memory_dir = project_dir / "memory"
        memory_dir.mkdir()
        (memory_dir / "learnings.jsonl").touch()
        (memory_dir / "quarantine.jsonl").touch()
        (memory_dir / "archive").mkdir()
        # Write a minimal config so sim_dialogue/generate_fakes can load it
        import json
        with open(memory_dir / "config.json", "w") as f:
            json.dump({"evaluate_enabled": True}, f)
        yield project_dir


@pytest_asyncio.fixture
async def client(temp_project):
    # Reset global state before each test
    with dashboard_api._FakeGenLock:
        dashboard_api._reset_fake_gen_state()
    paths = _make_paths(temp_project)
    ctx = _make_ctx()
    app = create_app(paths, ctx, api_key=None, dashboard=True)
    async with _make_client(app) as c:
        yield c
    # Clean up after test
    with dashboard_api._FakeGenLock:
        dashboard_api._reset_fake_gen_state()


async def _poll_until_done(client, timeout=30):
    """Poll /api/fake-gen/status until status is not 'running' or timeout."""
    for _ in range(timeout * 5):
        await asyncio.sleep(0.2)
        resp = await client.get("/api/fake-gen/status")
        data = resp.json()
        if data["status"] != "running":
            return data
    return None


class TestFakeGenStatus:
    async def test_status_idle(self, client):
        resp = await client.get("/api/fake-gen/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "idle"


class TestFakeGenValidation:
    async def test_invalid_script_rejected(self, client):
        resp = await client.post("/api/fake-gen/start", json={"script": "unknown", "batch_name": "test"})
        assert resp.status_code == 400

    async def test_missing_batch_name_rejected(self, client):
        resp = await client.post("/api/fake-gen/start", json={
            "script": "sim_dialogue", "clean": True, "confirm": False,
        })
        assert resp.status_code == 400

    async def test_invalid_mode_rejected(self, client):
        resp = await client.post("/api/fake-gen/start", json={
            "script": "sim_dialogue", "mode": "invalid", "batch_name": "test",
        })
        assert resp.status_code == 400


class TestFakeGenDryRun:
    async def test_start_sim_dialogue_dry_run(self, client):
        resp = await client.post("/api/fake-gen/start", json={
            "script": "sim_dialogue",
            "turns": 5,
            "mode": "direct",
            "dry_run": True,
            "seed": 42,
            "batch_name": "dry-run-test",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        result = await _poll_until_done(client)
        assert result is not None
        assert result["status"] == "done"
        assert result["exit_code"] == 0

    async def test_start_generate_fakes_dry_run(self, client):
        resp = await client.post("/api/fake-gen/start", json={
            "script": "generate_fakes",
            "turns": 10,
            "mode": "direct",
            "dry_run": True,
            "seed": 42,
            "batch_name": "dry-run-test",
        })
        assert resp.status_code == 200
        result = await _poll_until_done(client)
        assert result is not None
        assert result["status"] == "done"
        assert result["exit_code"] == 0


class TestFakeGenConflict:
    async def test_conflict_when_already_running(self, client):
        # Start a run
        resp = await client.post("/api/fake-gen/start", json={
            "script": "sim_dialogue",
            "turns": 50,
            "mode": "direct",
            "dry_run": True,
            "seed": 42,
            "batch_name": "conflict-test-1",
        })
        assert resp.status_code == 200
        # Immediately try to start another
        resp2 = await client.post("/api/fake-gen/start", json={
            "script": "sim_dialogue",
            "turns": 5,
            "mode": "direct",
            "dry_run": True,
            "batch_name": "conflict-test-2",
        })
        assert resp2.status_code == 409
        # Wait for the first to finish
        await _poll_until_done(client)


class TestFakeGenStop:
    async def test_stop_cancels_run(self, client):
        resp = await client.post("/api/fake-gen/start", json={
            "script": "sim_dialogue",
            "turns": 200,
            "mode": "direct",
            "dry_run": True,
            "seed": 42,
            "batch_name": "stop-test",
        })
        assert resp.status_code == 200
        # Wait for status to become "running" (thread startup)
        running = False
        for _ in range(20):
            await asyncio.sleep(0.05)
            status_resp = await client.get("/api/fake-gen/status")
            if status_resp.json()["status"] == "running":
                running = True
                break
        if not running:
            # Run finished too fast — verify it completed successfully
            result = await _poll_until_done(client, timeout=5)
            assert result is not None
            assert result["status"] in ("done", "cancelled")
            return
        stop_resp = await client.post("/api/fake-gen/stop", json={})
        assert stop_resp.status_code == 200
        assert stop_resp.json()["status"] == "cancelled"
        # Wait for the thread to notice
        result = await _poll_until_done(client, timeout=10)
        assert result is not None
        assert result["status"] == "cancelled"


class TestFakeGenStopIdle:
    async def test_stop_when_not_running(self, client):
        resp = await client.post("/api/fake-gen/stop", json={})
        assert resp.status_code == 409


class TestFakeGenDeleteData:
    async def test_delete_without_confirm_rejected(self, client):
        resp = await client.request("DELETE", "/api/fake-gen/data", json={})
        assert resp.status_code == 400

    async def test_delete_fakes_removes_file(self, client, temp_project):
        fakes_path = temp_project / "memory" / "fakes.jsonl"
        fakes_path.write_text('{"step":1}\n{"step":2}\n', encoding="utf-8")
        resp = await client.request("DELETE", "/api/fake-gen/data", json={"confirm": True})
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 2
        assert not fakes_path.exists()

    async def test_delete_when_no_fakes(self, client):
        resp = await client.request("DELETE", "/api/fake-gen/data", json={"confirm": True})
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 0


class TestBatchManagement:
    async def test_list_batches_empty(self, client):
        resp = await client.get("/api/fake-gen/batches")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["batches"] == []

    async def test_batch_name_required(self, client):
        resp = await client.post("/api/fake-gen/start", json={
            "script": "generate_fakes",
            "turns": 5,
            "mode": "direct",
            "seed": 42,
        })
        assert resp.status_code == 400

    async def test_start_creates_batch(self, client, temp_project):
        resp = await client.post("/api/fake-gen/start", json={
            "script": "generate_fakes",
            "turns": 5,
            "mode": "direct",
            "seed": 42,
            "batch_name": "Test Batch One",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["batch_slug"] == "test-batch-one"
        result = await _poll_until_done(client, timeout=60)
        assert result is not None
        assert result["status"] == "done"
        assert result["exit_code"] == 0
        # Verify batch file exists
        batch_file = temp_project / "memory" / "fake_batches" / "test-batch-one.jsonl"
        assert batch_file.exists()
        # Verify manifest entry
        resp2 = await client.get("/api/fake-gen/batches")
        batches = resp2.json()["batches"]
        assert len(batches) == 1
        assert batches[0]["slug"] == "test-batch-one"
        assert batches[0]["category"] == "generate_fakes"
        assert batches[0]["active"] is True
        assert batches[0]["entry_count"] > 0

    async def test_delete_batch_by_slug(self, client, temp_project):
        # Create a batch by writing manifest directly
        import json as _json
        batch_dir = temp_project / "memory" / "fake_batches"
        batch_dir.mkdir(parents=True, exist_ok=True)
        batch_file = batch_dir / "my-batch.jsonl"
        batch_file.write_text('{"step":1}\n{"step":2}\n{"step":3}\n', encoding="utf-8")
        manifest = [{
            "name": "My Batch",
            "slug": "my-batch",
            "category": "generate_fakes",
            "file": str(batch_file),
            "entry_count": 3,
            "created_at": "2025-01-01T00:00:00Z",
            "active": True,
            "params": {},
        }]
        manifest_path = temp_project / "memory" / "fake_batches.json"
        manifest_path.write_text(_json.dumps(manifest), encoding="utf-8")
        # Delete it
        resp = await client.request("DELETE", "/api/fake-gen/batches/my-batch", json={"confirm": True})
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 1
        assert not batch_file.exists()
        # Verify manifest is empty
        resp2 = await client.get("/api/fake-gen/batches")
        assert resp2.json()["count"] == 0

    async def test_toggle_batch_active(self, client, temp_project):
        import json as _json
        batch_dir = temp_project / "memory" / "fake_batches"
        batch_dir.mkdir(parents=True, exist_ok=True)
        batch_file = batch_dir / "toggle-test.jsonl"
        batch_file.write_text('{"step":1}\n', encoding="utf-8")
        manifest = [{
            "name": "Toggle Test",
            "slug": "toggle-test",
            "category": "sim_dialogue",
            "file": str(batch_file),
            "entry_count": 1,
            "created_at": "2025-01-01T00:00:00Z",
            "active": True,
            "params": {},
        }]
        (temp_project / "memory" / "fake_batches.json").write_text(_json.dumps(manifest), encoding="utf-8")
        resp = await client.request("PATCH", "/api/fake-gen/batches/toggle-test", json={"active": False})
        assert resp.status_code == 200
        assert resp.json()["active"] is False
        # Verify persisted
        resp2 = await client.get("/api/fake-gen/batches")
        batches = resp2.json()["batches"]
        assert batches[0]["active"] is False

    async def test_migration_of_legacy_fakes(self, client, temp_project):
        fakes_path = temp_project / "memory" / "fakes.jsonl"
        fakes_path.write_text('{"step":1}\n{"step":2}\n', encoding="utf-8")
        resp = await client.get("/api/fake-gen/batches")
        assert resp.status_code == 200
        batches = resp.json()["batches"]
        assert len(batches) == 1
        assert batches[0]["slug"] == "legacy"
        assert batches[0]["category"] == "generate_fakes"
        assert batches[0]["entry_count"] == 2
        assert not fakes_path.exists()
        # Verify batch file exists
        batch_file = temp_project / "memory" / "fake_batches" / "legacy.jsonl"
        assert batch_file.exists()

    async def test_delete_all_batches(self, client, temp_project):
        import json as _json
        batch_dir = temp_project / "memory" / "fake_batches"
        batch_dir.mkdir(parents=True, exist_ok=True)
        for i in range(2):
            bf = batch_dir / f"batch-{i}.jsonl"
            bf.write_text('{"step":1}\n', encoding="utf-8")
        manifest = [
            {"name": f"Batch {i}", "slug": f"batch-{i}", "category": "generate_fakes",
             "file": str(batch_dir / f"batch-{i}.jsonl"), "entry_count": 1,
             "created_at": "2025-01-01T00:00:00Z", "active": True, "params": {}}
            for i in range(2)
        ]
        (temp_project / "memory" / "fake_batches.json").write_text(_json.dumps(manifest), encoding="utf-8")
        resp = await client.request("DELETE", "/api/fake-gen/batches", json={"confirm": True})
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 2
        resp2 = await client.get("/api/fake-gen/batches")
        assert resp2.json()["count"] == 0

    async def test_slug_collision(self, client, temp_project):
        import json as _json
        batch_dir = temp_project / "memory" / "fake_batches"
        batch_dir.mkdir(parents=True, exist_ok=True)
        manifest = [{
            "name": "My Batch", "slug": "my-batch", "category": "generate_fakes",
            "file": str(batch_dir / "my-batch.jsonl"), "entry_count": 1,
            "created_at": "2025-01-01T00:00:00Z", "active": True, "params": {},
        }]
        (batch_dir / "my-batch.jsonl").write_text('{"step":1}\n', encoding="utf-8")
        (temp_project / "memory" / "fake_batches.json").write_text(_json.dumps(manifest), encoding="utf-8")
        # Start a run with same batch name — should get slug my-batch-2
        resp = await client.post("/api/fake-gen/start", json={
            "script": "generate_fakes",
            "turns": 3,
            "mode": "direct",
            "seed": 42,
            "batch_name": "My Batch",
        })
        assert resp.status_code == 200
        assert resp.json()["batch_slug"] == "my-batch-2"
        await _poll_until_done(client, timeout=60)

    async def test_read_fakes_merges_active_only(self, temp_project):
        import json as _json

        from agent_memory.engine.io import read_learnings_for_dashboard
        batch_dir = temp_project / "memory" / "fake_batches"
        batch_dir.mkdir(parents=True, exist_ok=True)
        active_file = batch_dir / "active.jsonl"
        inactive_file = batch_dir / "inactive.jsonl"
        active_file.write_text('{"step":1,"trigger":"a"}\n', encoding="utf-8")
        inactive_file.write_text('{"step":2,"trigger":"b"}\n', encoding="utf-8")
        manifest = [
            {"name": "Active", "slug": "active", "category": "generate_fakes",
             "file": str(active_file), "entry_count": 1,
             "created_at": "2025-01-01T00:00:00Z", "active": True, "params": {}},
            {"name": "Inactive", "slug": "inactive", "category": "generate_fakes",
             "file": str(inactive_file), "entry_count": 1,
             "created_at": "2025-01-01T00:00:00Z", "active": False, "params": {}},
        ]
        (temp_project / "memory" / "fake_batches.json").write_text(_json.dumps(manifest), encoding="utf-8")
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        ctx["data_source"] = "fakes"
        entries = read_learnings_for_dashboard(paths, ctx)
        assert len(entries) == 1
        assert entries[0].get("trigger") == "a"

    async def test_delete_data_alias(self, client, temp_project):
        import json as _json
        batch_dir = temp_project / "memory" / "fake_batches"
        batch_dir.mkdir(parents=True, exist_ok=True)
        bf = batch_dir / "alias-test.jsonl"
        bf.write_text('{"step":1}\n', encoding="utf-8")
        manifest = [{
            "name": "Alias Test", "slug": "alias-test", "category": "generate_fakes",
            "file": str(bf), "entry_count": 1,
            "created_at": "2025-01-01T00:00:00Z", "active": True, "params": {},
        }]
        (temp_project / "memory" / "fake_batches.json").write_text(_json.dumps(manifest), encoding="utf-8")
        resp = await client.request("DELETE", "/api/fake-gen/data", json={"confirm": True})
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 1
        assert not bf.exists()
        resp2 = await client.get("/api/fake-gen/batches")
        assert resp2.json()["count"] == 0
