"""Core handlers for filter.py operations.

Extracted from filter.py (Phase 6).
"""

from __future__ import annotations

import json
import re
import sys

from engine.io import read_learnings, write_learnings, append_learning, quarantine
from engine.validation import validate_entry, find_best_match, actions_oppose
from engine.git_utils import stamp_entry
from engine.agents_review import check_agents_conflict

TS_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

def handle_log(json_str, paths, ctx):
    """Handle --log mode: validate, dedup-check, append."""
    try:
        entry = json.loads(json_str)
    except json.JSONDecodeError as e:
        quarantine(paths, json_str, f"JSON parse error: {e}")
        print(f"QUARANTINED: JSON parse error: {e}", file=sys.stderr)
        return 1

    errors = validate_entry(entry, ctx)
    if errors:
        reason = "; ".join(errors)
        quarantine(paths, json_str, reason)
        print(f"QUARANTINED: {reason}", file=sys.stderr)
        return 1

    valid_retrieval_only = ctx.get("valid_retrieval_only_agents")
    if valid_retrieval_only is not None and entry.get("source_agent") in valid_retrieval_only:
        quarantine(paths, json_str, f"{entry['source_agent']} is retrieval-only (use --step mode)")
        print(f"QUARANTINED: {entry['source_agent']} is retrieval-only", file=sys.stderr)
        return 1

    entry = stamp_entry(entry, paths.repo_root)

    # AGENTS.md conflict detection (informational, non-blocking)
    conflict_detected, best_section, jaccard_score, containment_hits = check_agents_conflict(entry, paths)
    if conflict_detected:
        print(f"WARNING: Learning may overlap with AGENTS.md section '{best_section}'")
        print(f"  Jaccard: {jaccard_score:.2f}, Containment hits: {containment_hits}")
        print(f"  Learning: {entry['trigger']}: {entry['action']}")
        print(f"  Consider: Updating existing section instead of adding new rule")

    existing_entries = read_learnings(paths)
    similarity, best_match = find_best_match(entry, existing_entries)

    if similarity >= 0.7:
        best_match["access_count"] = best_match.get("access_count", 0) + 1
        best_match["reinforcement_count"] = best_match.get("reinforcement_count", 0) + 1
        write_learnings(paths, existing_entries)
        print(f"DUPLICATE — existing entry matches (similarity: {similarity:.2f}):")
        print(f"  [step-{best_match['step']}, {best_match['domain']}, {best_match['source_agent']}] {best_match['trigger']}: {best_match['action']}")
        print(f"  access_count incremented to {best_match['access_count']}.")
        print(f"  reinforcement_count incremented to {best_match['reinforcement_count']}.")
        return 0

    if 0.4 <= similarity < 0.7:
        if actions_oppose(entry["action"], best_match["action"]):
            append_learning(paths, entry)
            print(f"CONFLICT — potential contradiction detected (similarity: {similarity:.2f}):")
            print(f"  Existing: [step-{best_match['step']}, {best_match['domain']}, {best_match['source_agent']}] {best_match['trigger']}: {best_match['action']}")
            print(f"  Your entry proposes an opposing action for the same trigger.")
            print(f"  Follow the Challenge Protocol: re-submit with type 'architectural_pattern' and")
            print(f"  explain in the reason why the old rule no longer applies.")
            return 0
        else:
            append_learning(paths, entry)
            print(f"ADDED [step-{entry['step']}, {entry['type']}, {entry['domain']}] {entry['trigger']}: {entry['action']}")
            return 0

    append_learning(paths, entry)
    print(f"ADDED [step-{entry['step']}, {entry['type']}, {entry['domain']}] {entry['trigger']}: {entry['action']}")
    return 0


