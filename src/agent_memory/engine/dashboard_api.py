"""Dashboard API router for the Agent Memory Engine.

All dashboard-specific endpoints live on an APIRouter created by
``create_dashboard_router(paths, ctx, event_hub, invalidate_cache)``.
Included by ``server.py`` only when ``dashboard=True``.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from agent_memory.engine.analysis import (
    alerts_list,
    get_metrics_data,
    health_score,
    make_project_paths,
    parse_since,
    recommendations,
)
from agent_memory.engine.consolidation import (
    check_staleness,
    detect_contradictions,
    is_promotion_candidate,
    review_quarantine,
)
from agent_memory.engine.handlers import stats_core
from agent_memory.engine.io import _read_raw_jsonl, read_learnings_for_dashboard
from agent_memory.engine.metrics import (
    _agent_stats,
    _consolidation_stats,
    _dedup_stats,
    _get_project_id,
    _lifecycle_stats,
    _load_project_paths,
    _logging_stats,
    _retrieval_stats,
    _trend_stats,
    read_metrics,
)
from agent_memory.engine.models import ErrorResponse


def _validate_config_update(body):
    """Validate the fields the dashboard actually edits. Unknown fields pass through."""
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(code="BAD_REQUEST", message="Config must be a JSON object.").model_dump(),
        )

    if "data_source" in body and body["data_source"] not in ("real", "fakes"):
        raise HTTPException(status_code=400,
            detail=ErrorResponse(code="BAD_REQUEST",
                message="data_source must be 'real' or 'fakes'.").model_dump())

    if "project_name" in body:
        if not isinstance(body["project_name"], str) or not body["project_name"].strip():
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(code="BAD_REQUEST",
                         message="project_name must be a non-empty string.").model_dump(),
            )

    if "api_key" in body:
        if body["api_key"] is not None and not isinstance(body["api_key"], str):
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(code="BAD_REQUEST", message="api_key must be a string or null.").model_dump(),
            )

    for key in ("valid_domains", "valid_source_agents", "retrieval_only_agents"):
        if key in body:
            val = body[key]
            if val is not None and not (isinstance(val, list) and all(isinstance(x, str) for x in val)):
                raise HTTPException(
                    status_code=400,
                    detail=ErrorResponse(code="BAD_REQUEST",
                             message=f"{key} must be a list of strings or null.").model_dump(),
                )

    tuning = body.get("tuning")
    if tuning is not None:
        if not isinstance(tuning, dict):
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(code="BAD_REQUEST", message="tuning must be an object.").model_dump(),
            )
        for key in ("score_threshold", "decay_rate"):
            if key in tuning:
                try:
                    val = float(tuning[key])
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=400,
                        detail=ErrorResponse(code="BAD_REQUEST", message=f"{key} must be a float.").model_dump(),
                    )
                if not (0 <= val <= 1):
                    raise HTTPException(
                        status_code=400,
                        detail=ErrorResponse(code="BAD_REQUEST",
                                 message=f"{key} must be between 0 and 1.").model_dump(),
                    )
        for key in ("component_weight", "file_weight", "domain_weight", "no_match_weight"):
            if key in tuning:
                try:
                    val = float(tuning[key])
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=400,
                        detail=ErrorResponse(code="BAD_REQUEST", message=f"{key} must be a float.").model_dump(),
                    )
                if val < 0:
                    raise HTTPException(
                        status_code=400,
                        detail=ErrorResponse(code="BAD_REQUEST", message=f"{key} must be non-negative.").model_dump(),
                    )
        for key in ("max_warnings", "max_patterns"):
            if key in tuning:
                val = tuning[key]
                if not isinstance(val, int) or val <= 0:
                    raise HTTPException(
                        status_code=400,
                        detail=ErrorResponse(code="BAD_REQUEST",
                                 message=f"{key} must be a positive integer.").model_dump(),
                    )
        for key, val in tuning.items():
            if key.startswith("retention_"):
                if not isinstance(val, int) or val <= 0:
                    raise HTTPException(
                        status_code=400,
                        detail=ErrorResponse(code="BAD_REQUEST",
                                 message=f"{key} must be a positive integer.").model_dump(),
                    )


def _list_archives(paths):
    """Shared archive listing used by /api/metrics/archive and /api/consolidation."""
    if not os.path.isdir(paths.archive_dir):
        return []
    archives = []
    for fname in sorted(os.listdir(paths.archive_dir)):
        if fname.endswith(".jsonl"):
            fpath = os.path.join(paths.archive_dir, fname)
            with open(fpath, encoding="utf-8") as f:
                count = sum(1 for line in f if line.strip())
            archives.append({"file": fname, "entries": count})
    return archives


def create_dashboard_router(paths, ctx, event_hub, invalidate_cache, on_switch=None):
    """Create and return an APIRouter with all dashboard endpoints.

    Args:
        paths: Paths dataclass from filter.py
        ctx: context dict from filter.py
        event_hub: EventHub instance for WebSocket broadcasts
        invalidate_cache: callable to invalidate the metrics cache
        on_switch: optional callback(new_paths, new_ctx) to propagate project switches
    """
    router = APIRouter()

    # -- Quarantine --

    @router.get("/api/metrics/quarantine")
    async def get_quarantine():
        entries = _read_raw_jsonl(paths.quarantine_path)
        return {"count": len(entries), "entries": entries}

    # -- Archive --

    @router.get("/api/metrics/archive")
    async def get_archive():
        archives = _list_archives(paths)
        return {"count": len(archives), "archives": archives}

    # -- Consolidation state --

    @router.get("/api/consolidation")
    async def consolidation_state():
        entries = read_learnings_for_dashboard(paths, ctx)
        unresolved = [e for e in entries if not e.get("resolved", False)]
        stats = stats_core(paths, emit_event=False, ctx=ctx)
        stats.pop("exit_code", None)
        stats.pop("status", None)

        last_sprint = None
        last_consolidation_ts = None
        try:
            if os.path.exists(paths.session_file):
                with open(paths.session_file, encoding="utf-8") as f:
                    session = json.load(f)
                last_sprint = session.get("sprint")
                last_consolidation_ts = session.get("timestamp")
        except (OSError, json.JSONDecodeError):
            pass

        if last_sprint is None:
            cons_events = read_metrics(paths, event_type="consolidate")
            if cons_events:
                last_sprint = cons_events[-1].get("sprint_number")
                last_consolidation_ts = cons_events[-1].get("ts")

        current_step = max((e.get("step", 0) for e in unresolved), default=1)
        candidates = []
        for e in unresolved:
            is_candidate, score = is_promotion_candidate(e, current_step, ctx)
            if is_candidate:
                candidates.append({"score": score, "entry": e})
        candidates.sort(key=lambda x: x["score"], reverse=True)

        contradictions = detect_contradictions(unresolved)
        quarantine_count, quarantine_breakdown, quarantine_recent = review_quarantine(paths)

        stale_entries = []
        for e in unresolved:
            if not e.get("commit") or not e.get("files_touched"):
                continue
            is_stale, lines_changed, error = check_staleness(e, paths.repo_root, ctx)
            if is_stale or error:
                stale_entries.append({
                    "entry": e,
                    "is_stale": is_stale,
                    "lines_changed": lines_changed,
                    "error": error,
                    "diff_url": f"/api/diff?ts={e.get('ts')}",
                })

        unresolved_threshold = stats.get("sleep_cycle_unresolved_threshold", 20)
        return {
            "unresolved": len(unresolved),
            "total_entries": len(entries),
            "sleep_cycle_due": stats.get("sleep_cycle_due", len(unresolved) > unresolved_threshold),
            "last_sprint": last_sprint,
            "last_consolidation_ts": last_consolidation_ts,
            "promotion_candidates": candidates,
            "contradictions": contradictions,
            "stale_entries": stale_entries,
            "quarantine": {
                "count": quarantine_count,
                "breakdown": quarantine_breakdown,
                "recent": quarantine_recent,
            },
            "archive_history": _list_archives(paths),
        }

    @router.get("/api/diff")
    async def diff_entry(ts: str = Query(...)):
        entry = None
        for e in read_learnings_for_dashboard(paths, ctx):
            if e.get("ts") == ts:
                entry = e
                break
        if not entry:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(code="NOT_FOUND", message=f"No entry with ts={ts}").model_dump(),
            )
        if not entry.get("commit") or not entry.get("files_touched"):
            return {"diff": "", "error": "Entry has no commit or files_touched for diff"}
        commit = entry["commit"]
        if not all(c in "0123456789abcdefABCDEF" for c in commit) or len(commit) < 7:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(code="BAD_REQUEST", message=f"Invalid commit hash: {commit}").model_dump(),
            )
        try:
            result = subprocess.run(
                ["git", "diff", f"{commit}..HEAD", "--"] + entry["files_touched"],
                capture_output=True, text=True, cwd=paths.repo_root,
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail=ErrorResponse(code="GIT_ERROR", message="git not found").model_dump(),
            )
        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=ErrorResponse(code="GIT_ERROR", message=result.stderr.strip()[:200]).model_dump(),
            )
        return {"diff": result.stdout}

    # -- Learnings list --

    @router.get("/api/learnings")
    async def list_learnings(
        limit: int = Query(0, ge=0, description="Max entries to return (0 = all)"),
        offset: int = Query(0, ge=0, description="Skip first N entries"),
        domain: str | None = Query(None),
        type: str | None = Query(None),
        severity: str | None = Query(None),
        resolved: str | None = Query(None),
        agent: str | None = Query(None),
        step_min: int | None = Query(None),
        step_max: int | None = Query(None),
        q: str | None = Query(None),
    ):
        entries = read_learnings_for_dashboard(paths, ctx)
        if domain:
            entries = [e for e in entries if e.get("domain") == domain]
        if type:
            entries = [e for e in entries if e.get("type") == type]
        if severity:
            entries = [e for e in entries if e.get("severity") == severity]
        if resolved is not None:
            entries = [e for e in entries if str(bool(e.get("resolved", False))) == resolved]
        if agent:
            entries = [e for e in entries if e.get("source_agent") == agent]
        if step_min is not None:
            entries = [e for e in entries if e.get("step", 0) >= step_min]
        if step_max is not None:
            entries = [e for e in entries if e.get("step", 0) <= step_max]
        if q:
            ql = q.lower()
            entries = [e for e in entries
                       if ql in f"{e.get('trigger', '')} {e.get('action', '')} {e.get('reason', '')}".lower()]
        total = len(entries)
        if offset:
            entries = entries[offset:]
        if limit:
            entries = entries[:limit]
        return {"count": total, "returned": len(entries), "entries": entries}

    @router.get("/api/learnings/{ts}")
    async def get_learning(ts: str):
        entries = read_learnings_for_dashboard(paths, ctx)
        for e in entries:
            if e.get("ts") == ts:
                return e
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code="NOT_FOUND",
                message=f"No entry found with ts={ts}",
            ).model_dump(),
        )

    # -- Advanced Metrics --

    @router.get("/api/metrics/summary")
    async def metrics_summary(since: str | None = Query(None)):
        since_dt = parse_since(since)
        events, r, log_stats, c = get_metrics_data(paths, since_dt)
        return {"total_events": len(events), "retrieval": r, "logging": log_stats, "consolidation": c}

    @router.get("/api/metrics/health")
    async def metrics_health():
        stats = stats_core(paths, emit_event=False, ctx=ctx)
        stats.pop("exit_code", None)
        stats.pop("status", None)
        _, r, log_stats, c = get_metrics_data(paths)
        md = {"retrieval": r, "logging": log_stats, "consolidation": c}
        score = health_score(stats, md)
        return {"score": score, "rating": "good" if score >= 70 else "warn" if score >= 40 else "bad"}

    @router.get("/api/metrics/alerts")
    async def metrics_alerts():
        stats = stats_core(paths, emit_event=False, ctx=ctx)
        stats.pop("exit_code", None)
        stats.pop("status", None)
        _, r, log_stats, c = get_metrics_data(paths)
        md = {"retrieval": r, "logging": log_stats, "consolidation": c}
        return {"alerts": alerts_list(stats, md)}

    @router.get("/api/metrics/retrieval-quality")
    async def metrics_retrieval_quality(since: str | None = Query(None)):
        since_dt = parse_since(since)
        events = read_metrics(paths, event_type="retrieval", since=since_dt)
        return _retrieval_stats(events)

    @router.get("/api/metrics/lifecycle")
    async def metrics_lifecycle():
        entries = read_learnings_for_dashboard(paths, ctx)
        return _lifecycle_stats(entries)

    @router.get("/api/metrics/agents")
    async def metrics_agents():
        entries = read_learnings_for_dashboard(paths, ctx)
        log_events = read_metrics(paths, event_type="log")
        return {"agents": _agent_stats(entries, log_events)}

    @router.get("/api/metrics/dedup")
    async def metrics_dedup():
        log_events = read_metrics(paths, event_type="log")
        return _dedup_stats(log_events)

    @router.get("/api/metrics/consolidation-quality")
    async def metrics_consolidation_quality():
        events = read_metrics(paths, event_type="consolidate")
        return _consolidation_stats(events)

    @router.get("/api/metrics/snapshot")
    async def metrics_snapshot():
        entries = read_learnings_for_dashboard(paths, ctx)
        stats = stats_core(paths, emit_event=False, ctx=ctx)
        stats.pop("exit_code", None)
        stats.pop("status", None)
        _, r, log_stats, c = get_metrics_data(paths)
        return {
            "stats": stats,
            "lifecycle": _lifecycle_stats(entries),
            "retrieval": r,
            "logging": log_stats,
            "consolidation": c,
        }

    @router.get("/api/metrics/quarantine")
    async def metrics_quarantine():
        entries = _read_raw_jsonl(paths.quarantine_path)
        reasons = Counter()
        for e in entries:
            r = e.get("reason", "unknown")
            if "JSON parse" in r:
                reasons["JSON parse"] += 1
            elif "Missing" in r or "must be" in r:
                reasons["Schema validation"] += 1
            elif "retrieval-only" in r:
                reasons["Permission"] += 1
            elif "semantic_duplicate" in r:
                reasons["Semantic duplicate"] += 1
            else:
                reasons["Other"] += 1
        return {"total": len(entries), "breakdown": dict(reasons)}

    @router.get("/api/metrics/archive")
    async def metrics_archive():
        archives = _list_archives(paths)
        return {"total": len(archives), "archives": archives}

    @router.get("/api/metrics/config-tuning")
    async def metrics_config_tuning():
        config_path = paths.config_path
        try:
            with open(config_path, encoding="utf-8") as f:
                config = json.load(f)
        except (OSError, json.JSONDecodeError):
            config = {}
        tuning = config.get("tuning", {})
        stats = stats_core(paths, emit_event=False, ctx=ctx)
        stats.pop("exit_code", None)
        stats.pop("status", None)
        _, r, log_stats, c = get_metrics_data(paths)
        md = {"retrieval": r, "logging": log_stats, "consolidation": c}
        recs = recommendations(stats, md, config)
        tuning_recs = [r for r in recs if r["category"] == "config"]
        return {"current_tuning": tuning, "recommendations": tuning_recs}

    @router.get("/api/metrics/recommendations")
    async def metrics_recommendations():
        stats = stats_core(paths, emit_event=False, ctx=ctx)
        stats.pop("exit_code", None)
        stats.pop("status", None)
        config_path = paths.config_path
        try:
            with open(config_path, encoding="utf-8") as f:
                config = json.load(f)
        except (OSError, json.JSONDecodeError):
            config = {}
        _, r, log_stats, c = get_metrics_data(paths)
        md = {"retrieval": r, "logging": log_stats, "consolidation": c}
        return {"recommendations": recommendations(stats, md, config)}

    @router.get("/api/metrics/dashboard")
    async def metrics_dashboard():
        stats = stats_core(paths, emit_event=False, ctx=ctx)
        stats.pop("exit_code", None)
        stats.pop("status", None)
        entries = read_learnings_for_dashboard(paths, ctx)
        events, r, log_stats, c = get_metrics_data(paths)
        md = {"retrieval": r, "logging": log_stats, "consolidation": c}
        trends = _trend_stats(events, days=30)
        config_path = paths.config_path
        try:
            with open(config_path, encoding="utf-8") as f:
                config = json.load(f)
        except (OSError, json.JSONDecodeError):
            config = {}
        return {
            "stats": stats,
            "health": health_score(stats, md),
            "alerts": alerts_list(stats, md),
            "recommendations": recommendations(stats, md, config),
            "trends": trends,
            "retrieval": r,
            "logging": log_stats,
            "consolidation": c,
            "lifecycle": _lifecycle_stats(entries),
        }

    # -- Config --

    @router.get("/api/config")
    async def get_config():
        try:
            with open(paths.config_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise HTTPException(
                status_code=500,
                detail=ErrorResponse(
                    code="CONFIG_ERROR",
                    message=f"Could not read config: {e}",
                ).model_dump(),
            )

    @router.put("/api/config")
    async def update_config(req: Request):
        try:
            body = await req.json()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(
                    code="BAD_REQUEST",
                    message="Invalid JSON body.",
                ).model_dump(),
            )
        _validate_config_update(body)

        def _merge_config(base, update):
            merged = dict(base)
            for key, value in update.items():
                if isinstance(value, dict) and isinstance(merged.get(key), dict):
                    merged[key] = _merge_config(merged[key], value)
                else:
                    merged[key] = value
            return merged

        existing = {}
        if os.path.exists(paths.config_path):
            try:
                with open(paths.config_path, encoding="utf-8") as f:
                    existing = json.load(f)
            except (OSError, json.JSONDecodeError):
                pass
        merged_config = _merge_config(existing, body)

        config_dir = os.path.dirname(paths.config_path)
        tmp_path = os.path.join(config_dir, "config.json.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(merged_config, f, indent=2, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, paths.config_path)
            from agent_memory.engine.constants import DEFAULTS
            with open(paths.config_path, encoding="utf-8") as f:
                fresh = json.load(f)
            new_ctx = {k.lower(): v for k, v in DEFAULTS.items()}
            for k, v in fresh.items():
                if k == "tuning" and isinstance(v, dict):
                    for tk, tv in v.items():
                        new_ctx[tk.lower()] = tv
                else:
                    new_ctx[k.lower()] = v
            nonlocal ctx
            ctx = new_ctx
        except OSError as e:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise HTTPException(
                status_code=500,
                detail=ErrorResponse(
                    code="WRITE_ERROR",
                    message=f"Could not write config: {e}",
                ).model_dump(),
            )
        await event_hub.broadcast({
            "event": "config_update",
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        invalidate_cache()
        return {"status": "ok", "config": merged_config}

    # -- Cross-Project / Fleet --

    @router.get("/api/projects/active")
    async def active_project():
        return {"project_id": _get_project_id(paths), "path": paths.repo_root}

    @router.post("/api/projects/switch")
    async def switch_project(req: Request):
        try:
            body = await req.json()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(code="BAD_REQUEST", message="Invalid JSON body.").model_dump(),
            )
        project_id = body.get("project_id")
        if not project_id or not isinstance(project_id, str):
            raise HTTPException(
                status_code=400,
                detail=ErrorResponse(code="BAD_REQUEST", message="project_id must be a non-empty string.").model_dump(),
            )

        new_paths = None
        for project_root in _load_project_paths():
            pp = make_project_paths(project_root)
            if pp is None:
                continue
            if _get_project_id(pp) == project_id:
                new_paths = pp
                break

        if new_paths is None:
            raise HTTPException(
                status_code=404,
                detail=ErrorResponse(code="NOT_FOUND", message=f"Project '{project_id}' not found.").model_dump(),
            )

        import agent_memory.cli as cli
        from agent_memory.engine.constants import DEFAULTS

        cli.PATHS = new_paths
        config = cli.load_config()
        new_ctx = {k.lower(): v for k, v in DEFAULTS.items()}
        new_ctx.update({k.lower(): v for k, v in config.items()})

        nonlocal paths, ctx
        paths = new_paths
        ctx = new_ctx

        if on_switch:
            on_switch(new_paths, new_ctx)
        invalidate_cache()
        await event_hub.broadcast({
            "event": "project_switch",
            "project_id": project_id,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        return {"status": "ok", "project_id": project_id, "path": str(new_paths.repo_root)}

    @router.get("/api/projects")
    async def list_projects():
        project_paths = _load_project_paths()
        projects = []
        for p in project_paths:
            pp = make_project_paths(p)
            if pp is None:
                continue
            projects.append({"id": _get_project_id(pp), "path": str(p)})
        return {"count": len(projects), "projects": projects}

    @router.get("/api/projects/{id}/metrics/summary")
    async def project_metrics_summary(id: str):
        for project_root in _load_project_paths():
            pp = make_project_paths(project_root)
            if pp is None:
                continue
            if _get_project_id(pp) != id:
                continue
            events = read_metrics(pp)
            r = _retrieval_stats([e for e in events if e.get("event_type") == "retrieval"])
            log_stats = _logging_stats([e for e in events if e.get("event_type") == "log"])
            c = _consolidation_stats([e for e in events if e.get("event_type") == "consolidate"])
            return {"project": id, "total_events": len(events),
                    "retrieval": r, "logging": log_stats, "consolidation": c}
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(code="NOT_FOUND", message=f"Project {id} not found").model_dump(),
        )

    @router.get("/api/fleet")
    async def fleet_overview():
        project_summaries = []
        all_events = []
        for project_root in _load_project_paths():
            pp = make_project_paths(project_root)
            if pp is None:
                continue
            entries = read_learnings_for_dashboard(pp, ctx)
            events = read_metrics(pp)
            all_events.extend(events)
            r = _retrieval_stats([e for e in events if e.get("event_type") == "retrieval"])
            log_stats = _logging_stats([e for e in events if e.get("event_type") == "log"])
            c = _consolidation_stats([e for e in events if e.get("event_type") == "consolidate"])
            stats = stats_core(pp, emit_event=False, ctx=ctx)
            stats.pop("exit_code", None)
            stats.pop("status", None)
            last_consolidation = None
            cons_events = [e for e in events if e.get("event_type") == "consolidate"]
            if cons_events:
                last_consolidation = cons_events[-1].get("ts")
            domains = sorted({e.get("domain") for e in entries if e.get("domain")})
            project_summaries.append({
                "project": _get_project_id(pp),
                "path": str(project_root),
                "health": health_score(stats, {"retrieval": r, "logging": log_stats, "consolidation": c}),
                "total_entries": len(entries),
                "unresolved": stats.get("unresolved", 0),
                "retrievals": r.get("total_retrievals", 0),
                "hit_rate": r.get("hit_rate", 0),
                "logs": log_stats.get("total_logs", 0),
                "dup_rate": log_stats.get("duplicate_rate", 0),
                "quar_rate": log_stats.get("quarantine_rate", 0),
                "last_consolidation": last_consolidation,
                "domains": domains,
                "trends": _trend_stats(events, days=30),
            })

        domain_set = sorted({d for p in project_summaries for d in p["domains"]})
        heatmap = {
            "projects": [p["project"] for p in project_summaries],
            "domains": domain_set,
            "matrix": [[1 if d in p["domains"] else 0 for d in domain_set] for p in project_summaries],
        }

        return {
            "count": len(project_summaries),
            "projects": project_summaries,
            "domain_heatmap": heatmap,
            "fleet_trends": _trend_stats(all_events, days=30),
        }

    return router
