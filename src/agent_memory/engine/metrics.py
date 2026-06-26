"""Metrics logging and analysis for the memory engine.

Captures structured events for every engine invocation, enabling
cross-project analysis of retrieval effectiveness, logging patterns,
consolidation history, and trend analysis.

Events are appended to <memory_dir>/metrics.jsonl (one JSON object per line).
Cross-project aggregation reads from all projects listed in
~/.agent-memory/engine/projects.txt.

Event schema (common fields):
  ts          — ISO-8601 UTC timestamp
  event_type  — "retrieval" | "log" | "update" | "resolve" | "stats" |
                "consolidate" | "review_agents"
  project_id  — from config.json project_name or repo dirname
  latency_ms  — handler execution time

Type-specific fields are documented in the instrumentation points
(retrieval.py, handlers.py, consolidation.py, agents_review.py).
"""

from __future__ import annotations

import json
import os
import sys
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENGINE_DIR = Path.home() / ".agent-memory" / "engine"

# Lightweight path container for cross-project metrics reading.
# Avoids importing Paths from filter.py (would create circular import).
_ProjectPaths = namedtuple("_ProjectPaths", [
    "memory_dir", "repo_root", "config_path", "learnings_path",
    "quarantine_path", "archive_dir", "session_file", "agents_md_path",
])


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------

def _metrics_path(paths):
    """Compute metrics.jsonl path from paths object."""
    return os.path.join(paths.memory_dir, "metrics.jsonl")


def _get_project_id(paths):
    """Get project identifier from config.json or directory name."""
    config_path = Path(paths.config_path)
    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                config = json.load(f)
            name = config.get("project_name")
            if name and isinstance(name, str):
                return name
        except (OSError, json.JSONDecodeError):
            pass
    return os.path.basename(paths.repo_root) or "unknown"


