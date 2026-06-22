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
from datetime import datetime, timezone, timedelta
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
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            name = config.get("project_name")
            if name and isinstance(name, str):
                return name
        except (json.JSONDecodeError, IOError):
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
    with open(path, "r", encoding="utf-8") as f:
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
    with open(projects_file, "r", encoding="utf-8") as f:
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
        return {}

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

    return {
        "total_consolidations": total,
        "sprints": sprints,
        "total_promotion_candidates": sum(promos),
        "avg_promotion_candidates": sum(promos) / total,
        "total_contradictions": sum(contras),
        "total_stale": sum(stales),
        "total_quarantine_reviewed": sum(quars),
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
