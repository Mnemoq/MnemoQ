"""Python SDK for the Agent Memory Engine.

Supports both direct file access (local) and HTTP API (remote) transports.

Examples:
    from sdk import MemoryClient

    # Direct file access
    client = MemoryClient(memory_dir="/path/to/project/memory")
    client.log({...})

    # HTTP API
    client = MemoryClient(base_url="http://localhost:8765", api_key="secret")
    client.log({...})
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Any

from agent_memory.sdk.exceptions import APIError, ConflictError, NotFoundError, ValidationError

from agent_memory import cli as filter
from agent_memory.engine.constants import DEFAULTS
from agent_memory.engine.consolidation import consolidate_core
from agent_memory.engine.handlers import log_core, resolve_core, stats_core, update_core
from agent_memory.engine.metrics import _consolidation_stats, _logging_stats, _retrieval_stats, read_metrics
from agent_memory.engine.models import LearningEntry
from agent_memory.engine.retrieval import retrieve_core


# ---------------------------------------------------------------------------
# Transport helpers
# ---------------------------------------------------------------------------


def _build_ctx(paths):
    """Load config and build a lowercase ctx dict for engine functions."""
    # filter.load_config() reads from the module-level PATHS singleton.
    filter.PATHS = paths
    config = filter.load_config()
    ctx = {k.lower(): v for k, v in DEFAULTS.items()}
    if config:
        ctx.update({k.lower(): v for k, v in config.items()})
    return ctx


def _validate_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Run client-side Pydantic validation for log/update payloads."""
    try:
        validated = LearningEntry(**entry)
    except Exception as exc:
        raise ValidationError(str(exc), code="VALIDATION_ERROR") from exc
    return validated.model_dump(exclude_unset=True)


def _local_error_from_result(result: dict[str, Any], ref: str | None = None) -> None:
    """Raise the appropriate exception for a non-zero engine result."""
    status = result.get("status", "error")
    message = result.get("message", f"engine error: {status}")
    kwargs = {
        "code": status.upper(),
        "suggested_action": result.get("suggested_action", ""),
        "entry_ref": ref or (result.get("entry", {}).get("ts") if isinstance(result.get("entry"), dict) else None),
    }
    if status in ("quarantined", "invalid_ts"):
        raise ValidationError(message, **kwargs)
    if status == "not_found":
        raise NotFoundError(message, **kwargs)
    if status == "archive_exists":
        raise ConflictError(message, **kwargs)
    raise APIError(message, **kwargs)


# ---------------------------------------------------------------------------
# Local transport
# ---------------------------------------------------------------------------


class _LocalTransport:
    """Direct file-access transport backed by engine *_core functions."""

    def __init__(self, paths, ctx):
        self.paths = paths
        self.ctx = ctx

    def retrieve(self, step, components, files, domain):
        return retrieve_core(
            step,
            list(components) if components else [],
            list(files) if files else [],
            domain or "",
            self.ctx,
            self.paths,
        )

    def log(self, entry):
        result = log_core(json.dumps(entry), self.paths, self.ctx)
        if result.get("exit_code", 0) != 0:
            _local_error_from_result(result)
        return result

    def update(self, ts, entry):
        result = update_core(ts, json.dumps(entry), self.paths, self.ctx)
        if result.get("exit_code", 0) != 0:
            _local_error_from_result(result, ref=ts)
        return result

    def resolve(self, ts):
        result = resolve_core(ts, self.paths)
        if result.get("exit_code", 0) != 0:
            _local_error_from_result(result, ref=ts)
        return result

    def stats(self):
        result = stats_core(self.paths, ctx=self.ctx)
        result.pop("exit_code", None)
        result.pop("status", None)
        return result

    def metrics(self, since=None, type=None):
        since_dt = None
        if since is not None:
            if isinstance(since, str):
                since_dt = datetime.fromisoformat(since + "T00:00:00+00:00")
            elif isinstance(since, datetime):
                since_dt = since
        events = read_metrics(self.paths, since=since_dt)
        if type:
            filtered = [e for e in events if e.get("event_type") == type]
            if type == "retrieval":
                return {"total_events": len(filtered), "retrieval": _retrieval_stats(filtered)}
            if type == "log":
                return {"total_events": len(filtered), "logging": _logging_stats(filtered)}
            if type == "consolidate":
                return {"total_events": len(filtered), "consolidation": _consolidation_stats(filtered)}
            if type in ("stats", "review_agents"):
                return {"total_events": len(filtered), "events": filtered}
            return {"total_events": len(filtered), "events": filtered}
        r = _retrieval_stats([e for e in events if e.get("event_type") == "retrieval"])
        l = _logging_stats([e for e in events if e.get("event_type") == "log"])
        c = _consolidation_stats([e for e in events if e.get("event_type") == "consolidate"])
        return {"total_events": len(events), "retrieval": r, "logging": l, "consolidation": c}

    def consolidate(self, sprint_number=None, force=False):
        result = consolidate_core(sprint_number, False, force, self.paths, self.ctx)
        if result.get("exit_code", 0) != 0:
            _local_error_from_result(result)
        return result


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------