def handle_update(ts, json_str, paths, ctx):
    """Handle --update mode: amend existing entry."""
    try:
        entry = json.loads(json_str)
    except json.JSONDecodeError as e:
        quarantine(paths, json_str, f"JSON parse error: {e}")
        print(f"QUARANTINED: JSON parse error: {e}", file=sys.stderr)
        return 1

    errors = validate_entry(entry, ctx)
    if errors:
        reason = "; ".join(errors)
        quarantine(paths, json_str, reason)
        print(f"QUARANTINED: {reason}", file=sys.stderr)
        return 1

    valid_retrieval_only = ctx.get("valid_retrieval_only_agents")
    if valid_retrieval_only is not None and entry.get("source_agent") in valid_retrieval_only:
        quarantine(paths, json_str, f"{entry['source_agent']} is retrieval-only (use --step mode)")
        print(f"ERROR: Cannot append entry. learnings.jsonl is not writable or missing.", file=sys.stderr)
        return 1

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
            
            existing_entries[i] = entry
            found = True
            break

    if not found:
        print(f"ERROR: No entry found with ts={ts}", file=sys.stderr)
        return 1

    write_learnings(paths, existing_entries)
    print(f"UPDATED [step-{entry['step']}, {entry['type']}, {entry['domain']}] {entry['trigger']}: {entry['action']}")
    return 0


def handle_resolve(ts, paths):
    """
    Handle --resolve mode: mark existing entry as resolved (partial update).
    
    Note: Uses read-modify-write pattern without file locking. Safe under
    current sequential execution model. If parallel agent execution is added,
    implement fcntl.flock() or equivalent per-platform locking.
    """
    if not TS_PATTERN.match(ts):
        print(f"ERROR: Invalid timestamp format: {ts}. Expected YYYY-MM-DDTHH:MM:SSZ", file=sys.stderr)
        return 1
    
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
        print(f"ERROR: No entry found with ts={ts}", file=sys.stderr)
        return 1

    write_learnings(paths, existing_entries)
    print(f"RESOLVED [step-{resolved_entry['step']}, {resolved_entry['type']}, {resolved_entry['domain']}] {resolved_entry['trigger']}")
    return 0


def handle_stats(paths):
    """Handle --stats mode: print summary statistics about the memory system."""
    entries = read_learnings(paths)
    
    if not entries:
        print("## MEMORY STATS")
        print("No entries found.")
        return 0
    
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
    
    print("## MEMORY STATS")
    print(f"Total entries: {total}")
    print(f"Unresolved: {unresolved}")
    print(f"Resolved: {resolved}")
    print(f"Average access_count: {avg_access:.1f}")
    print(f"Average reinforcement_count: {avg_reinforcement:.1f}")
    print(f"Step range: {min_step}-{max_step}")
    print(f"\nSeverity breakdown:")
    for sev in ["critical", "major", "minor"]:
        count = severity_counts.get(sev, 0)
        print(f"  {sev}: {count}")
    print(f"\nType breakdown:")
    for t in sorted(type_counts.keys()):
        print(f"  {t}: {type_counts[t]}")
    print(f"\nScope breakdown:")
    for s in ["system", "module", "file"]:
        count = scope_counts.get(s, 0)
        print(f"  {s}: {count}")
    print(f"\nDebt level breakdown:")
    for d in ["proper", "workaround", "temporary"]:
        count = debt_counts.get(d, 0)
        print(f"  {d}: {count}")
    print(f"\nVerified: {verified_count} verified, {unverified_count} unverified")
    
    print(f"\nReinforcement patterns:")
    print(f"  Proven (reinforcement >= 5): {len(proven)}")
    print(f"  Over-injected (access >= 10, reinforcement <= 2): {len(over_injected)}")
    print(f"  Under-retrieved (access <= 2, reinforcement >= 5): {len(under_retrieved)}")
    
    if unresolved > 50:
        print(f"\n## SLEEP CYCLE DUE — {unresolved} unresolved entries exceed threshold of 50")
        print("Run the Sleep Cycle per AGENTS.md ## Memory before starting new work.")
    
    return 0
