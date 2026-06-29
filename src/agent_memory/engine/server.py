# Copyright (C) 2026 Mnemoq
# SPDX-License-Identifier: AGPL-3.0-or-later

"""FastAPI HTTP server for the Agent Memory Engine.

Core CRUD endpoints live here. Dashboard endpoints are in dashboard_api.py,
included only when dashboard=True.

Run with: python filter.py --serve [--port 8765]
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agent_memory.engine.agents_review import review_agents_core
from agent_memory.engine.auto_learn import auto_learn_core
from agent_memory.engine.consolidation import consolidate_core
from agent_memory.engine.handlers import log_core, resolve_core, stats_core, update_core
from agent_memory.engine.metrics import _consolidation_stats, _logging_stats, _retrieval_stats, read_metrics
from agent_memory.engine.models import (
    ConsolidateRequest,
    ErrorResponse,
    LogRequest,
    ResolveRequest,
    UpdateRequest,
)
from agent_memory.engine.retrieval import retrieve_core


class EventHub:
    """Simple in-memory WebSocket event hub. ponytail: single-process, no Redis needed."""

    _SEEN_MAX = 500

    def __init__(self):
        self._clients: list[WebSocket] = []
        self.last_alert_count: int = 0
        self._seen: set[str] = set()
        self._seen_order: list[str] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._clients.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self._clients:
            self._clients.remove(ws)

    @staticmethod
    def _seen_key(event: dict) -> str:
        """Normalize API-hook and metrics.jsonl events into a single key."""
        et = event.get("event") or event.get("event_type", "unknown")
        status = (event.get("status") or event.get("outcome", "")).lower()
        if et in ("log",):
            entry = event.get("entry") or {}
            detail = entry.get("ts") or event.get("entry_ts", "")
            return f"log|{detail}|{status}"
        if et in ("resolve",):
            ts = event.get("ts", "")
            return f"resolve|{ts}|{status}"
        if et in ("consolidate",):
            sprint = event.get("sprint") or event.get("sprint_number", "")
            return f"consolidate|{sprint}|{status}"
        if et in ("config_update",):
            return f"config_update|{event.get('ts', '')}"
        if et in ("alert",):
            return f"alert|{event.get('message', '')}"
        return f"{et}|{event.get('ts', '')}"

    def _mark_seen(self, key: str) -> bool:
        """Add key to the rolling seen set. Returns True if newly added."""
        if key in self._seen:
            return False
        self._seen.add(key)
        self._seen_order.append(key)
        if len(self._seen_order) > self._SEEN_MAX:
            old = self._seen_order.pop(0)
            self._seen.discard(old)
        return True

    async def broadcast(self, event: dict):
        key = self._seen_key(event)
        if not self._mark_seen(key):
            return
        dead = []
        for ws in self._clients:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def watch_metrics_file(self, get_paths):
        """Watch metrics.jsonl for CLI-triggered events and broadcast them.

        Accepts a callable so project switches are picked up without restarting.
        ponytail: uses broadcast() which deduplicates against events already
        pushed instantly by the API post-call hooks.
        """
        from agent_memory.engine.metrics import _metrics_path

        state = {"size": 0, "mtime": 0.0}
        while True:
            await asyncio.sleep(2)
            try:
                mp = _metrics_path(get_paths())
                if not os.path.exists(mp):
                    state["size"] = 0
                    state["mtime"] = 0.0
                    continue
                mtime = os.path.getmtime(mp)
                if mtime == state["mtime"]:
                    continue
                state["mtime"] = mtime
                size = os.path.getsize(mp)
                if size < state["size"]:
                    state["size"] = 0
                with open(mp, encoding="utf-8") as f:
                    f.seek(state["size"])
                    new_lines = f.readlines()
                state["size"] = size
                for line in new_lines:
                    if not line.strip():
                        continue
                    try:
                        evt = json.loads(line)
                        et = evt.get("event_type", "unknown")
                        # Resolve API hook uses target entry ts; metrics event ts is the event timestamp
                        ts = evt.get("target_ts") if et == "resolve" else evt.get("ts", "")
                        await self.broadcast({
                            "event": et,
                            "ts": ts or "",
                            "outcome": evt.get("outcome", ""),
                            "entry_ts": evt.get("entry_ts", ""),
                            "sprint": evt.get("sprint_number", ""),
                            "source": "file_watcher",
                        })
                    except json.JSONDecodeError:
                        pass
            except OSError:
                pass


async def _check_and_broadcast_alerts(paths, ctx, event_hub):
    """Check for new alerts after a write op and broadcast any new ones via WS."""
    try:
        from agent_memory.engine.analysis import alerts_list, get_metrics_data
        stats = stats_core(paths, emit_event=False, ctx=ctx)
        stats.pop("exit_code", None)
        stats.pop("status", None)
        _, r, log_stats, c = get_metrics_data(paths)
        md = {"retrieval": r, "logging": log_stats, "consolidation": c}
        current = alerts_list(stats, md)
        current_count = len(current)
        if current_count > event_hub.last_alert_count:
            for a in current[event_hub.last_alert_count:]:
                await event_hub.broadcast({
                    "event": "alert",
                    "severity": a.get("type", "warning"),
                    "message": a.get("message", ""),
                })
        event_hub.last_alert_count = current_count
    except Exception:
        pass


def create_app(paths, ctx, api_key: str | None = None, dashboard: bool = False):
    """Create and return the FastAPI app.

    Args:
        paths: Paths dataclass from filter.py
        ctx: context dict from filter.py
        api_key: optional API key for authentication
        dashboard: if True, mount static dashboard files at /
    """
    from agent_memory.engine_version import get_engine_version

    def _read_version() -> str:
        return get_engine_version()

    event_hub = EventHub()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if dashboard:
            watcher = asyncio.create_task(event_hub.watch_metrics_file(lambda: paths))
        yield
        if dashboard:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass

    app = FastAPI(
        title="Agent Memory Engine",
        description="HTTP API for memory retrieval, logging, and consolidation.",
        version=_read_version(),
        lifespan=lifespan,
    )

    # Cache invalidation — no-op when dashboard is off
    if dashboard:
        from agent_memory.engine.analysis import invalidate_metrics_cache
    else:
        def invalidate_metrics_cache():
            pass

    if api_key:

        @app.middleware("http")
        async def check_api_key(request: Request, call_next):
            if request.url.path.startswith("/api/"):
                token = request.headers.get("X-API-Key", "")
                if token != api_key:
                    return JSONResponse(
                        status_code=401,
                        content=ErrorResponse(
                            code="UNAUTHORIZED",
                            message="Invalid or missing X-API-Key header.",
                            suggested_action="Provide the correct API key in the X-API-Key header.",
                        ).model_dump(),
                    )
            return await call_next(request)

    # -- Health --

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": _read_version()}

    # -- Retrieve --

    @app.get("/api/retrieve")
    async def retrieve(
        step: int = Query(..., ge=1),
        components: str = Query(""),
        files: str = Query(""),
        domain: str = Query(""),
    ):
        task_components = [c.strip() for c in components.split(",") if c.strip()] if components else []
        task_files = [f.strip() for f in files.split(",") if f.strip()] if files else []
        result = retrieve_core(step, task_components, task_files, domain, ctx, paths)
        return result

    # -- Log --

    @app.post("/api/log")
    async def log_entry(req: LogRequest):
        json_str = json.dumps(req.entry)
        result = log_core(json_str, paths, ctx)
        status_map = {
            "quarantined": 422,
            "not_found": 404,
            "invalid_ts": 400,
        }
        code = status_map.get(result.get("status"), 200)
        if code != 200:
            raise HTTPException(
                status_code=code,
                detail=ErrorResponse(
                    code=result.get("status", "ERROR").upper(),
                    message=result.get("message", "Unknown error"),
                    entry_ref=(result.get("matched_entry", {}).get("ts")
                              if isinstance(result.get("matched_entry"), dict) else None),
                ).model_dump(),
            )
        await event_hub.broadcast({"event": "log", "status": result.get("status"), "entry": result.get("entry")})
        invalidate_metrics_cache()
        if dashboard:
            await _check_and_broadcast_alerts(paths, ctx, event_hub)
        return result

    # -- Update --

    @app.post("/api/update")
    async def update_entry(req: UpdateRequest):
        json_str = json.dumps(req.entry)
        result = update_core(req.ts, json_str, paths, ctx)
        status_map = {
            "quarantined": 422,
            "not_found": 404,
            "invalid_ts": 400,
        }
        code = status_map.get(result.get("status"), 200)
        if code != 200:
            raise HTTPException(
                status_code=code,
                detail=ErrorResponse(
                    code=result.get("status", "ERROR").upper(),
                    message=result.get("message", "Unknown error"),
                    entry_ref=req.ts,
                ).model_dump(),
            )
        return result

    # -- Resolve --

    @app.post("/api/resolve")
    async def resolve_entry(req: ResolveRequest):
        result = resolve_core(req.ts, paths)
        status_map = {
            "not_found": 404,
            "invalid_ts": 400,
        }
        code = status_map.get(result.get("status"), 200)
        if code != 200:
            raise HTTPException(
                status_code=code,
                detail=ErrorResponse(
                    code=result.get("status", "ERROR").upper(),
                    message=result.get("message", "Unknown error"),
                    entry_ref=req.ts,
                ).model_dump(),
            )
        await event_hub.broadcast({"event": "resolve", "ts": req.ts, "status": result.get("status")})
        invalidate_metrics_cache()
        if dashboard:
            await _check_and_broadcast_alerts(paths, ctx, event_hub)
        return result

    # -- Stats --

    @app.get("/api/stats")
    async def stats():
        result = stats_core(paths, ctx=ctx)
        result.pop("exit_code", None)
        result.pop("status", None)
        return result

    # -- Metrics --

    @app.get("/api/metrics")
    async def metrics(
        since: str | None = Query(None),
        type: str | None = Query(None, description="Filter by event_type: retrieval, log, consolidate"),
    ):
        since_dt = None
        if since:
            try:
                since_dt = datetime.fromisoformat(since + "T00:00:00+00:00")
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=ErrorResponse(
                        code="BAD_REQUEST",
                        message=f"Invalid since date: {since}",
                        suggested_action="Use YYYY-MM-DD format.",
                    ).model_dump(),
                )
        events = read_metrics(paths, since=since_dt)
        if type:
            filtered = [e for e in events if e.get("event_type") == type]
            if type == "retrieval":
                return {"total_events": len(filtered), "retrieval": _retrieval_stats(filtered)}
            elif type == "log":
                return {"total_events": len(filtered), "logging": _logging_stats(filtered)}
            elif type == "consolidate":
                return {"total_events": len(filtered), "consolidation": _consolidation_stats(filtered)}
            elif type in ("stats", "review_agents"):
                return {"total_events": len(filtered), "events": filtered}
            return {"total_events": len(filtered), "events": filtered}
        r = _retrieval_stats([e for e in events if e.get("event_type") == "retrieval"])
        log_stats = _logging_stats([e for e in events if e.get("event_type") == "log"])
        c = _consolidation_stats([e for e in events if e.get("event_type") == "consolidate"])
        return {
            "total_events": len(events),
            "retrieval": r,
            "logging": log_stats,
            "consolidation": c,
        }

    # -- Auto-Learn --

    @app.post("/api/auto-learn")
    async def auto_learn():
        result = auto_learn_core(paths, ctx)
        await event_hub.broadcast({"event": "auto-learn", "status": result.get("status"),
                                   "generated": len(result.get("generated", []))})
        invalidate_metrics_cache()
        if dashboard:
            await _check_and_broadcast_alerts(paths, ctx, event_hub)
        return result

    # -- Consolidate --

    @app.post("/api/consolidate")
    async def consolidate(req: ConsolidateRequest):
        result = consolidate_core(req.sprint_number, False, req.force, paths, ctx)
        status_map = {
            "archive_exists": 409,
            "no_entries": 200,
        }
        code = status_map.get(result.get("status"), 200)
        if code == 409:
            raise HTTPException(
                status_code=409,
                detail=ErrorResponse(
                    code="ARCHIVE_EXISTS",
                    message=result.get("message", "Archive already exists."),
                    suggested_action="Use force=true to overwrite, or specify a different sprint_number.",
                ).model_dump(),
            )
        # Run auto-learning only on successful consolidation
        if result.get("status") == "reported":
            try:
                al_result = auto_learn_core(paths, ctx)
                result["auto_learning"] = {
                    "generated": len(al_result.get("generated", [])),
                    "deduped": al_result.get("deduped", 0),
                    "skipped": al_result.get("skipped", 0),
                    "capped": al_result.get("capped", False),
                    "git_available": al_result.get("git_available", True),
                }
            except Exception:
                result["auto_learning"] = {"error": "auto-learning failed"}
        await event_hub.broadcast({"event": "consolidate",
                                 "sprint": result.get("sprint_number"),
                                 "status": result.get("status")})
        invalidate_metrics_cache()
        if dashboard:
            await _check_and_broadcast_alerts(paths, ctx, event_hub)
        return result

    # -- Review Agents --

    @app.get("/api/review-agents")
    async def review_agents(step: int = Query(..., ge=1), threshold: int = Query(10, ge=1)):
        result = review_agents_core(step, threshold, paths)
        result.pop("exit_code", None)
        return result

    # -- WebSocket --

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket):
        await event_hub.connect(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            event_hub.disconnect(ws)

    # -- Dashboard router + static files --

    if dashboard:
        from agent_memory.engine.dashboard_api import create_dashboard_router

        def _on_project_switch(new_paths, new_ctx):
            nonlocal paths, ctx
            paths = new_paths
            ctx = new_ctx

        router = create_dashboard_router(paths, ctx, event_hub, invalidate_metrics_cache, on_switch=_on_project_switch)
        app.include_router(router)

        # ponytail: check deployed location first, then source tree
        _candidates = [
            os.path.join(os.path.dirname(__file__), "..", "dashboard", "static"),
            os.path.join(os.path.dirname(__file__), "dashboard", "static"),
        ]
        static_dir = next((os.path.abspath(p) for p in _candidates if os.path.isdir(p)), None)
        if static_dir:
            app.mount("/css", StaticFiles(directory=os.path.join(static_dir, "css")), name="css")
            app.mount("/js", StaticFiles(directory=os.path.join(static_dir, "js")), name="js")

            _index_html = None
            _index_path = os.path.join(static_dir, "index.html")
            if os.path.isfile(_index_path):
                with open(_index_path, encoding="utf-8") as f:
                    _index_html = f.read()

            @app.get("/")
            async def serve_dashboard():
                if _index_html is not None:
                    return HTMLResponse(_index_html)
                return HTMLResponse("<h1>Dashboard not found</h1>", status_code=404)

    return app
