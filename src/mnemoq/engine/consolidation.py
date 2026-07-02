# Copyright (C) 2026 Mnemoq
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Sleep Cycle (Consolidation) logic for the memory engine.

Extracted from filter.py (Phase 4).
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from mnemoq.engine.git_utils import check_staleness, staleness_tier
from mnemoq.engine.io import read_learnings
from mnemoq.engine.metrics import log_event, read_metrics


def _sprint_metrics(paths):
    """Return a 3-line metrics snapshot string for the consolidate report, or None."""
    try:
        events = read_metrics(paths)
        if not events:
            return None

        retrievals = [e for e in events if e.get("event_type") == "retrieval"]
        logs = [e for e in events if e.get("event_type") == "log"]

        lines = []
        if retrievals:
            hits = sum(1 for e in retrievals
                       if (e.get("warnings_returned", 0) or 0) + (e.get("patterns_returned", 0) or 0) > 0)
            avg_lat = sum(e.get("latency_ms", 0) or 0 for e in retrievals) / len(retrievals)
            lines.append(f"  Retrieval: {len(retrievals)} calls, "
                         f"{hits/len(retrievals):.0%} hit rate, {avg_lat:.0f}ms avg")

        if logs:
            dups = sum(1 for e in logs if e.get("outcome") == "DUPLICATE")
            quars = sum(1 for e in logs if e.get("outcome") == "QUARANTINED")
            lines.append(f"  Logging: {len(logs)} entries, {dups/len(logs):.0%} dup, {quars/len(logs):.0%} quarantined")

        all_lat = [e.get("latency_ms", 0) or 0 for e in events if e.get("latency_ms")]
        if all_lat:
            lines.append(f"  Overall: {len(events)} events, {sum(all_lat)/len(all_lat):.0f}ms avg latency")

        return "\n".join(lines) if lines else None
    except Exception:
        return None


def score_for_promotion(entry, current_step, ctx):
    """Score an entry for promotion to SYSTEM_INVARIANTS.md.

    The scoring weights, divisors, and severity map are all config-tunable
    (PROMOTION_* in constants.py / tuning block); defaults reproduce the
    historical hardcoded values so behavior is unchanged unless overridden.
    """
    ctx = ctx or {}
    access_count = entry.get("access_count", 0)
    severity = entry.get("severity", "minor")
    step_diff = current_step - entry.get("step", current_step)

    divisor = ctx.get("promotion_access_divisor", 10.0) or 10.0
    access_score = min(access_count / divisor, 1.0)

    minor_score = ctx.get("promotion_severity_minor", 0.3)
    severity_map = {
        "critical": ctx.get("promotion_severity_critical", 1.0),
        "major": ctx.get("promotion_severity_major", 0.6),
        "minor": minor_score,
    }
    severity_score = severity_map.get(severity, minor_score)

    window = ctx.get("promotion_recency_window", 30.0) or 30.0
    recency_score = max(0.0, 1.0 - step_diff / window)

    promotion_score = (
        ctx.get("promotion_weight_access", 0.4) * access_score +
        ctx.get("promotion_weight_severity", 0.4) * severity_score +
        ctx.get("promotion_weight_recency", 0.2) * recency_score
    )

    return promotion_score


def is_promotion_candidate(entry, current_step, ctx):
    """Check if an entry qualifies for promotion.

    Candidacy cutoff and the critical/high-access force rules are config-tunable
    (promotion_score_threshold, promotion_force_critical, promotion_force_access).
    """
    ctx = ctx or {}
    score = score_for_promotion(entry, current_step, ctx)
    severity = entry.get("severity", "minor")
    access_count = entry.get("access_count", 0)

    if score >= ctx.get("promotion_score_threshold", 0.5):
        return True, score
    if ctx.get("promotion_force_critical", True) and severity == "critical":
        return True, score
    if access_count > ctx.get("promotion_force_access", 5):
        return True, score

    return False, score


def detect_contradictions(entries):
    """Identify architectural_pattern entries proposing supersession."""
    contradiction_keywords = {"supersede", "outdated", "no longer applies", "conflicts with", "replaces"}
    contradictions = []

    for entry in entries:
        if entry.get("type") != "architectural_pattern":
            continue

        reason = entry.get("reason", "").lower()
        if any(kw in reason for kw in contradiction_keywords):
            contradictions.append(entry)

    return contradictions


