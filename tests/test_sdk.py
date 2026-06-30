"""Regression tests for the Python SDK (tier 2.4)."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

httpx = pytest.importorskip("httpx")
fastapi = pytest.importorskip("fastapi")

from mnemoq.engine.server import create_app  # noqa: E402
from mnemoq.sdk import AsyncMemoryClient, MemoryClient  # noqa: E402
from mnemoq.sdk.exceptions import APIError, NotFoundError, ValidationError  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_project():
    """Create a temporary project with a valid memory directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        memory_dir = project_dir / "memory"
        memory_dir.mkdir()
        (memory_dir / "learnings.jsonl").touch()
        (memory_dir / "quarantine.jsonl").touch()
        (memory_dir / "archive").mkdir()
        (project_dir / "AGENTS.md").touch()
        yield project_dir


def _make_paths(project_dir):
    """Build a Paths dataclass matching filter.py."""
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
    """Minimal ctx for HTTP tests."""
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
        "valid_types": {"bug_fix", "optimization", "architectural_pattern"},
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
    }


def _sample_entry(step=1):
    """Return a valid entry for the default engine schema."""
    return {
        "step": step,
        "source_agent": "gm",
        "type": "bug_fix",
        "domain": "backend",
        "components": ["Server"],
        "files_touched": ["src/engine/server.py"],
        "trigger": "When handling a request",
        "action": "ALWAYS validate the input",
        "reason": "Invalid input causes handler errors",
        "importance": 7,
        "severity": "major",
    }


# ---------------------------------------------------------------------------
# Local transport tests
# ---------------------------------------------------------------------------


class TestLocalClient:
    def test_requires_memory_dir_or_base_url(self):
        with pytest.raises(ValueError):
            MemoryClient()

    def test_log_and_retrieve(self, temp_project):
        client = MemoryClient(memory_dir=str(temp_project / "memory"))
        entry = _sample_entry()
        result = client.log(entry)
        assert result["status"] == "added"
        assert "entry" in result
        assert "ts" in result["entry"]

        retrieved = client.retrieve(step=1, components=["Server"])
        assert retrieved["total_entries"] == 1

    def test_resolve(self, temp_project):
        client = MemoryClient(memory_dir=str(temp_project / "memory"))
        entry = _sample_entry()
        logged = client.log(entry)
        ts = logged["entry"]["ts"]

        resolved = client.resolve(ts)
        assert resolved["status"] == "resolved"
        assert resolved["entry"]["resolved"] is True

    def test_resolve_not_found(self, temp_project):
        client = MemoryClient(memory_dir=str(temp_project / "memory"))
        with pytest.raises(NotFoundError):
            client.resolve("2025-01-01T00:00:00Z")

    def test_update(self, temp_project):
        client = MemoryClient(memory_dir=str(temp_project / "memory"))
        entry = _sample_entry()
        logged = client.log(entry)
        ts = logged["entry"]["ts"]

        updated = client.update(ts, {**entry, "action": "NEVER trust the input"})
        assert updated["status"] == "updated"
        assert updated["entry"]["action"] == "NEVER trust the input"

    def test_invalid_entry_raises(self, temp_project):
        client = MemoryClient(memory_dir=str(temp_project / "memory"))
        with pytest.raises(ValidationError):
            client.log({"step": 1})

    def test_stats(self, temp_project):
        client = MemoryClient(memory_dir=str(temp_project / "memory"))
        client.log(_sample_entry())
        stats = client.stats()
        assert stats["total"] == 1

    def test_metrics(self, temp_project):
        client = MemoryClient(memory_dir=str(temp_project / "memory"))
        client.log(_sample_entry())
        metrics = client.metrics()
        assert "total_events" in metrics

    def test_consolidate_no_entries(self, temp_project):
        client = MemoryClient(memory_dir=str(temp_project / "memory"))
        result = client.consolidate()
        assert result["status"] == "no_entries"


# ---------------------------------------------------------------------------
# HTTP transport tests
# ---------------------------------------------------------------------------


class TestHTTPClientAsync:
    """HTTP transport tests via AsyncMemoryClient + httpx.AsyncClient.

    ASGITransport is async-only, so we test the HTTP path through the async client.
    The sync _HTTPTransport uses the same request logic, just with httpx.Client.
    """

    @pytest.fixture
    def app(self, temp_project):
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        return create_app(paths, ctx, api_key=None)

    async def test_log_and_retrieve(self, app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
            sdk_transport = _sdk_async_http_transport(http_client)
            async with AsyncMemoryClient(base_url="http://testserver") as client:
                client._http_transport = sdk_transport

                entry = _sample_entry()
                result = await client.log(entry)
                assert result["status"] == "added"

                retrieved = await client.retrieve(step=1, components=["Server"])
                assert retrieved["total_entries"] == 1

    async def test_resolve(self, app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
            sdk_transport = _sdk_async_http_transport(http_client)
            async with AsyncMemoryClient(base_url="http://testserver") as client:
                client._http_transport = sdk_transport

                logged = await client.log(_sample_entry())
                ts = logged["entry"]["ts"]
                resolved = await client.resolve(ts)
                assert resolved["status"] == "resolved"

    async def test_stats(self, app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
            sdk_transport = _sdk_async_http_transport(http_client)
            async with AsyncMemoryClient(base_url="http://testserver") as client:
                client._http_transport = sdk_transport
                stats = await client.stats()
                assert stats["total"] == 0

    async def test_unauthorized(self, temp_project):
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        app = create_app(paths, ctx, api_key="secret")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
            sdk_transport = _sdk_async_http_transport(http_client)
            async with AsyncMemoryClient(base_url="http://testserver") as client:
                client._http_transport = sdk_transport
                with pytest.raises(APIError):
                    await client.stats()


# ---------------------------------------------------------------------------
# Async client tests
# ---------------------------------------------------------------------------


class TestAsyncClient:
    @pytest.fixture
    def app(self, temp_project):
        paths = _make_paths(temp_project)
        ctx = _make_ctx()
        return create_app(paths, ctx, api_key=None)

    async def test_local_async_log(self, temp_project):
        async with AsyncMemoryClient(memory_dir=str(temp_project / "memory")) as client:
            result = await client.log(_sample_entry())
            assert result["status"] == "added"
            retrieved = await client.retrieve(step=1, components=["Server"])
            assert retrieved["total_entries"] == 1

    async def test_http_async_log(self, app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
            sdk_transport = _sdk_async_http_transport(http_client)
            async with AsyncMemoryClient(base_url="http://testserver") as client:
                client._http_transport = sdk_transport
                result = await client.log(_sample_entry())
                assert result["status"] == "added"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sdk_async_http_transport(http_client):
    """Wrap an existing httpx.AsyncClient into the SDK async HTTP transport."""
    from mnemoq.sdk.client import _AsyncHTTPTransport

    return _AsyncHTTPTransport._with_client(http_client)
