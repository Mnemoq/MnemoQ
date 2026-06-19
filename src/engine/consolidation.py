"""Sleep Cycle (Consolidation) logic for the memory engine.

Extracted from filter.py (Phase 4).
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone, timedelta

from engine.io import read_learnings
from engine.git_utils import check_staleness


def score_for_promotion(entry, current_step, ctx):
    """Score an entry for promotion to SYSTEM_INVARIANTS.md."""
    access_count = entry.get("access_count", 0)
    severity = entry.get("severity", "minor")
    step_diff = current_step - entry.get("step", current_step)

    access_score = min(access_count / 10.0, 1.0)

    severity_map = {"critical": 1.0, "major": 0.6, "minor": 0.3}
    severity_score = severity_map.get(severity, 0.3)

    recency_score = max(0.0, 1.0 - step_diff / 30.0)

    promotion_score = (
        0.4 * access_score +
        0.4 * severity_score +
        0.2 * recency_score
    )

    return promotion_score


def is_promotion_candidate(entry, current_step, ctx):
    """Check if an entry qualifies for promotion."""
    score = score_for_promotion(entry, current_step, ctx)
    severity = entry.get("severity", "minor")
    access_count = entry.get("access_count", 0)

    if score >= 0.5:
        return True, score
    if severity == "critical":
        return True, score
    if access_count > 5:
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
    with open(paths.quarantine_path, "r", encoding="utf-8") as f:
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
    except IOError as e:
        print(f"WARNING: Could not save session file: {e}", file=sys.stderr)


def load_session(paths, ctx):
    """Load session timestamp. Returns (timestamp, sprint) or (None, None) if missing/invalid."""
    if not os.path.exists(paths.session_file):
        return None, None

    try:
        with open(paths.session_file, "r", encoding="utf-8") as f:
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

    except (json.JSONDecodeError, KeyError, ValueError, IOError):
        return None, None


def clear_session(paths):
    """Remove session file after successful reset."""
    if os.path.exists(paths.session_file):
        try:
            os.remove(paths.session_file)
        except IOError:
            pass


def handle_confirm_reset(paths, ctx):
    """Handle --consolidate --confirm-reset: clear learnings.jsonl after review."""
    ts, sprint = load_session(paths, ctx)

    if ts is None:
        print("ERROR: No recent --consolidate session found.")
        print("  Run --consolidate first, then --confirm-reset within 10 minutes.")
        return 1

    print(f"âœ“ Session valid (sprint {sprint}, started {ts.strftime('%H:%M:%S')})")

    with open(paths.learnings_path, "w", encoding="utf-8") as f:
        pass  # Empty file

    print("âœ“ learnings.jsonl reset. Sprint complete.")

    clear_session(paths)

    return 0


def handle_consolidate(sprint_number, confirm_reset, force, paths, ctx):
    """Handle --consolidate mode: Sleep Cycle for episodic memory."""
    if confirm_reset:
        return handle_confirm_reset(paths, ctx)

    entries = read_learnings(paths)
    unresolved = [e for e in entries if not e.get("resolved", False)]

    if not unresolved:
        print("âš  No unresolved entries to consolidate. Archive not created.")
        return 0

    if sprint_number is None:
        sprint_number = infer_sprint_number(unresolved)

    archive_path = os.path.join(paths.archive_dir, f"sprint-{sprint_number}.jsonl")

    if os.path.exists(archive_path) and not force:
        print(f"âš  Warning: {archive_path} already exists.")
        print("  Use --force to overwrite, or specify a different sprint number.")
        return 1

    os.makedirs(paths.archive_dir, exist_ok=True)

    with open(archive_path, "w", encoding="utf-8") as f:
        for entry in unresolved:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"âœ“ Archived {len(unresolved)} entries to {archive_path}")

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
        is_stale, lines_changed, error = check_staleness(entry, paths.repo_root)
        stale_entries.append((is_stale, lines_changed, error, entry))

    agents_suggestions = get_agents_md_suggestions(unresolved)

    print("\n" + "=" * 60)
    print(f"## SLEEP CYCLE REPORT â€” Sprint {sprint_number}")
    print("=" * 60)

    print(f"\n## ðŸŽ¯ PROMOTION CANDIDATES ({len(candidates)} entries)")
    print("The following entries are candidates for promotion to SYSTEM_INVARIANTS.md.")
    print("Review each entry, then apply approved entries to the invariants file.\n")

    if candidates:
        for i, (score, entry) in enumerate(candidates, 1):
            print(f"### Candidate {i}: [step-{entry.get('step', '?')}, {entry.get('domain', '?')}, {entry.get('source_agent', '?')}]")
            print(f"**Trigger:** {entry.get('trigger', '')}")
            print(f"**Action:** {entry.get('action', '')}")
            print(f"**Reason:** {entry.get('reason', '')}")
            print(f"**Promotion score:** {score:.2f} (access_count={entry.get('access_count', 0)}, severity={entry.get('severity', 'minor')}, step_diff={current_step - entry.get('step', 0)})")
            print()
    else:
        print("No promotion candidates found.\n")

    print(f"\n## âš”ï¸ CONTRADICTIONS ({len(contradictions)} entries)")
    print("The following entries propose superseding existing invariants via the Challenge Protocol.")
    print("Review carefully â€” these may indicate outdated rules or necessary architectural changes.\n")

    if contradictions:
        for i, entry in enumerate(contradictions, 1):
            print(f"### Contradiction {i}: [step-{entry.get('step', '?')}, {entry.get('domain', '?')}, {entry.get('source_agent', '?')}]")
            print(f"**Trigger:** {entry.get('trigger', '')}")
            print(f"**Action:** {entry.get('action', '')}")
            print(f"**Reason:** {entry.get('reason', '')}")
            print(f"**Action required:** Review and update SYSTEM_INVARIANTS.md if applicable")
            print()
    else:
        print("No contradictions detected.\n")

    print(f"\n## ðŸ—‘ï¸ QUARANTINE REVIEW")
    print(f"Total quarantined entries: {quarantine_count}\n")

    if quarantine_breakdown:
        print("### Breakdown by reason:")
        for category, count in sorted(quarantine_breakdown.items()):
            print(f"  - {category}: {count}")
        print()

    if quarantine_recent:
        print("### Recent failures:")
        for i, entry in enumerate(quarantine_recent, 1):
            print(f"{i}. [{entry.get('ts', '?')}] {entry.get('reason', 'unknown')}")
            print(f"   Raw: {entry.get('raw', '')[:80]}...")
        print()

    print("Interpretation guidance:")
    print("- Chronic quarantine from one agent â†’ meta_learning signal (agent doesn't understand schema)")
    print("- Repeated validation errors â†’ need for better documentation or examples")
    print("- Clear quarantine after review: echo \"\" > memory/quarantine.jsonl")
    print()

    print(f"\n## ðŸ•°ï¸ STALE ENTRIES")
    print("The following entries may be stale based on git history.")
    print("Verify against current code before promoting.\n")

    stale_count = sum(1 for is_stale, _, _, _ in stale_entries if is_stale)
    error_count = sum(1 for _, _, err, _ in stale_entries if err is not None)

    if stale_count > 0 or error_count > 0:
        idx = 1
        for is_stale, lines_changed, error, entry in stale_entries:
            if error:
                print(f"### Uncheckable {idx}: [step-{entry.get('step', '?')}, {entry.get('domain', '?')}, {entry.get('source_agent', '?')}]")
                print(f"**Status:**  Could not check staleness â€” {error}")
                print(f"**Entry:** {entry.get('trigger', '')}")
                print()
                idx += 1
            elif is_stale:
                print(f"### Stale {idx}: [step-{entry.get('step', '?')}, {entry.get('domain', '?')}, {entry.get('source_agent', '?')}]")
                print(f"**Commit:** {entry.get('commit', 'unknown')}")
                print(f"**Files touched:** {', '.join(entry.get('files_touched', []))}")
                print(f"**Lines changed since entry:** {lines_changed}")
                print(f"**Status:**  HIGH CHURN â€” verify trigger/action still hold")
                print(f"**Entry:** {entry.get('trigger', '')}")
                print()
                idx += 1
    else:
        print("No stale entries detected.\n")

    if agents_suggestions:
        print(f"\n## AGENTS.md Updates Suggested ({len(agents_suggestions)} entries)")
        print("The following learnings reference AGENTS.md and may suggest updates.\n")

        for entry in agents_suggestions:
            print(f"- [step-{entry.get('step', '?')}, {entry.get('domain', '?')}, {entry.get('source_agent', '?')}] {entry.get('trigger', '')}")
            print(f"  â†’ Suggests reviewing AGENTS.md for potential updates")
        print()

    print("=" * 60)
    print("## NEXT STEPS")
    print("=" * 60)
    print("1. Review promotion candidates above")
    print("2. Draft proposed diff to SYSTEM_INVARIANTS.md (output in chat or scratch file)")
    print("3. Human reviews and applies approved entries to SYSTEM_INVARIANTS.md")
    print("4. Human confirms with: python memory/filter.py --consolidate --confirm-reset")
    print()

    save_session(sprint_number, paths)
    expiry = ctx.get("session_expiry_minutes", 10)
    print(f"âœ“ Session saved. Run --confirm-reset within {expiry} minutes to clear learnings.jsonl.")

    return 0
