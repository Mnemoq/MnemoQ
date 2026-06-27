"""Core handlers for filter.py operations.

Extracted from filter.py (Phase 6).
"""

from __future__ import annotations

import json
import re
import sys
import time

from agent_memory.engine.agents_review import check_agents_conflict
from agent_memory.engine.git_utils import stamp_entry
from agent_memory.engine.io import append_learning, quarantine, read_learnings, write_learnings
from agent_memory.engine.metrics import log_event
from agent_memory.engine.migrate import CURRENT_SCHEMA_VERSION
from agent_memory.engine.retrieval import embed_entry, encode_embedding, find_semantic_duplicate
from agent_memory.engine.triggers import check_sleep_cycle
from agent_memory.engine.validation import actions_oppose, find_best_match, validate_entry

TS_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

def log_core(json_str, paths, ctx):
    """Shared logic for --log mode. Returns dict, no printing.

    Returns:
        {"exit_code": int, "status": str, "message": str, ...}
    """
    _start = time.perf_counter()

    try:
        entry = json.loads(json_str)
    except json.JSONDecodeError as e:
        log_event(paths, "log", outcome="QUARANTINED", outcome_detail=f"JSON parse error: {e}",
                  entry_ts=None,
                  latency_ms=round((time.perf_counter() - _start) * 1000, 2))
        quarantine(paths, json_str, f"JSON parse error: {e}")
        return {"exit_code": 1, "status": "quarantined", "message": f"QUARANTINED: JSON parse error: {e}"}

    errors = validate_entry(entry, ctx)
    if errors:
        reason = "; ".join(errors)
        log_event(paths, "log", outcome="QUARANTINED", outcome_detail=reason,
                  entry_ts=entry.get("ts"),
                  entry_type=entry.get("type"), entry_domain=entry.get("domain"),
                  entry_severity=entry.get("severity"), entry_step=entry.get("step"),
                  entry_source_agent=entry.get("source_agent"),
                  latency_ms=round((time.perf_counter() - _start) * 1000, 2))
        quarantine(paths, json_str, reason)
        return {"exit_code": 1, "status": "quarantined", "message": f"QUARANTINED: {reason}"}

    valid_retrieval_only = ctx.get("valid_retrieval_only_agents")
    if valid_retrieval_only is not None and entry.get("source_agent") in valid_retrieval_only:
        detail = f"{entry['source_agent']} is retrieval-only (use --step mode)"
        log_event(paths, "log", outcome="QUARANTINED", outcome_detail=detail,
                  entry_ts=entry.get("ts"),
                  entry_type=entry.get("type"), entry_domain=entry.get("domain"),
                  entry_severity=entry.get("severity"), entry_step=entry.get("step"),
                  entry_source_agent=entry.get("source_agent"),
                  latency_ms=round((time.perf_counter() - _start) * 1000, 2))
        quarantine(paths, json_str, detail)
        return {"exit_code": 1, "status": "quarantined", "message": f"QUARANTINED: {detail}"}

    entry = stamp_entry(entry, paths.repo_root)
    entry["schema_version"] = CURRENT_SCHEMA_VERSION

    # Populate provenance fields
    from agent_memory.engine.metrics import _get_project_id
    _project_id = _get_project_id(paths)
    entry.setdefault("project_id", _project_id)
    entry.setdefault("origin_project", _project_id)
    entry.setdefault("contributing_projects", [])
    entry.setdefault("contributors", [entry.get("source_agent", "unknown")])

    # Compute and store embedding at log time
    _emb_model = ctx.get("embedding_model")
    _emb_cache = ctx.get("embedding_cache_dir")
    entry["embedding"] = encode_embedding(embed_entry(entry, _emb_model, _emb_cache))

    # AGENTS.md conflict detection (informational, non-blocking)
    conflict_detected, best_section, jaccard_score, containment_hits = check_agents_conflict(entry, paths)
    if conflict_detected:
        print(
            f"WARNING: Learning may overlap with AGENTS.md section '{best_section}'\n"
            f"  Jaccard: {jaccard_score:.2f}, Containment hits: {containment_hits}\n"
            f"  Learning: {entry['trigger']}: {entry['action']}\n"
            f"  Consider: Updating existing section instead of adding new rule",
            file=sys.stderr,
        )

    existing_entries = read_learnings(paths)

    _lat = lambda: round((time.perf_counter() - _start) * 1000, 2)
    _entry_meta = lambda: {
        "entry_ts": entry.get("ts"),
        "entry_type": entry.get("type"),
        "entry_domain": entry.get("domain"),
        "entry_severity": entry.get("severity"),
        "entry_step": entry.get("step"),
        "entry_source_agent": entry.get("source_agent"),
        "agents_md_conflict": conflict_detected,
        "agents_md_section": best_section,
        "agents_md_jaccard": round(jaccard_score, 4),
    }

    # --- Semantic dedup: cosine check before Jaccard ---
    sem_cosine, sem_match = find_semantic_duplicate(entry, existing_entries, ctx)
    if sem_match is not None:
        # Merge: combine access_count, keep richer description, append contributor
        sem_match["access_count"] = sem_match.get("access_count", 0) + 1
        sem_match["reinforcement_count"] = sem_match.get("reinforcement_count", 0) + 1
        new_reason_len = len(entry.get("reason", ""))
        old_reason_len = len(sem_match.get("reason", ""))
        if new_reason_len > old_reason_len:
            sem_match["trigger"] = entry["trigger"]
            sem_match["action"] = entry["action"]
            sem_match["reason"] = entry["reason"]
        _contrib = sem_match.get("contributors", [])
        if entry.get("source_agent") not in _contrib:
            _contrib.append(entry.get("source_agent"))
        sem_match["contributors"] = _contrib
        # Backfill embedding on existing entry if new entry has one and old doesn't
        if entry.get("embedding") is not None and sem_match.get("embedding") is None:
            sem_match["embedding"] = entry["embedding"]
        write_learnings(paths, existing_entries)
        quarantine(paths, json_str, f"semantic_duplicate (cosine: {sem_cosine:.3f})")
        msg = (
            f"SEMANTIC DUPLICATE — merged with existing entry (cosine: {sem_cosine:.3f}):\n"
            f"  [step-{sem_match['step']}, {sem_match['domain']}, "
            f"{sem_match['source_agent']}] {sem_match['trigger']}: {sem_match['action']}\n"
            f"  access_count incremented to {sem_match['access_count']}.\n"
            f"  contributors: {', '.join(sem_match['contributors'])}"
        )
        log_event(paths, "log", outcome="SEMANTIC_DUPLICATE", similarity_score=round(sem_cosine, 4),
                  latency_ms=_lat(), **_entry_meta())
        return {"exit_code": 0, "status": "semantic_duplicate", "message": msg,
                "matched_entry": sem_match, "similarity": round(sem_cosine, 4)}

    similarity, best_match = find_best_match(entry, existing_entries)

    if similarity >= 0.7:
        best_match["access_count"] = best_match.get("access_count", 0) + 1
        best_match["reinforcement_count"] = best_match.get("reinforcement_count", 0) + 1
        # Backfill embedding on existing entry if new entry has one and old doesn't
        if entry.get("embedding") is not None and best_match.get("embedding") is None:
            best_match["embedding"] = entry["embedding"]
        write_learnings(paths, existing_entries)
        msg = (
            f"DUPLICATE — existing entry matches (similarity: {similarity:.2f}):\n"
            f"  [step-{best_match['step']}, {best_match['domain']}, "
            f"{best_match['source_agent']}] {best_match['trigger']}: {best_match['action']}\n"
            f"  access_count incremented to {best_match['access_count']}.\n"
            f"  reinforcement_count incremented to {best_match['reinforcement_count']}."
        )
        log_event(paths, "log", outcome="DUPLICATE", similarity_score=round(similarity, 4),
                  latency_ms=_lat(), **_entry_meta())
        return {"exit_code": 0, "status": "duplicate", "message": msg,
                "matched_entry": best_match, "similarity": round(similarity, 4)}

    if 0.4 <= similarity < 0.7:
        if actions_oppose(entry["action"], best_match["action"]):
            append_learning(paths, entry)
            msg = (
                f"CONFLICT — potential contradiction detected (similarity: {similarity:.2f}):\n"
                f"  Existing: [step-{best_match['step']}, {best_match['domain']}, "
                f"{best_match['source_agent']}] {best_match['trigger']}: {best_match['action']}\n"
                f"  Your entry proposes an opposing action for the same trigger.\n"
                f"  Follow the Challenge Protocol: re-submit with type 'architectural_pattern' and\n"
                f"  explain in the reason why the old rule no longer applies."
            )
            log_event(paths, "log", outcome="CONFLICT", similarity_score=round(similarity, 4),
                      matched_source_agent=best_match.get("source_agent", "unknown"),
                      latency_ms=_lat(), **_entry_meta())
            return {"exit_code": 0, "status": "conflict", "message": msg,
                    "entry": entry, "matched_entry": best_match,
                    "similarity": round(similarity, 4)}
        else:
            append_learning(paths, entry)
            msg = (f"ADDED [step-{entry['step']}, {entry['type']}, {entry['domain']}] "
                   f"{entry['trigger']}: {entry['action']}")
            log_event(paths, "log", outcome="ADDED", similarity_score=round(similarity, 4),
                      latency_ms=_lat(), **_entry_meta())
            return {"exit_code": 0, "status": "added", "message": msg,
                    "entry": entry, "similarity": round(similarity, 4)}

    append_learning(paths, entry)
    msg = f"ADDED [step-{entry['step']}, {entry['type']}, {entry['domain']}] {entry['trigger']}: {entry['action']}"
    log_event(paths, "log", outcome="ADDED", similarity_score=round(similarity, 4),
              latency_ms=_lat(), **_entry_meta())
    return {"exit_code": 0, "status": "added", "message": msg, "entry": entry, "similarity": round(similarity, 4)}


