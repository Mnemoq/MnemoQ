"""Thin analysis functions for the dashboard.

Takes paths (and ctx where needed), returns plain dicts. Per AGENTS.md,
no Pydantic models. Cache is module-level here since only dashboard
endpoints use it.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from agent_memory.engine.metrics import (
    read_metrics,
    _retrieval_stats,
    _logging_stats,
    _consolidation_stats,
    _ProjectPaths,
)
from agent_memory.engine.models import ErrorResponse


# -- Metrics cache (only used by dashboard endpoints) --

_metrics_cache: dict[str, dict] = {}
_METRICS_CACHE_TTL = 5.0


def _cache_key(paths):
    return paths.config_path


def get_metrics_data(paths, since_dt=None):
    """Return (events, retrieval_stats, logging_stats, consolidation_stats).

    Caches the no-since result per project for _METRICS_CACHE_TTL seconds.
    """
    key = _cache_key(paths)
    if since_dt is None:
        now = time.monotonic()
        cached = _metrics_cache.get(key)
        if cached and now - cached["ts"] < _METRICS_CACHE_TTL:
            return cached["data"]
    events = read_metrics(paths, since=since_dt)
    r = _retrieval_stats([e for e in events if e.get("event_type") == "retrieval"])
    l = _logging_stats([e for e in events if e.get("event_type") == "log"])
    c = _consolidation_stats([e for e in events if e.get("event_type") == "consolidate"])
    result = (events, r, l, c)
    if since_dt is None:
        _metrics_cache[key] = {"ts": time.monotonic(), "data": result}
    return result


def invalidate_metrics_cache():
    """Clear the metrics cache for all projects. Called after writes that change stats."""
    _metrics_cache.clear()


def parse_since(since: str | None):
    """Parse a YYYY-MM-DD string into a datetime, or None."""
    if not since:
        return None
    try:
        return datetime.fromisoformat(since + "T00:00:00+00:00")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code="BAD_REQUEST",
                message=f"Invalid since date: {since}",
                suggested_action="Use YYYY-MM-DD format.",
            ).model_dump(),
        )


def make_project_paths(project_root: Path):
    """Build a _ProjectPaths from a project root, or None if no memory dir."""
    memory_dir = project_root / "memory"
    if not memory_dir.exists():
        return None
    return _ProjectPaths(
        memory_dir=str(memory_dir),
        repo_root=str(project_root),
        config_path=str(memory_dir / "config.json"),
        learnings_path=str(memory_dir / "learnings.jsonl"),
        quarantine_path=str(memory_dir / "quarantine.jsonl"),
        archive_dir=str(memory_dir / "archive"),
        session_file=str(memory_dir / ".consolidate_session.json"),
        agents_md_path=str(project_root / "AGENTS.md"),
    )


# -- Analysis functions (moved from metrics.py) --

def health_score(stats, metrics_data):
    """Compute a 0-100 health score from stats and metrics."""
    score = 100
    total = stats.get("total", 0)
    if total == 0:
        return 50

    unresolved = stats.get("unresolved", 0)
    if unresolved > 50:
        score -= 20
    elif unresolved > 30:
        score -= 10

    over_injected = stats.get("over_injected", 0)
    score -= min(over_injected * 2, 15)

    under_retrieved = stats.get("under_retrieved", 0)
    score -= min(under_retrieved * 2, 15)

    unverified = stats.get("unverified", 0)
    if total > 0:
        unverified_ratio = unverified / total
        score -= min(int(unverified_ratio * 20), 15)

    r = metrics_data.get("retrieval", {})
    hit_rate = r.get("hit_rate", 1) if r else 1
    if hit_rate < 0.5:
        score -= 10

    l = metrics_data.get("logging", {})
    dup_rate = l.get("duplicate_rate", 0) if l else 0
    if dup_rate > 0.5:
        score -= 10

    return max(0, min(100, score))


def recommendations(stats, metrics_data, config=None):
    """Generate actionable recommendations from stats and metrics."""
    recs = []

    if stats.get("sleep_cycle_due"):
        reasons = stats.get("sleep_cycle_reasons", [])
        messages = []
        if "threshold" in reasons:
            messages.append(f"{stats['unresolved']} unresolved entries exceed threshold of 50")
        if "time" in reasons:
            days = (config or {}).get("tuning", {}).get("sleep_cycle_days", 7)
            messages.append(f"more than {days} days since last consolidation")
        if "quarantine" in reasons:
            messages.append("quarantine entries exceed threshold")
        msg = "Sleep cycle due — " + "; ".join(messages) + "."
        recs.append({
            "priority": "high",
            "category": "consolidation",
            "message": msg,
            "action": "Run consolidation to archive resolved learnings and promote candidates.",
        })

    if stats.get("over_injected", 0) > 0:
        recs.append({
            "priority": "medium",
            "category": "quality",
            "message": f"{stats['over_injected']} over-injected entries (access >= 10, reinforcement <= 2).",
            "action": "Review these entries — they may be noise or need resolution.",
        })

    if stats.get("under_retrieved", 0) > 0:
        recs.append({
            "priority": "medium",
            "category": "quality",
            "message": f"{stats['under_retrieved']} under-retrieved entries (access <= 2, reinforcement >= 5).",
            "action": "These proven entries aren't being retrieved. Check retrieval thresholds or component tagging.",
        })

    r = metrics_data.get("retrieval", {})
    if r and r.get("hit_rate", 1) < 0.5:
        recs.append({
            "priority": "high",
            "category": "retrieval",
            "message": f"Retrieval hit rate is {r['hit_rate']:.0%} — below 50%.",
            "action": "Lower score_threshold in config.json or improve component/file tagging in entries.",
        })

    l = metrics_data.get("logging", {})
    if l and l.get("duplicate_rate", 0) > 0.4:
        recs.append({
            "priority": "low",
            "category": "logging",
            "message": f"Duplicate rate is {l['duplicate_rate']:.0%} — high redundancy.",
            "action": "Agents may be re-logging known patterns. Consider retrieval before logging.",
        })

    unverified = stats.get("unverified", 0)
    total = stats.get("total", 1)
    if total > 0 and unverified / total > 0.8:
        recs.append({
            "priority": "low",
            "category": "quality",
            "message": f"{unverified}/{total} entries unverified.",
            "action": "Run --review-agents to verify entries against AGENTS.md.",
        })

    tuning = (config or {}).get("tuning", {})
    if tuning.get("score_threshold", 0.15) > 0.3:
        recs.append({
            "priority": "low",
            "category": "config",
            "message": f"score_threshold is {tuning['score_threshold']} — may be too high.",
            "action": "Consider lowering to 0.15-0.20 for better retrieval recall.",
        })

    return recs


def alerts_list(stats, metrics_data):
    """Generate active alerts from stats and metrics."""
    alerts = []

    if stats.get("sleep_cycle_due"):
        reasons = stats.get("sleep_cycle_reasons", [])
        messages = []
        if "threshold" in reasons:
            messages.append(f"{stats['unresolved']} unresolved entries exceed threshold of 50")
        if "time" in reasons:
            messages.append("more than 7 days since last consolidation")
        if "quarantine" in reasons:
            messages.append("quarantine entries exceed threshold")
        msg = "Sleep cycle due — " + "; ".join(messages) + "."
        alerts.append({
            "type": "danger",
            "message": msg,
        })

    if stats.get("over_injected", 0) > 0:
        alerts.append({
            "type": "warning",
            "message": f"{stats['over_injected']} over-injected entries (access >= 10, reinforcement <= 2).",
        })

    if stats.get("under_retrieved", 0) > 0:
        alerts.append({
            "type": "warning",
            "message": f"{stats['under_retrieved']} under-retrieved entries (access <= 2, reinforcement >= 5).",
        })

    r = metrics_data.get("retrieval", {})
    if r and r.get("hit_rate", 1) < 0.3:
        alerts.append({
            "type": "danger",
            "message": f"Retrieval hit rate critically low at {r['hit_rate']:.0%}.",
        })

    return alerts