class _HTTPError:
    """Parse HTTP error bodies and raise typed exceptions."""

    @staticmethod
    def raise_for_status(response, ref=None):
        if response.status_code < 400:
            return
        try:
            payload = response.json()
        except Exception:
            payload = {}
        # Server returns errors in two formats:
        # - HTTPException: {"detail": {"code": ..., "message": ...}}
        # - Middleware (401): flat {"code": ..., "message": ...}
        detail = payload.get("detail")
        if not isinstance(detail, dict):
            detail = payload if isinstance(payload, dict) else {}
        code = detail.get("code") or str(response.status_code)
        message = detail.get("message") or response.reason_phrase
        kwargs = {
            "code": code,
            "suggested_action": detail.get("suggested_action", ""),
            "entry_ref": detail.get("entry_ref") or ref,
        }
        if response.status_code in (400, 422):
            raise ValidationError(message, **kwargs)
        if response.status_code == 404:
            raise NotFoundError(message, **kwargs)
        if response.status_code == 409:
            raise ConflictError(message, **kwargs)
        raise APIError(message, **kwargs)


class _HTTPTransport:
    """Synchronous HTTP transport using httpx."""

    def __init__(self, base_url, api_key=None):
        import httpx

        headers = {}
        if api_key:
            headers["X-API-Key"] = api_key
        self._client = httpx.Client(base_url=base_url, headers=headers, timeout=30.0)

    def _request(self, method, path, **kwargs):
        response = self._client.request(method, path, **kwargs)
        _HTTPError.raise_for_status(response)
        return response.json()

    def retrieve(self, step, components, files, domain):
        params: dict[str, Any] = {"step": step}
        if components:
            params["components"] = ",".join(components)
        if files:
            params["files"] = ",".join(files)
        if domain:
            params["domain"] = domain
        return self._request("GET", "/api/retrieve", params=params)

    def log(self, entry):
        return self._request("POST", "/api/log", json={"entry": entry})

    def update(self, ts, entry):
        return self._request("POST", "/api/update", json={"ts": ts, "entry": entry})

    def resolve(self, ts):
        return self._request("POST", "/api/resolve", json={"ts": ts})

    def stats(self):
        return self._request("GET", "/api/stats")

    def metrics(self, since=None, type=None):
        params: dict[str, Any] = {}
        if since is not None:
            if isinstance(since, datetime):
                params["since"] = since.strftime("%Y-%m-%d")
            else:
                params["since"] = since
        if type is not None:
            params["type"] = type
        return self._request("GET", "/api/metrics", params=params)

    def consolidate(self, sprint_number=None, force=False):
        body: dict[str, Any] = {"force": force}
        if sprint_number is not None:
            body["sprint_number"] = sprint_number
        return self._request("POST", "/api/consolidate", json=body)

    def close(self):
        self._client.close()


class _AsyncHTTPTransport:
    """Asynchronous HTTP transport using httpx.AsyncClient."""

    def __init__(self, base_url, api_key=None):
        import httpx

        headers = {}
        if api_key:
            headers["X-API-Key"] = api_key
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30.0)

    @classmethod
    def _with_client(cls, client):
        """Create a transport wrapping an existing httpx.AsyncClient (for tests)."""
        t = cls.__new__(cls)
        t._client = client
        return t

    async def _request(self, method, path, **kwargs):
        response = await self._client.request(method, path, **kwargs)
        _HTTPError.raise_for_status(response)
        return response.json()

    async def retrieve(self, step, components, files, domain):
        params: dict[str, Any] = {"step": step}
        if components:
            params["components"] = ",".join(components)
        if files:
            params["files"] = ",".join(files)
        if domain:
            params["domain"] = domain
        return await self._request("GET", "/api/retrieve", params=params)

    async def log(self, entry):
        return await self._request("POST", "/api/log", json={"entry": entry})

    async def update(self, ts, entry):
        return await self._request("POST", "/api/update", json={"ts": ts, "entry": entry})

    async def resolve(self, ts):
        return await self._request("POST", "/api/resolve", json={"ts": ts})

    async def stats(self):
        return await self._request("GET", "/api/stats")

    async def metrics(self, since=None, type=None):
        params: dict[str, Any] = {}
        if since is not None:
            if isinstance(since, datetime):
                params["since"] = since.strftime("%Y-%m-%d")
            else:
                params["since"] = since
        if type is not None:
            params["type"] = type
        return await self._request("GET", "/api/metrics", params=params)

    async def consolidate(self, sprint_number=None, force=False):
        body: dict[str, Any] = {"force": force}
        if sprint_number is not None:
            body["sprint_number"] = sprint_number
        return await self._request("POST", "/api/consolidate", json=body)

    async def close(self):
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Public clients
# ---------------------------------------------------------------------------