def review_quarantine(paths):
    """Read quarantine.jsonl and summarize entries."""
    if not os.path.exists(paths.quarantine_path):
        return 0, {}, []

    entries = []
    with open(paths.quarantine_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not entries:
        return 0, {}, []

    reason_counts = {}
    for entry in entries:
        reason = entry.get("reason", "unknown")
        if "JSON parse error" in reason:
            category = "JSON parse errors"
        elif "Missing required field" in reason or "must be" in reason:
            category = "Schema validation errors"
        elif "retrieval-only" in reason:
            category = "Permission/agent errors"
        else:
            category = "Other errors"

        reason_counts[category] = reason_counts.get(category, 0) + 1

    recent = entries[-5:] if len(entries) > 5 else entries

    return len(entries), reason_counts, recent


def get_agents_md_suggestions(entries):
    """Find learnings that suggest AGENTS.md updates."""
    suggestions = []
    for entry in entries:
        files_touched = entry.get("files_touched", [])
        components = entry.get("components", [])

        has_agents_ref = (
            any("AGENTS.md" in f for f in files_touched) or
            any("agents" in c.lower() for c in components)
        )

        if has_agents_ref:
            suggestions.append(entry)

    return suggestions


def infer_sprint_number(entries):
    """Infer sprint number from max step in entries."""
    if not entries:
        return 1

    max_step = max(e.get("step", 0) for e in entries)
    return math.ceil(max_step / 10)


def save_session(sprint_number, paths):
    """Save session timestamp for --confirm-reset safety check."""
    session_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sprint": sprint_number
    }
    try:
        with open(paths.session_file, "w", encoding="utf-8") as f:
            json.dump(session_data, f)
    except OSError as e:
        print(f"WARNING: Could not save session file: {e}", file=sys.stderr)


def load_session(paths, ctx):
    """Load session timestamp. Returns (timestamp, sprint) or (None, None) if missing/invalid."""
    if not os.path.exists(paths.session_file):
        return None, None

    try:
        with open(paths.session_file, encoding="utf-8") as f:
            data = json.load(f)

        ts = datetime.fromisoformat(data["timestamp"])
        sprint = data.get("sprint", 1)

        now = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        expiry = ctx.get("session_expiry_minutes", 10)
        if (now - ts) > timedelta(minutes=expiry):
            return None, None

        return ts, sprint

    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return None, None


def clear_session(paths):
    """Remove session file after successful reset."""
    if os.path.exists(paths.session_file):
        try:
            os.remove(paths.session_file)
        except OSError:
            pass


def handle_confirm_reset(paths, ctx):
    """Handle --consolidate --confirm-reset: clear learnings.jsonl after review."""
    _start = time.perf_counter()
    ts, sprint = load_session(paths, ctx)

    if ts is None:
        log_event(paths, "consolidate", outcome="NO_SESSION", confirm_reset=True,
                  latency_ms=round((time.perf_counter() - _start) * 1000, 2))
        print("ERROR: No recent --consolidate session found.")
        print("  Run --consolidate first, then --confirm-reset within 10 minutes.")
        return 1

    print(f"âœ“ Session valid (sprint {sprint}, started {ts.strftime('%H:%M:%S')})")

    open(paths.learnings_path, "w", encoding="utf-8").close()  # Empty file

    print("âœ“ learnings.jsonl reset. Sprint complete.")

    clear_session(paths)

    log_event(paths, "consolidate", outcome="RESET", confirm_reset=True,
              sprint_number=sprint,
              latency_ms=round((time.perf_counter() - _start) * 1000, 2))

    return 0