def handle_log(json_str, paths, ctx):
    """Handle --log mode: validate, dedup-check, append. CLI wrapper."""
    result = log_core(json_str, paths, ctx)
    if result["exit_code"] != 0:
        print(result["message"], file=sys.stderr)
    else:
        print(result["message"])
    return result["exit_code"]


def update_core(ts, json_str, paths, ctx):
    """Shared logic for --update mode. Returns dict, no printing."""
    _start = time.perf_counter()

    try:
        entry = json.loads(json_str)
    except json.JSONDecodeError as e:
        log_event(paths, "update", outcome="QUARANTINED", outcome_detail=f"JSON parse error: {e}",
                  target_ts=ts, latency_ms=round((time.perf_counter() - _start) * 1000, 2))
        quarantine(paths, json_str, f"JSON parse error: {e}")
        return {"exit_code": 1, "status": "quarantined", "message": f"QUARANTINED: JSON parse error: {e}"}

    errors = validate_entry(entry, ctx)
    if errors:
        reason = "; ".join(errors)
        log_event(paths, "update", outcome="QUARANTINED", outcome_detail=reason,
                  target_ts=ts, entry_type=entry.get("type"), entry_domain=entry.get("domain"),
                  entry_severity=entry.get("severity"),
                  latency_ms=round((time.perf_counter() - _start) * 1000, 2))
        quarantine(paths, json_str, reason)
        return {"exit_code": 1, "status": "quarantined", "message": f"QUARANTINED: {reason}"}

    valid_retrieval_only = ctx.get("valid_retrieval_only_agents")
    if valid_retrieval_only is not None and entry.get("source_agent") in valid_retrieval_only:
        detail = f"{entry['source_agent']} is retrieval-only (use --step mode)"
        log_event(paths, "update", outcome="QUARANTINED", outcome_detail=detail,
                  target_ts=ts, latency_ms=round((time.perf_counter() - _start) * 1000, 2))
        quarantine(paths, json_str, detail)
        return {"exit_code": 1, "status": "quarantined", "message": f"QUARANTINED: {detail}"}

    original_fields = set(entry.keys())
    entry = stamp_entry(entry, paths.repo_root)

    existing_entries = read_learnings(paths)
    found = False
    for i, existing in enumerate(existing_entries):
        if existing.get("ts") == ts:
            old_access_count = existing.get("access_count", 0)
            old_reinforcement_count = existing.get("reinforcement_count", 0)
            entry["access_count"] = old_access_count
            entry["reinforcement_count"] = old_reinforcement_count
            
            if "verified" not in original_fields:
                entry["verified"] = existing.get("verified", False)
            if "scope" not in original_fields:
                entry["scope"] = existing.get("scope", "file")
            if "symptoms" not in original_fields:
                entry["symptoms"] = existing.get("symptoms", "")
            if "debt_level" not in original_fields:
                entry["debt_level"] = existing.get("debt_level", "proper")
            
            entry["schema_version"] = existing.get("schema_version", CURRENT_SCHEMA_VERSION)

            # Re-compute embedding if trigger/action/reason changed
            text_changed = (entry.get("trigger") != existing.get("trigger") or
                            entry.get("action") != existing.get("action") or
                            entry.get("reason") != existing.get("reason"))
            if text_changed:
                _emb_model = ctx.get("embedding_model")
                _emb_cache = ctx.get("embedding_cache_dir")
                entry["embedding"] = encode_embedding(embed_entry(entry, _emb_model, _emb_cache))
            else:
                entry["embedding"] = existing.get("embedding")

            existing_entries[i] = entry
            found = True
            break

    if not found:
        log_event(paths, "update", outcome="NOT_FOUND", target_ts=ts,
                  latency_ms=round((time.perf_counter() - _start) * 1000, 2))
        return {"exit_code": 1, "status": "not_found", "message": f"ERROR: No entry found with ts={ts}"}

    write_learnings(paths, existing_entries)
    msg = f"UPDATED [step-{entry['step']}, {entry['type']}, {entry['domain']}] {entry['trigger']}: {entry['action']}"
    log_event(paths, "update", outcome="UPDATED", target_ts=ts,
              entry_type=entry.get("type"), entry_domain=entry.get("domain"),
              entry_severity=entry.get("severity"),
              latency_ms=round((time.perf_counter() - _start) * 1000, 2))
    return {"exit_code": 0, "status": "updated", "message": msg, "entry": entry}