def log_event(paths, event_type, **fields):
    """Append a metrics event to metrics.jsonl.

    Best-effort: never raises. Failures are silently ignored to avoid
    disrupting engine operation.
    """
    try:
        event = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event_type": event_type,
            "project_id": _get_project_id(paths),
            **fields,
        }
        with open(_metrics_path(paths), "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------

def read_metrics(paths, event_type=None, since=None):
    """Read metrics events with optional filtering.

    Args:
        paths: Paths object for the project
        event_type: Filter to specific event type (None = all)
        since: datetime, only events at or after this moment

    Returns list of event dicts.
    """
    path = _metrics_path(paths)
    if not os.path.exists(path):
        return []

    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event_type and event.get("event_type") != event_type:
                continue
            if since:
                try:
                    ts = datetime.fromisoformat(event["ts"].replace("Z", "+00:00"))
                    if ts < since:
                        continue
                except (KeyError, ValueError):
                    pass

            events.append(event)

    return events


# ---------------------------------------------------------------------------
# Cross-project reading
# ---------------------------------------------------------------------------

def _load_project_paths():
    """Load all project paths from projects.txt."""
    projects_file = ENGINE_DIR / "projects.txt"
    if not projects_file.exists():
        return []

    projects = []
    with open(projects_file, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            p = Path(s)
            if p.exists():
                projects.append(p)
    return projects


def _read_all_project_metrics(since=None):
    """Read metrics from all registered projects.

    Returns list of (project_id, events) tuples.
    """
    results = []
    for project_path in _load_project_paths():
        memory_dir = project_path / "memory"
        if not memory_dir.exists():
            continue

        paths = _ProjectPaths(
            memory_dir=str(memory_dir),
            repo_root=str(project_path),
            config_path=str(memory_dir / "config.json"),
            learnings_path=str(memory_dir / "learnings.jsonl"),
            quarantine_path=str(memory_dir / "quarantine.jsonl"),
            archive_dir=str(memory_dir / "archive"),
            session_file=str(memory_dir / ".consolidate_session.json"),
            agents_md_path=str(project_path / "AGENTS.md"),
        )
        events = read_metrics(paths, since=since)
        if events:
            results.append((_get_project_id(paths), events))
    return results


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def _sf(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _si(val, default=0):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _retrieval_stats(events):
    """Compute retrieval effectiveness metrics."""
    total = len(events)
    if not total:
        return {}

    hits = sum(1 for e in events
               if _si(e.get("warnings_returned", 0)) + _si(e.get("patterns_returned", 0)) > 0)
    total_results = sum(_si(e.get("warnings_returned", 0)) + _si(e.get("patterns_returned", 0))
                        for e in events)
    scores = [_sf(e.get("top_score")) for e in events if e.get("top_score") is not None]

    qcomps = {}
    qdoms = {}
    for e in events:
        for c in e.get("query_components", []):
            qcomps[c] = qcomps.get(c, 0) + 1
        d = e.get("query_domain")
        if d:
            qdoms[d] = qdoms.get(d, 0) + 1

    return {
        "total_retrievals": total,
        "hit_count": hits,
        "hit_rate": hits / total,
        "empty_count": total - hits,
        "empty_rate": (total - hits) / total,
        "avg_results": total_results / total,
        "avg_top_score": sum(scores) / len(scores) if scores else 0,
        "max_top_score": max(scores) if scores else 0,
        "min_top_score": min(scores) if scores else 0,
        "top_query_components": sorted(qcomps.items(), key=lambda x: -x[1])[:10],
        "top_query_domains": sorted(qdoms.items(), key=lambda x: -x[1])[:10],
    }


def _logging_stats(events):
    """Compute logging pattern metrics."""
    total = len(events)
    if not total:
        return {}

    outcomes = {}
    for e in events:
        o = e.get("outcome", "unknown")
        outcomes[o] = outcomes.get(o, 0) + 1

    qreasons = {}
    for e in events:
        if e.get("outcome") == "QUARANTINED":
            r = e.get("outcome_detail", "unknown")
            if "JSON parse" in r:
                cat = "JSON parse"
            elif "Missing" in r or "must be" in r:
                cat = "Schema validation"
            elif "retrieval-only" in r:
                cat = "Agent permission"
            else:
                cat = "Other"
            qreasons[cat] = qreasons.get(cat, 0) + 1

    agents = {}
    domains = {}
    types = {}
    for e in events:
        a = e.get("entry_source_agent", "unknown")
        agents[a] = agents.get(a, 0) + 1
        d = e.get("entry_domain", "unknown")
        domains[d] = domains.get(d, 0) + 1
        t = e.get("entry_type", "unknown")
        types[t] = types.get(t, 0) + 1

    dup = outcomes.get("DUPLICATE", 0)
    conf = outcomes.get("CONFLICT", 0)
    quar = outcomes.get("QUARANTINED", 0)

    return {
        "total_logs": total,
        "outcomes": outcomes,
        "added": outcomes.get("ADDED", 0),
        "duplicate": dup,
        "conflict": conf,
        "quarantined": quar,
        "duplicate_rate": dup / total,
        "conflict_rate": conf / total,
        "quarantine_rate": quar / total,
        "quarantine_reasons": qreasons,
        "agent_contributions": sorted(agents.items(), key=lambda x: -x[1]),
        "domain_distribution": sorted(domains.items(), key=lambda x: -x[1]),
        "type_distribution": sorted(types.items(), key=lambda x: -x[1]),
    }


def _consolidation_stats(events):
    """Compute consolidation history metrics."""
    total = len(events)
    if not total:
        return {
            "total_consolidations": 0,
            "sprints": [],
            "total_promotion_candidates": 0,
            "avg_promotion_candidates": 0,
            "total_contradictions": 0,
            "total_stale": 0,
            "total_quarantine_reviewed": 0,
            "daily": {"days": [], "promotion_candidates": [], "contradictions": [], "stale_entries": []},
        }

    sprints = []
    promos = []
    contras = []
    stales = []
    quars = []
    for e in events:
        sprints.append(e.get("sprint_number", "?"))
        promos.append(_si(e.get("promotion_candidates")))
        contras.append(_si(e.get("contradictions")))
        stales.append(_si(e.get("stale_entries")))
        quars.append(_si(e.get("quarantine_count")))

    # 30-day daily trend
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    daily = {}
    for e in events:
        try:
            ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if ts < cutoff:
            continue
        day = ts.strftime("%Y-%m-%d")
        if day not in daily:
            daily[day] = {"promotion_candidates": 0, "contradictions": 0, "stale_entries": 0}
        daily[day]["promotion_candidates"] += _si(e.get("promotion_candidates"))
        daily[day]["contradictions"] += _si(e.get("contradictions"))
        daily[day]["stale_entries"] += _si(e.get("stale_entries"))

    days_sorted = sorted(daily.keys())
    daily_out = {
        "days": days_sorted,
        "promotion_candidates": [daily[d]["promotion_candidates"] for d in days_sorted],
        "contradictions": [daily[d]["contradictions"] for d in days_sorted],
        "stale_entries": [daily[d]["stale_entries"] for d in days_sorted],
    }

    return {
        "total_consolidations": total,
        "sprints": sprints,
        "total_promotion_candidates": sum(promos),
        "avg_promotion_candidates": sum(promos) / total,
        "total_contradictions": sum(contras),
        "total_stale": sum(stales),
        "total_quarantine_reviewed": sum(quars),
        "daily": daily_out,
    }


def _trend_stats(events, days=30):
    """Compute time-series trends, bucketed by day."""
    if not events:
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    buckets = {}
    for e in events:
        try:
            ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if ts < cutoff:
            continue
        day = ts.strftime("%Y-%m-%d")
        if day not in buckets:
            buckets[day] = {"retrievals": 0, "logs": 0, "consolidations": 0, "other": 0}
        et = e.get("event_type", "other")
        if et == "retrieval":
            buckets[day]["retrievals"] += 1
        elif et == "log":
            buckets[day]["logs"] += 1
        elif et == "consolidate":
            buckets[day]["consolidations"] += 1
        else:
            buckets[day]["other"] += 1

    sd = sorted(buckets.keys())
    return {
        "days": sd,
        "buckets": [buckets[d] for d in sd],
        "total_retrievals": sum(b["retrievals"] for b in buckets.values()),
        "total_logs": sum(b["logs"] for b in buckets.values()),
        "total_consolidations": sum(b["consolidations"] for b in buckets.values()),
    }


def _lifecycle_stats(entries):
    """Compute entry lifecycle metrics from learnings (not metrics events)."""
    if not entries:
        return {}
    total = len(entries)
    resolved = sum(1 for e in entries if e.get("resolved", False))
    unresolved = total - resolved

    now = datetime.now(timezone.utc)
    ages = []
    age_days = []
    for e in entries:
        try:
            ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
            age_days.append((now - ts).days)
        except (KeyError, ValueError):
            age_days.append(0)
    ages = [a for a in age_days if a is not None]

    avg_age = sum(ages) / len(ages) if ages else 0
    max_age = max(ages) if ages else 0

    access_counts = [e.get("access_count", 0) for e in entries]
    reinforcement_counts = [e.get("reinforcement_count", 0) for e in entries]

    # Age buckets (days): 0, 1-7, 8-14, 15-30, 31-60, 61-90, 180+ (captures >90)
    age_buckets = [0, 0, 0, 0, 0, 0, 0]
    age_labels = ["0", "1-7", "8-14", "15-30", "31-60", "61-90", "180+"]
    for a in ages:
        if a == 0:
            age_buckets[0] += 1
        elif a <= 7:
            age_buckets[1] += 1
        elif a <= 14:
            age_buckets[2] += 1
        elif a <= 30:
            age_buckets[3] += 1
        elif a <= 60:
            age_buckets[4] += 1
        elif a <= 90:
            age_buckets[5] += 1
        else:
            age_buckets[6] += 1

    # Access buckets: 0, 1, 2-5, 6-10, 11-25, 26-50, 50+
    access_buckets = [0, 0, 0, 0, 0, 0, 0]
    access_labels = ["0", "1", "2-5", "6-10", "11-25", "26-50", "50+"]
    for a in access_counts:
        if a == 0:
            access_buckets[0] += 1
        elif a == 1:
            access_buckets[1] += 1
        elif a <= 5:
            access_buckets[2] += 1
        elif a <= 10:
            access_buckets[3] += 1
        elif a <= 25:
            access_buckets[4] += 1
        elif a <= 50:
            access_buckets[5] += 1
        else:
            access_buckets[6] += 1

    # Zombie: unresolved, zero access, age >= 7 days
    zombies = []
    for i, e in enumerate(entries):
        if e.get("resolved", False):
            continue
        if access_counts[i] != 0:
            continue
        if age_days[i] < 7:
            continue
        zombies.append({
            "ts": e.get("ts"),
            "source_agent": e.get("source_agent", "unknown"),
            "domain": e.get("domain", "unknown"),
            "severity": e.get("severity", "unknown"),
            "trigger": e.get("trigger", "")[:80],
            "age_days": age_days[i],
        })
    zombies.sort(key=lambda x: -x["age_days"])
    zombies = zombies[:50]

    return {
        "total": total,
        "resolved": resolved,
        "unresolved": unresolved,
        "resolution_rate": resolved / total,
        "avg_age_days": round(avg_age, 1),
        "max_age_days": max_age,
        "avg_access_count": round(sum(access_counts) / total, 2),
        "avg_reinforcement_count": round(sum(reinforcement_counts) / total, 2),
        "zero_access": sum(1 for a in access_counts if a == 0),
        "high_access": sum(1 for a in access_counts if a >= 10),
        "age_distribution": {"labels": age_labels, "values": age_buckets},
        "access_distribution": {"labels": access_labels, "values": access_buckets},
        "zombie_entries": zombies,
        "zombie_count": len(zombies),
    }


def _agent_stats(entries, log_events=None):
    """Compute per-agent contribution breakdown."""
    agents = {}
    for e in entries:
        a = e.get("source_agent", "unknown")
        if a not in agents:
            agents[a] = {
                "entries": 0,
                "resolved": 0,
                "avg_importance": 0,
                "domains": set(),
                "severity_counts": {"critical": 0, "major": 0, "minor": 0, "unknown": 0},
            }
        agents[a]["entries"] += 1
        if e.get("resolved", False):
            agents[a]["resolved"] += 1
        agents[a]["avg_importance"] += e.get("importance", 5)
        sev = e.get("severity", "unknown")
        if sev not in agents[a]["severity_counts"]:
            sev = "unknown"
        agents[a]["severity_counts"][sev] += 1
        d = e.get("domain")
        if d:
            agents[a]["domains"].add(d)

    # Build 30-day trend buckets per agent from log_events
    trend_days = []
    trend = {}
    if log_events:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        for ev in log_events:
            a = ev.get("entry_source_agent", "unknown")
            if a not in trend:
                trend[a] = {}
            try:
                ts = datetime.fromisoformat(ev["ts"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if ts < cutoff:
                continue
            day = ts.strftime("%Y-%m-%d")
            trend[a][day] = trend[a].get(day, 0) + 1

    # Normalize to a contiguous 30-day label list
    if trend:
        start = datetime.now(timezone.utc) - timedelta(days=29)
        trend_days = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(30)]
        for a in trend:
            trend[a] = [trend[a].get(d, 0) for d in trend_days]

    result = []
    for a, s in sorted(agents.items(), key=lambda x: -x[1]["entries"]):
        result.append({
            "agent": a,
            "entries": s["entries"],
            "resolved": s["resolved"],
            "resolution_rate": s["resolved"] / s["entries"] if s["entries"] else 0,
            "avg_importance": round(s["avg_importance"] / s["entries"], 1),
            "domains": sorted(s["domains"]),
            "severity_counts": s["severity_counts"],
            "trend": trend.get(a, [0] * len(trend_days)) if trend_days else [],
        })

    if log_events:
        log_by_agent = {}
        for ev in log_events:
            a = ev.get("entry_source_agent", "unknown")
            if a not in log_by_agent:
                log_by_agent[a] = {"added": 0, "duplicate": 0, "conflict": 0, "quarantined": 0}
            outcome = ev.get("outcome", "").lower()
            if outcome == "added":
                log_by_agent[a]["added"] += 1
            elif outcome == "duplicate":
                log_by_agent[a]["duplicate"] += 1
            elif outcome == "conflict":
                log_by_agent[a]["conflict"] += 1
            elif outcome == "quarantined":
                log_by_agent[a]["quarantined"] += 1
        for r in result:
            r["log_outcomes"] = log_by_agent.get(r["agent"], {})

    return result


def _dedup_stats(log_events):
    """Compute deduplication effectiveness from log events."""
    total = len(log_events)
    if not total:
        return {
            "total_logs": 0,
            "added": 0,
            "duplicates": 0,
            "semantic_duplicates": 0,
            "conflicts": 0,
            "conflicts_list": [],
            "dedup_rate": 0,
            "conflict_rate": 0,
            "avg_similarity": 0,
            "max_similarity": 0,
            "daily": {"days": [], "duplicates": [], "conflicts": [], "added": []},
        }
    dup = sum(1 for e in log_events if e.get("outcome", "").upper() == "DUPLICATE")
    sem_dup = sum(1 for e in log_events if e.get("outcome", "").upper() == "SEMANTIC_DUPLICATE")
    conflict = sum(1 for e in log_events if e.get("outcome", "").upper() == "CONFLICT")
    added = sum(1 for e in log_events if e.get("outcome", "").upper() == "ADDED")

    similarities = [_sf(e.get("similarity_score")) for e in log_events if e.get("similarity_score") is not None]

    # 30-day duplicate/conflict trend
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    daily = {}
    for e in log_events:
        try:
            ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if ts < cutoff:
            continue
        day = ts.strftime("%Y-%m-%d")
        if day not in daily:
            daily[day] = {"duplicates": 0, "conflicts": 0, "added": 0}
        o = e.get("outcome", "").upper()
        if o in ("DUPLICATE", "SEMANTIC_DUPLICATE"):
            daily[day]["duplicates"] += 1
        elif o == "CONFLICT":
            daily[day]["conflicts"] += 1
        elif o == "ADDED":
            daily[day]["added"] += 1

    days_sorted = sorted(daily.keys())
    daily_out = {
        "days": days_sorted,
        "duplicates": [daily[d]["duplicates"] for d in days_sorted],
        "conflicts": [daily[d]["conflicts"] for d in days_sorted],
        "added": [daily[d]["added"] for d in days_sorted],
    }

    # Recent conflicts list
    conflicts_list = []
    for e in log_events:
        if e.get("outcome", "").upper() == "CONFLICT":
            conflicts_list.append({
                "ts": e.get("ts"),
                "source_agent": e.get("entry_source_agent", "unknown"),
                "matched_source_agent": e.get("matched_source_agent", "unknown"),
                "trigger": e.get("entry_trigger", "")[:80],
                "similarity_score": _sf(e.get("similarity_score")),
            })
    conflicts_list.sort(key=lambda x: x.get("ts", "") or "", reverse=True)
    conflicts_list = conflicts_list[:20]

    return {
        "total_logs": total,
        "added": added,
        "duplicates": dup,
        "semantic_duplicates": sem_dup,
        "conflicts": conflict,
        "conflicts_list": conflicts_list,
        "dedup_rate": (dup + sem_dup) / total,
        "conflict_rate": conflict / total,
        "avg_similarity": round(sum(similarities) / len(similarities), 4) if similarities else 0,
        "max_similarity": round(max(similarities), 4) if similarities else 0,
        "daily": daily_out,
    }


# ---------------------------------------------------------------------------
# CLI handler
# ---------------------------------------------------------------------------

def handle_metrics(args, paths):
    """Handle --metrics mode: report on collected metrics."""
    since = None
    if getattr(args, "metrics_since", None):
        try:
            since = datetime.fromisoformat(args.metrics_since + "T00:00:00+00:00")
        except ValueError:
            print(f"ERROR: Invalid --metrics-since date: {args.metrics_since}", file=sys.stderr)
            return 1

    if getattr(args, "metrics_export", None):
        events = read_metrics(paths, since=since)
        with open(args.metrics_export, "w", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        print(f"Exported {len(events)} events to {args.metrics_export}")
        return 0

    if getattr(args, "metrics_all_projects", False):
        return _report_cross_project(since, getattr(args, "metrics_json", False))

    events = read_metrics(paths, since=since)
    if not events:
        print("## METRICS")
        print("No metrics events found. Metrics are logged automatically on each engine invocation.")
        return 0

    json_out = getattr(args, "metrics_json", False)

    if getattr(args, "metrics_retrieval", False):
        return _report_retrieval(events, json_out)
    if getattr(args, "metrics_logging", False):
        return _report_logging(events, json_out)
    if getattr(args, "metrics_consolidation", False):
        return _report_consolidation(events, json_out)
    if getattr(args, "metrics_trend", False):
        return _report_trend(events, json_out)
    return _report_summary(events, json_out)


def _report_summary(events, json_out=False):
    """Print summary dashboard."""
    r = _retrieval_stats([e for e in events if e.get("event_type") == "retrieval"])
    l = _logging_stats([e for e in events if e.get("event_type") == "log"])
    c = _consolidation_stats([e for e in events if e.get("event_type") == "consolidate"])

    if json_out:
        print(json.dumps({"summary": {"total_events": len(events),
              "retrieval": r, "logging": l, "consolidation": c}},
              indent=2, default=str))
        return 0

    print("## METRICS SUMMARY")
    print(f"Total events: {len(events)}\n")

    print("### Retrieval")
    if r:
        print(f"  Total: {r['total_retrievals']}, Hit rate: {r['hit_rate']:.1%}, "
              f"Avg results: {r['avg_results']:.1f}, Avg top score: {r['avg_top_score']:.3f}")
    else:
        print("  No retrieval events.")
    print()

    print("### Logging")
    if l:
        print(f"  Total: {l['total_logs']}, Added: {l['added']}, Dup: {l['duplicate']}, "
              f"Conflict: {l['conflict']}, Quar: {l['quarantined']}")
        print(f"  Dup rate: {l['duplicate_rate']:.1%}, Quar rate: {l['quarantine_rate']:.1%}")
    else:
        print("  No logging events.")
    print()

    print("### Consolidation")
    if c:
        print(f"  Total: {c['total_consolidations']}, "
              f"Promotion candidates: {c['total_promotion_candidates']}, "
              f"Contradictions: {c['total_contradictions']}, Stale: {c['total_stale']}")
    else:
        print("  No consolidation events.")
    return 0


def _report_retrieval(events, json_out=False):
    """Print retrieval effectiveness deep-dive."""
    s = _retrieval_stats(events)
    if json_out:
        print(json.dumps({"retrieval": s}, indent=2, default=str))
        return 0
    if not s:
        print("## RETRIEVAL METRICS\nNo retrieval events found.")
        return 0

    print("## RETRIEVAL EFFECTIVENESS")
    print(f"Total: {s['total_retrievals']}, Hit rate: {s['hit_rate']:.1%} "
          f"({s['hit_count']} hits, {s['empty_count']} empty)")
    print(f"Avg results: {s['avg_results']:.1f}\n")

    print("### Score Distribution")
    print(f"  Avg: {s['avg_top_score']:.3f}, Max: {s['max_top_score']:.3f}, "
          f"Min: {s['min_top_score']:.3f}\n")

    print("### Top Query Components")
    for c, n in s['top_query_components']:
        print(f"  {c}: {n}")
    print()
    print("### Top Query Domains")
    for d, n in s['top_query_domains']:
        print(f"  {d}: {n}")
    return 0


def _report_logging(events, json_out=False):
    """Print logging patterns deep-dive."""
    s = _logging_stats(events)
    if json_out:
        print(json.dumps({"logging": s}, indent=2, default=str))
        return 0
    if not s:
        print("## LOGGING METRICS\nNo logging events found.")
        return 0

    print("## LOGGING PATTERNS")
    print(f"Total: {s['total_logs']}\n")

    print("### Outcomes")
    for o, n in sorted(s['outcomes'].items(), key=lambda x: -x[1]):
        print(f"  {o}: {n} ({n / s['total_logs']:.1%})")

    print(f"\nDup rate: {s['duplicate_rate']:.1%}, "
          f"Conflict rate: {s['conflict_rate']:.1%}, "
          f"Quar rate: {s['quarantine_rate']:.1%}")

    if s['quarantine_reasons']:
        print("\n### Quarantine Reasons")
        for r, n in sorted(s['quarantine_reasons'].items(), key=lambda x: -x[1]):
            print(f"  {r}: {n}")

    print("\n### Agent Contributions")
    for a, n in s['agent_contributions'][:10]:
        print(f"  {a}: {n}")

    print("\n### Domain Distribution")
    for d, n in s['domain_distribution'][:10]:
        print(f"  {d}: {n}")

    print("\n### Type Distribution")
    for t, n in s['type_distribution']:
        print(f"  {t}: {n}")
    return 0


def _report_consolidation(events, json_out=False):
    """Print consolidation history."""
    s = _consolidation_stats(events)
    if json_out:
        print(json.dumps({"consolidation": s}, indent=2, default=str))
        return 0
    if not s:
        print("## CONSOLIDATION METRICS\nNo consolidation events found.")
        return 0

    print("## CONSOLIDATION HISTORY")
    print(f"Total: {s['total_consolidations']}, Sprints: {', '.join(str(x) for x in s['sprints'])}")
    print(f"Promotion candidates: {s['total_promotion_candidates']} "
          f"(avg {s['avg_promotion_candidates']:.1f}/sprint)")
    print(f"Contradictions: {s['total_contradictions']}, "
          f"Stale: {s['total_stale']}, "
          f"Quarantine reviewed: {s['total_quarantine_reviewed']}")
    return 0


def _report_trend(events, json_out=False, days=30):
    """Print time-series trends."""
    s = _trend_stats(events, days=days)
    if json_out:
        print(json.dumps({"trend": s}, indent=2, default=str))
        return 0
    if not s:
        print(f"## TREND (last {days} days)\nNo events found.")
        return 0

    print(f"## TREND (last {days} days)")
    print(f"Retrievals: {s['total_retrievals']}, "
          f"Logs: {s['total_logs']}, "
          f"Consolidations: {s['total_consolidations']}\n")

    print("### Daily Activity")
    for day, b in zip(s['days'], s['buckets']):
        print(f"  {day}: {b['retrievals']}R {b['logs']}L {b['consolidations']}C {b['other']}O")
    return 0


def _report_cross_project(since, json_out=False):
    """Print cross-project metrics comparison."""
    all_m = _read_all_project_metrics(since=since)
    if not all_m:
        print("## CROSS-PROJECT METRICS")
        print("No metrics found.")
        print("Ensure projects are registered in ~/.agent-memory/engine/projects.txt")
        return 0

    summaries = []
    for pid, events in all_m:
        r = _retrieval_stats([e for e in events if e.get("event_type") == "retrieval"])
        l = _logging_stats([e for e in events if e.get("event_type") == "log"])
        summaries.append({
            "project": pid,
            "total_events": len(events),
            "retrievals": r.get("total_retrievals", 0),
            "hit_rate": r.get("hit_rate", 0),
            "logs": l.get("total_logs", 0),
            "dup_rate": l.get("duplicate_rate", 0),
            "quar_rate": l.get("quarantine_rate", 0),
        })

    if json_out:
        print(json.dumps({"cross_project": summaries}, indent=2, default=str))
        return 0

    print("## CROSS-PROJECT METRICS\n")
    print(f"{'Project':<25} {'Events':>7} {'Retrievals':>11} {'Hit Rate':>9} "
          f"{'Logs':>6} {'Dup%':>6} {'Quar%':>6}")
    print("-" * 80)
    for s in sorted(summaries, key=lambda x: -x["total_events"]):
        print(f"{s['project']:<25} {s['total_events']:>7} {s['retrievals']:>11} "
              f"{s['hit_rate']:>8.1%} {s['logs']:>6} {s['dup_rate']:>5.1%} {s['quar_rate']:>5.1%}")
    return 0