def consolidate_core(sprint_number, confirm_reset, force, paths, ctx):
    """Shared logic for --consolidate mode. Returns dict, no printing.

    Returns:
        {"exit_code": int, "status": str, ...report fields}
    """
    if confirm_reset:
        return {"exit_code": handle_confirm_reset(paths, ctx), "status": "confirm_reset"}

    _start = time.perf_counter()
    entries = read_learnings(paths)
    unresolved = [e for e in entries if not e.get("resolved", False)]

    if not unresolved:
        log_event(paths, "consolidate", outcome="NO_ENTRIES",
                  latency_ms=round((time.perf_counter() - _start) * 1000, 2))
        return {"exit_code": 0, "status": "no_entries",
                "message": "No unresolved entries to consolidate. Archive not created."}

    if sprint_number is None:
        sprint_number = infer_sprint_number(unresolved)

    archive_path = os.path.join(paths.archive_dir, f"sprint-{sprint_number}.jsonl")

    if os.path.exists(archive_path) and not force:
        log_event(paths, "consolidate", outcome="ARCHIVE_EXISTS", sprint_number=sprint_number,
                  latency_ms=round((time.perf_counter() - _start) * 1000, 2))
        return {"exit_code": 1, "status": "archive_exists",
                "message": (f"Warning: {archive_path} already exists. "
                            "Use --force to overwrite, or specify a different sprint number."),
                "archive_path": archive_path}

    os.makedirs(paths.archive_dir, exist_ok=True)

    with open(archive_path, "w", encoding="utf-8") as f:
        for entry in unresolved:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    current_step = max(e.get("step", 0) for e in unresolved)

    candidates = []
    for entry in unresolved:
        is_candidate, score = is_promotion_candidate(entry, current_step, ctx)
        if is_candidate:
            candidates.append((score, entry))

    candidates.sort(key=lambda x: x[0], reverse=True)

    contradictions = detect_contradictions(unresolved)

    quarantine_count, quarantine_breakdown, quarantine_recent = review_quarantine(paths)

    stale_entries = []
    for entry in unresolved:
        is_stale, lines_changed, error = check_staleness(entry, paths.repo_root, ctx)
        stale_entries.append((is_stale, lines_changed, error, entry))

    stale_count = sum(1 for is_stale, _, _, _ in stale_entries if is_stale)
    error_count = sum(1 for _, _, err, _ in stale_entries if err is not None)

    tier_counts = {"minor": 0, "moderate": 0, "severe": 0}
    for is_stale, lines_changed, err, _ in stale_entries:
        if is_stale and err is None:
            tier_counts[staleness_tier(lines_changed, ctx)] += 1

    agents_suggestions = get_agents_md_suggestions(unresolved)

    _metrics_summary = _sprint_metrics(paths)

    # Accept-driven threshold lowering (homeostasis Part B). Periodic, access-
    # based recompute of each domain's usefulness offset — the safe lowering half
    # deferred by the per-turn loop. Gated by adaptive_thresholds; best-effort.
    usefulness_lowered = 0
    if ctx.get("adaptive_thresholds", False):
        from mnemoq.engine import homeostasis as hz
        dom_stats = {}
        for e in entries:
            if e.get("source_agent") != "system":
                continue
            d = e.get("domain")
            if not d:
                continue
            st = dom_stats.setdefault(d, {"n": 0, "_sum": 0})
            st["n"] += 1
            st["_sum"] += int(e.get("access_count", 0) or 0)
        for st in dom_stats.values():
            st["mean_access"] = st["_sum"] / st["n"] if st["n"] else 0.0
        state = hz.load_state(paths)
        usefulness_lowered = hz.recompute_usefulness(state, dom_stats, ctx)
        hz.save_state(paths, state)

    # Promotion feedback loop (best-effort). Record this pass's candidates and
    # follow up on whether previously-proposed candidates were reinforced (their
    # access_count grew since proposal). Never raises; degrades to no-op.
    from mnemoq.engine import promotion_feedback as pf
    pf_state = pf.load_state(paths)
    current_by_key = {pf.entry_key(e): int(e.get("access_count", 0) or 0) for e in entries}
    promotion_follow_up = pf.record_and_follow_up(pf_state, candidates, current_by_key)
    pf.save_state(paths, pf_state)

    save_session(sprint_number, paths)

    log_event(paths, "consolidate",
        outcome="REPORTED",
        sprint_number=sprint_number,
        total_entries=len(entries),
        unresolved_entries=len(unresolved),
        archived=len(unresolved),
        promotion_candidates=len(candidates),
        promotion_tracked=promotion_follow_up["tracked"],
        promotion_reinforced=promotion_follow_up["reinforced"],
        contradictions=len(contradictions),
        quarantine_count=quarantine_count,
        stale_entries=stale_count,
        stale_errors=error_count,
        stale_moderate=tier_counts["moderate"],
        stale_severe=tier_counts["severe"],
        agents_md_suggestions=len(agents_suggestions),
        usefulness_lowered=usefulness_lowered,
        latency_ms=round((time.perf_counter() - _start) * 1000, 2),
    )

    return {
        "exit_code": 0,
        "status": "reported",
        "sprint_number": sprint_number,
        "archive_path": archive_path,
        "archived_count": len(unresolved),
        "total_entries": len(entries),
        "current_step": current_step,
        "promotion_candidates": [{"score": s, "entry": e} for s, e in candidates],
        "contradictions": contradictions,
        "quarantine": {
            "count": quarantine_count,
            "breakdown": quarantine_breakdown,
            "recent": quarantine_recent,
        },
        "stale": {
            "count": stale_count,
            "error_count": error_count,
            "tier_counts": tier_counts,
            "entries": [{"is_stale": s, "lines_changed": lc,
                         "error": err,
                         "tier": staleness_tier(lc, ctx) if (s and err is None) else "none",
                         "entry": e}
                        for s, lc, err, e in stale_entries],
        },
        "agents_md_suggestions": agents_suggestions,
        "metrics_summary": _metrics_summary,
        "promotion_follow_up": promotion_follow_up,
        "session_expiry_minutes": ctx.get("session_expiry_minutes", 10),
    }