def handle_update(ts, json_str, paths, ctx):
    """Handle --update mode: amend existing entry. CLI wrapper."""
    result = update_core(ts, json_str, paths, ctx)
    if result["exit_code"] != 0:
        print(result["message"], file=sys.stderr)
    else:
        print(result["message"])
    return result["exit_code"]


def resolve_core(ts, paths):
    """Shared logic for --resolve mode. Returns dict, no printing."""
    _start = time.perf_counter()

    if not TS_PATTERN.match(ts):
        log_event(paths, "resolve", outcome="INVALID_TS", target_ts=ts,
                  latency_ms=round((time.perf_counter() - _start) * 1000, 2))
        return {"exit_code": 1, "status": "invalid_ts",
                "message": f"ERROR: Invalid timestamp format: {ts}. Expected YYYY-MM-DDTHH:MM:SSZ"}
    
    existing_entries = read_learnings(paths)
    found = False
    resolved_entry = None

    for i, existing in enumerate(existing_entries):
        if existing.get("ts") == ts:
            existing["resolved"] = True
            resolved_entry = existing
            found = True
            break

    if not found:
        log_event(paths, "resolve", outcome="NOT_FOUND", target_ts=ts,
                  latency_ms=round((time.perf_counter() - _start) * 1000, 2))
        return {"exit_code": 1, "status": "not_found", "message": f"ERROR: No entry found with ts={ts}"}

    write_learnings(paths, existing_entries)
    msg = (f"RESOLVED [step-{resolved_entry['step']}, {resolved_entry['type']}, "
           f"{resolved_entry['domain']}] {resolved_entry['trigger']}")
    log_event(paths, "resolve", outcome="RESOLVED", target_ts=ts,
              entry_step=resolved_entry.get("step"),
              entry_domain=resolved_entry.get("domain"),
              entry_type=resolved_entry.get("type"),
              latency_ms=round((time.perf_counter() - _start) * 1000, 2))
    return {"exit_code": 0, "status": "resolved", "message": msg, "entry": resolved_entry}


