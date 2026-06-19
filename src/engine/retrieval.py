"""Retrieval scoring and filtering for the memory engine.

Extracted from filter.py (Phase 3). All configurable values come from
the ctx dict; file I/O uses the paths object.
"""

from __future__ import annotations

from engine.io import read_learnings, write_learnings
from profile import load_profile, get_profile_context


def score_entry(entry, current_step, task_components, task_files, task_domain, ctx):
    """Score an entry against the current task context.

    ctx keys used: decay_rate, component_weight, file_weight,
                   domain_weight, no_match_weight
    """
    step_diff = current_step - entry["step"]
    recency = ctx["decay_rate"] ** step_diff
    importance = entry["importance"] / 10.0

    task_components_lower = {c.lower() for c in task_components}
    entry_components_lower = {c.lower() for c in entry["components"]}
    if task_components_lower & entry_components_lower:
        relevance = ctx["component_weight"]
    elif task_files and set(task_files) & set(entry.get("files_touched", [])):
        relevance = ctx["file_weight"]
    elif task_domain and task_domain == entry.get("domain"):
        relevance = ctx["domain_weight"]
    else:
        relevance = ctx["no_match_weight"]

    return recency * importance * relevance


def is_in_retention(entry, current_step, ctx):
    """Check if an entry is within its retention window.

    ctx keys used: major_retention, minor_retention
    """
    severity = entry["severity"]
    step_diff = current_step - entry["step"]
    access_count = entry.get("access_count", 0)

    if severity == "critical":
        return True
    elif severity == "major":
        return step_diff <= ctx["major_retention"]
    elif severity == "minor":
        return step_diff <= ctx["minor_retention"] or access_count > 3
    return False


def handle_retrieval(current_step, task_components, task_files, task_domain, ctx, paths):
    """Handle retrieval mode: score, filter, and print relevant learnings.

    ctx keys used: score_threshold, escalation_threshold, max_warnings,
                   max_patterns, max_step, domain_mappings,
                   + all keys used by score_entry and is_in_retention
    """
    entries = read_learnings(paths)

    warnings = []
    patterns = []
    escalations = []

    for entry in entries:
        if entry.get("resolved", False):
            continue

        if not is_in_retention(entry, current_step, ctx):
            continue

        score = score_entry(entry, current_step, task_components, task_files, task_domain, ctx)

        if entry["severity"] == "critical" or score >= ctx["score_threshold"]:
            if entry["severity"] == "critical":
                warnings.append((score, entry))
                step_diff = current_step - entry["step"]
                if step_diff >= ctx["escalation_threshold"]:
                    escalations.append(entry)
            else:
                patterns.append((score, entry))

    warnings.sort(key=lambda x: x[0], reverse=True)
    patterns.sort(key=lambda x: x[0], reverse=True)

    warnings = warnings[:ctx["max_warnings"]]
    patterns = patterns[:ctx["max_patterns"]]

    for _, entry in warnings + patterns:
        entry["access_count"] = entry.get("access_count", 0) + 1

    if warnings or patterns:
        write_learnings(paths, entries)

    print("## ⚠ WARNINGS — Read before starting")
    if warnings:
        for _, entry in warnings:
            print(f"- [step-{entry['step']}, {entry['domain']}, {entry['source_agent']}] {entry['trigger']}: {entry['action']} Reason: {entry['reason']}")
            verified_str = "verified" if entry.get("verified", False) else "unverified"
            scope_str = entry.get("scope", "file")
            debt_str = entry.get("debt_level", "proper")
            symptoms_str = entry.get("symptoms", "")
            print(f"  ({verified_str}, scope: {scope_str}, debt: {debt_str}, accessed {entry.get('access_count', 0)}x, reinforced {entry.get('reinforcement_count', 0)}x)")
            if symptoms_str:
                print(f"  Symptoms: {symptoms_str}")
    else:
        print("(none)")

    profile = load_profile()
    # Pass config-provided domain_mappings (may be None if not in config)
    profile_context = get_profile_context(profile, task_domain, domain_mappings=ctx.get("domain_mappings"))
    print("\n## 🎯 DEVELOPER PREFERENCES")
    if profile_context:
        for pref in profile_context:
            print(f"- [{pref['source']}] {pref['trigger']}: {pref['action']}")
            print(f"  Reason: {pref['reason']}")
    else:
        print("(none)")

    print("\n## RELEVANT PATTERNS")
    if patterns:
        for _, entry in patterns:
            print(f"- [step-{entry['step']}, {entry['domain']}, {entry['source_agent']}] {entry['trigger']}: {entry['action']} Reason: {entry['reason']}")
            verified_str = "verified" if entry.get("verified", False) else "unverified"
            scope_str = entry.get("scope", "file")
            debt_str = entry.get("debt_level", "proper")
            symptoms_str = entry.get("symptoms", "")
            print(f"  ({verified_str}, scope: {scope_str}, debt: {debt_str}, accessed {entry.get('access_count', 0)}x, reinforced {entry.get('reinforcement_count', 0)}x)")
            if symptoms_str:
                print(f"  Symptoms: {symptoms_str}")
    else:
        print("(none)")

    if escalations:
        print("\n## ⚡ ESCALATION — Critical entries unresolved for 30+ steps")
        for entry in escalations:
            print(f"- [step-{entry['step']}, {entry['domain']}, {entry['source_agent']}] {entry['trigger']}: {entry['action']}")

    unresolved_count = sum(1 for e in entries if not e.get("resolved", False))

    print(f"\n## STATS: {len(entries)} total entries, {unresolved_count} unresolved")

    max_step = ctx.get("max_step")
    sleep_cycle_steps = {10, 20, max_step} if max_step is not None else {10, 20}
    if unresolved_count > 50 or current_step in sleep_cycle_steps:
        if unresolved_count > 50:
            print(f"\n## SLEEP CYCLE DUE — {unresolved_count} unresolved entries exceed threshold of 50")
        if current_step in sleep_cycle_steps:
            print(f"\n## SLEEP CYCLE DUE — Sprint boundary at step {current_step} ({unresolved_count} unresolved entries)")
        print("Run the Sleep Cycle per AGENTS.md ## Memory before starting new work.")

    return 0