def handle_consolidate(sprint_number, confirm_reset, force, paths, ctx):
    """Handle --consolidate mode: Sleep Cycle for episodic memory. CLI wrapper.

    Calls consolidate_core and prints the report.
    """
    result = consolidate_core(sprint_number, confirm_reset, force, paths, ctx)

    if result["exit_code"] != 0:
        print(result.get("message", "ERROR"), file=sys.stderr)
        return result["exit_code"]

    if result["status"] == "confirm_reset":
        return result["exit_code"]

    if result["status"] == "no_entries":
        print(result["message"])
        return 0

    sprint = result["sprint_number"]
    print(f"\nArchived {result['archived_count']} entries to {result['archive_path']}")

    print("\n" + "=" * 60)
    print(f"## SLEEP CYCLE REPORT — Sprint {sprint}")
    print("=" * 60)

    candidates = result["promotion_candidates"]
    print(f"\n## PROMOTION CANDIDATES ({len(candidates)} entries)")
    print("The following entries are candidates for promotion to SYSTEM_INVARIANTS.md.")
    print("Review each entry, then apply approved entries to the invariants file.\n")

    pfu = result.get("promotion_follow_up")
    if pfu and pfu.get("tracked"):
        print(f"Promotion follow-up: {pfu['reinforced']} of {pfu['tracked']} previously-proposed "
              f"entries reinforced (accessed more) since proposal.\n")

    if candidates:
        for i, c in enumerate(candidates, 1):
            entry = c["entry"]
            print(f"### Candidate {i}: [step-{entry.get('step', '?')}, "
                  f"{entry.get('domain', '?')}, {entry.get('source_agent', '?')}]")
            print(f"**Trigger:** {entry.get('trigger', '')}")
            print(f"**Action:** {entry.get('action', '')}")
            print(f"**Reason:** {entry.get('reason', '')}")
            print(f"**Promotion score:** {c['score']:.2f} "
                  f"(access_count={entry.get('access_count', 0)}, "
                  f"severity={entry.get('severity', 'minor')}, "
                  f"step_diff={result['current_step'] - entry.get('step', 0)})")
            print()
    else:
        print("No promotion candidates found.\n")

    contradictions = result["contradictions"]
    print(f"\n## CONTRADICTIONS ({len(contradictions)} entries)")
    print("The following entries propose superseding existing invariants via the Challenge Protocol.")
    print("Review carefully — these may indicate outdated rules or necessary architectural changes.\n")

    if contradictions:
        for i, entry in enumerate(contradictions, 1):
            print(f"### Contradiction {i}: [step-{entry.get('step', '?')}, "
                  f"{entry.get('domain', '?')}, {entry.get('source_agent', '?')}]")
            print(f"**Trigger:** {entry.get('trigger', '')}")
            print(f"**Action:** {entry.get('action', '')}")
            print(f"**Reason:** {entry.get('reason', '')}")
            print("**Action required:** Review and update SYSTEM_INVARIANTS.md if applicable")
            print()
    else:
        print("No contradictions detected.\n")

    q = result["quarantine"]
    print("\n## QUARANTINE REVIEW")
    print(f"Total quarantined entries: {q['count']}\n")

    if q["breakdown"]:
        print("### Breakdown by reason:")
        for category, count in sorted(q["breakdown"].items()):
            print(f"  - {category}: {count}")
        print()

    if q["recent"]:
        print("### Recent failures:")
        for i, entry in enumerate(q["recent"], 1):
            print(f"{i}. [{entry.get('ts', '?')}] {entry.get('reason', 'unknown')}")
            print(f"   Raw: {entry.get('raw', '')[:80]}...")
        print()

    print("Interpretation guidance:")
    print("- Chronic quarantine from one agent \u2192 meta_learning signal (agent doesn't understand schema)")
    print("- Repeated validation errors \u2192 need for better documentation or examples")
    print('- Clear quarantine after review: echo "" > memory/quarantine.jsonl')
    print()

    stale = result["stale"]
    print("\n## STALE ENTRIES")
    print("The following entries may be stale based on git history.")
    print("Verify against current code before promoting.\n")

    tc = stale.get("tier_counts")
    if tc and stale["count"] > 0:
        print(f"By churn tier: {tc['minor']} minor · {tc['moderate']} moderate · "
              f"{tc['severe']} severe\n")

    if stale["count"] > 0 or stale["error_count"] > 0:
        idx = 1
        for item in stale["entries"]:
            if item["error"]:
                entry = item["entry"]
                print(f"### Uncheckable {idx}: [step-{entry.get('step', '?')}, "
                      f"{entry.get('domain', '?')}, {entry.get('source_agent', '?')}]")
                print(f"**Status:**  Could not check staleness — {item['error']}")
                print(f"**Entry:** {entry.get('trigger', '')}")
                print()
                idx += 1
            elif item["is_stale"]:
                entry = item["entry"]
                tier = item.get("tier", "minor")
                print(f"### Stale {idx}: [step-{entry.get('step', '?')}, "
                      f"{entry.get('domain', '?')}, {entry.get('source_agent', '?')}]")
                print(f"**Commit:** {entry.get('commit', 'unknown')}")
                print(f"**Files touched:** {', '.join(entry.get('files_touched', []))}")
                print(f"**Lines changed since entry:** {item['lines_changed']}")
                print(f"**Status:**  {tier.upper()} CHURN — verify trigger/action still hold")
                print(f"**Entry:** {entry.get('trigger', '')}")
                print()
                idx += 1
    else:
        print("No stale entries detected.\n")

    if result["agents_md_suggestions"]:
        print(f"\n## AGENTS.md Updates Suggested ({len(result['agents_md_suggestions'])} entries)")
        print("The following learnings reference AGENTS.md and may suggest updates.\n")
        for entry in result["agents_md_suggestions"]:
            print(f"- [step-{entry.get('step', '?')}, {entry.get('domain', '?')}, "
                  f"{entry.get('source_agent', '?')}] {entry.get('trigger', '')}")
            print("  \u2192 Suggests reviewing AGENTS.md for potential updates")
        print()

    if result["metrics_summary"]:
        print("=" * 60)
        print("## SPRINT METRICS")
        print("=" * 60)
        print(result["metrics_summary"])
        print()

    print("=" * 60)
    print("## NEXT STEPS")
    print("=" * 60)
    print("1. Review promotion candidates above")
    print("2. Draft proposed diff to SYSTEM_INVARIANTS.md (output in chat or scratch file)")
    print("3. Human reviews and applies approved entries to SYSTEM_INVARIANTS.md")
    print("4. Human confirms with: python memory/filter.py --consolidate --confirm-reset")
    print()

    expiry = result.get("session_expiry_minutes", 10)
    print(f"Session saved. Run --confirm-reset within {expiry} minutes to clear learnings.jsonl.")

    return 0