def handle_resolve(ts, paths):
    """
    Handle --resolve mode: mark existing entry as resolved (partial update). CLI wrapper.
    
    Note: Uses read-modify-write pattern without file locking. Safe under
    current sequential execution model. If parallel agent execution is added,
    implement fcntl.flock() or equivalent per-platform locking.
    """
    result = resolve_core(ts, paths)
    if result["exit_code"] != 0:
        print(result["message"], file=sys.stderr)
    else:
        print(result["message"])
    return result["exit_code"]


def stats_core(paths, emit_event: bool = True, ctx=None):
    """Shared logic for --stats mode. Returns dict, no printing."""
    _start = time.perf_counter() if emit_event else None
    entries = read_learnings(paths)

    if not entries:
        if emit_event:
            log_event(paths, "stats", total_entries=0, unresolved=0, resolved=0,
                      latency_ms=round((time.perf_counter() - _start) * 1000, 2))
        return {
            "exit_code": 0, "status": "ok",
            "total": 0, "unresolved": 0, "resolved": 0,
            "avg_access_count": 0.0, "avg_reinforcement_count": 0.0,
            "step_range": [0, 0],
            "severity_breakdown": {}, "type_breakdown": {},
            "scope_breakdown": {}, "debt_breakdown": {},
            "verified": 0, "unverified": 0,
            "proven": 0, "over_injected": 0, "under_retrieved": 0,
            "sleep_cycle_due": False,
            "sleep_cycle_reasons": [],
        }
    
    total = len(entries)
    unresolved = sum(1 for e in entries if not e.get("resolved", False))
    resolved = total - unresolved
    
    avg_access = sum(e.get("access_count", 0) for e in entries) / total
    avg_reinforcement = sum(e.get("reinforcement_count", 0) for e in entries) / total
    
    steps = [e.get("step", 0) for e in entries]
    min_step = min(steps)
    max_step = max(steps)
    
    severity_counts = {}
    for e in entries:
        sev = e.get("severity", "minor")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
    
    type_counts = {}
    for e in entries:
        t = e.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
    
    scope_counts = {}
    for e in entries:
        s = e.get("scope", "file")
        scope_counts[s] = scope_counts.get(s, 0) + 1
    
    debt_counts = {}
    for e in entries:
        d = e.get("debt_level", "proper")
        debt_counts[d] = debt_counts.get(d, 0) + 1
    
    verified_count = sum(1 for e in entries if e.get("verified", False))
    unverified_count = total - verified_count
    
    # Reinforcement pattern analysis
    proven = [e for e in entries if e.get("reinforcement_count", 0) >= 5]
    over_injected = [e for e in entries if e.get("access_count", 0) >= 10 and e.get("reinforcement_count", 0) <= 2]
    under_retrieved = [e for e in entries if e.get("access_count", 0) <= 2 and e.get("reinforcement_count", 0) >= 5]
    
    if ctx is not None:
        sleep_cycle_due, sleep_cycle_reasons = check_sleep_cycle(paths, ctx, unresolved)
    else:
        sleep_cycle_due = unresolved > 50
        sleep_cycle_reasons = ["threshold"] if sleep_cycle_due else []

    if emit_event:
        log_event(paths, "stats",
            total_entries=total, unresolved=unresolved, resolved=resolved,
            avg_access_count=round(avg_access, 2), avg_reinforcement_count=round(avg_reinforcement, 2),
            step_range=[min_step, max_step],
            severity_breakdown=severity_counts, type_breakdown=type_counts,
            scope_breakdown=scope_counts, debt_breakdown=debt_counts,
            verified=verified_count, unverified=unverified_count,
            proven=len(proven), over_injected=len(over_injected), under_retrieved=len(under_retrieved),
            latency_ms=round((time.perf_counter() - _start) * 1000, 2),
        )

    return {
        "exit_code": 0, "status": "ok",
        "total": total, "unresolved": unresolved, "resolved": resolved,
        "avg_access_count": round(avg_access, 2), "avg_reinforcement_count": round(avg_reinforcement, 2),
        "step_range": [min_step, max_step],
        "severity_breakdown": severity_counts, "type_breakdown": type_counts,
        "scope_breakdown": scope_counts, "debt_breakdown": debt_counts,
        "verified": verified_count, "unverified": unverified_count,
        "proven": len(proven), "over_injected": len(over_injected), "under_retrieved": len(under_retrieved),
        "sleep_cycle_due": sleep_cycle_due,
        "sleep_cycle_reasons": sleep_cycle_reasons,
    }