class MemoryClient:
    """Synchronous SDK client for the Agent Memory Engine.

    Use `memory_dir` for direct file access or `base_url` for HTTP API access.
    Provide `api_key` or set `AGENT_MEMORY_API_KEY` for authenticated servers.
    """

    def __init__(
        self,
        *,
        memory_dir: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        if memory_dir is None and base_url is None:
            raise ValueError("Either memory_dir or base_url must be provided")
        if memory_dir is not None and base_url is not None:
            raise ValueError("Only one of memory_dir or base_url may be provided")

        self._api_key = api_key or os.environ.get("AGENT_MEMORY_API_KEY")
        self._transport: _LocalTransport | _HTTPTransport

        if memory_dir is not None:
            paths = filter.setup_paths(memory_dir)
            ctx = _build_ctx(paths)
            self._transport = _LocalTransport(paths, ctx)
        else:
            url = (base_url or "").rstrip("/")
            if not url:
                raise ValueError("base_url must not be empty")
            self._transport = _HTTPTransport(url, api_key=self._api_key)

    def retrieve(
        self,
        step: int,
        *,
        components: list[str] | None = None,
        files: list[str] | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
        return self._transport.retrieve(step, components, files, domain)

    def log(self, entry: dict[str, Any]) -> dict[str, Any]:
        validated = _validate_entry(entry)
        return self._transport.log(validated)

    def update(self, ts: str, entry: dict[str, Any]) -> dict[str, Any]:
        validated = _validate_entry(entry)
        return self._transport.update(ts, validated)

    def resolve(self, ts: str) -> dict[str, Any]:
        return self._transport.resolve(ts)

    def stats(self) -> dict[str, Any]:
        return self._transport.stats()

    def metrics(self, *, since: str | datetime | None = None, type: str | None = None) -> dict[str, Any]:
        return self._transport.metrics(since, type)

    def consolidate(self, *, sprint_number: int | None = None, force: bool = False) -> dict[str, Any]:
        return self._transport.consolidate(sprint_number, force)

    def close(self) -> None:
        if isinstance(self._transport, _HTTPTransport):
            self._transport.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


class AsyncMemoryClient:
    """Asynchronous SDK client for the Agent Memory Engine.

    Mirrors `MemoryClient` but uses `httpx.AsyncClient`. Local file operations
    run in a thread so they do not block the event loop.
    """

    def __init__(
        self,
        *,
        memory_dir: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        if memory_dir is None and base_url is None:
            raise ValueError("Either memory_dir or base_url must be provided")
        if memory_dir is not None and base_url is not None:
            raise ValueError("Only one of memory_dir or base_url may be provided")

        self._api_key = api_key or os.environ.get("AGENT_MEMORY_API_KEY")
        self._memory_dir = memory_dir
        self._base_url = base_url
        self._paths = None
        self._ctx = None
        self._local_transport: _LocalTransport | None = None
        self._http_transport: _AsyncHTTPTransport | None = None

        if memory_dir is not None:
            self._paths = filter.setup_paths(memory_dir)
            self._ctx = _build_ctx(self._paths)
        else:
            url = (base_url or "").rstrip("/")
            if not url:
                raise ValueError("base_url must not be empty")
            self._base_url = url
            self._http_transport = _AsyncHTTPTransport(url, api_key=self._api_key)

    def _get_local(self) -> _LocalTransport:
        if self._local_transport is None:
            self._local_transport = _LocalTransport(self._paths, self._ctx)
        return self._local_transport

    async def retrieve(
        self,
        step: int,
        *,
        components: list[str] | None = None,
        files: list[str] | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
        if self._http_transport is not None:
            return await self._http_transport.retrieve(step, components, files, domain)
        return await asyncio.to_thread(self._get_local().retrieve, step, components, files, domain)

    async def log(self, entry: dict[str, Any]) -> dict[str, Any]:
        validated = _validate_entry(entry)
        if self._http_transport is not None:
            return await self._http_transport.log(validated)
        return await asyncio.to_thread(self._get_local().log, validated)

    async def update(self, ts: str, entry: dict[str, Any]) -> dict[str, Any]:
        validated = _validate_entry(entry)
        if self._http_transport is not None:
            return await self._http_transport.update(ts, validated)
        return await asyncio.to_thread(self._get_local().update, ts, validated)

    async def resolve(self, ts: str) -> dict[str, Any]:
        if self._http_transport is not None:
            return await self._http_transport.resolve(ts)
        return await asyncio.to_thread(self._get_local().resolve, ts)

    async def stats(self) -> dict[str, Any]:
        if self._http_transport is not None:
            return await self._http_transport.stats()
        return await asyncio.to_thread(self._get_local().stats)

    async def metrics(self, *, since: str | datetime | None = None, type: str | None = None) -> dict[str, Any]:
        if self._http_transport is not None:
            return await self._http_transport.metrics(since, type)
        return await asyncio.to_thread(self._get_local().metrics, since, type)

    async def consolidate(self, *, sprint_number: int | None = None, force: bool = False) -> dict[str, Any]:
        if self._http_transport is not None:
            return await self._http_transport.consolidate(sprint_number, force)
        return await asyncio.to_thread(self._get_local().consolidate, sprint_number, force)

    async def close(self) -> None:
        if self._http_transport is not None:
            await self._http_transport.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
        return False