def handle_stats(paths, ctx=None):
    """Handle --stats mode: print summary statistics. CLI wrapper."""
    result = stats_core(paths, ctx=ctx)
    if result["exit_code"] != 0:
        return result["exit_code"]
    
    if result["total"] == 0:
        print("## MEMORY STATS")
        print("No entries found.")
        return 0
    
    print("## MEMORY STATS")
    print(f"Total entries: {result['total']}")
    print(f"Unresolved: {result['unresolved']}")
    print(f"Resolved: {result['resolved']}")
    print(f"Average access_count: {result['avg_access_count']:.1f}")
    print(f"Average reinforcement_count: {result['avg_reinforcement_count']:.1f}")
    print(f"Step range: {result['step_range'][0]}-{result['step_range'][1]}")
    print("\nSeverity breakdown:")
    for sev in ["critical", "major", "minor"]:
        count = result["severity_breakdown"].get(sev, 0)
        print(f"  {sev}: {count}")
    print("\nType breakdown:")
    for t in sorted(result["type_breakdown"].keys()):
        print(f"  {t}: {result['type_breakdown'][t]}")
    print("\nScope breakdown:")
    for s in ["system", "module", "file"]:
        count = result["scope_breakdown"].get(s, 0)
        print(f"  {s}: {count}")
    print("\nDebt level breakdown:")
    for d in ["proper", "workaround", "temporary"]:
        count = result["debt_breakdown"].get(d, 0)
        print(f"  {d}: {count}")
    print(f"\nVerified: {result['verified']} verified, {result['unverified']} unverified")
    print("\nReinforcement patterns:")
    print(f"  Proven (reinforcement >= 5): {result['proven']}")
    print(f"  Over-injected (access >= 10, reinforcement <= 2): {result['over_injected']}")
    print(f"  Under-retrieved (access <= 2, reinforcement >= 5): {result['under_retrieved']}")
    if result["sleep_cycle_due"]:
        reasons = result.get("sleep_cycle_reasons", [])
        if "threshold" in reasons:
            print(f"\n## SLEEP CYCLE DUE — {result['unresolved']} "
                  f"unresolved entries exceed threshold of 50")
        if "time" in reasons:
            days = ctx.get('sleep_cycle_days', 7) if ctx else 7
            print(f"\n## SLEEP CYCLE DUE — {days} days since last consolidation")
        if "quarantine" in reasons:
            qt = ctx.get('sleep_cycle_quarantine_threshold', 20) if ctx else 20
            print(f"\n## SLEEP CYCLE DUE — Quarantine entries exceed threshold of {qt}")
        print("Run the Sleep Cycle per AGENTS.md ## Memory before starting new work.")
    return 0
