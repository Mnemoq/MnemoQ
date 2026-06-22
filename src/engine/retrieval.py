"""Retrieval scoring and filtering for the memory engine.

Extracted from filter.py (Phase 3). All configurable values come from
the ctx dict; file I/O uses the paths object.
"""

from __future__ import annotations

import math
import re
import time

from engine.io import read_learnings, write_learnings
from engine.metrics import log_event
from .profile import load_profile, get_profile_context


_TOKEN_SPLIT = re.compile(r'[^a-z0-9]+')


def _tokenize(text, stop_words):
    """Lowercase, split on non-alphanumeric, remove stop words. Returns list (preserves duplicates)."""
    tokens = _TOKEN_SPLIT.split(text.lower())
    return [t for t in tokens if t and t not in stop_words]


def _compute_corpus_stats(entries, stop_words):
    """Compute (doc_freqs, total_docs, avg_doc_len) from entries.

    doc_freqs: dict[str, int] — term → number of docs containing it
    total_docs: int — N
    avg_doc_len: float — mean token count per doc
    """
    doc_freqs = {}
    total_docs = len(entries)
    total_len = 0

    for entry in entries:
        text = entry["trigger"] + " " + entry["action"] + " " + entry["reason"]
        tokens = _tokenize(text, stop_words)
        total_len += len(tokens)
        for term in set(tokens):
            doc_freqs[term] = doc_freqs.get(term, 0) + 1

    avg_doc_len = total_len / total_docs if total_docs > 0 else 0.0
    return doc_freqs, total_docs, avg_doc_len


def bm25_score(query_tokens, doc_tokens, doc_freqs, total_docs, avg_doc_len, k1, b):
    """Standard BM25 score for a single (query, doc) pair.

    Iterates over unique query terms. Returns 0.0 on edge cases.
    """
    if total_docs == 0 or avg_doc_len == 0:
        return 0.0

    doc_len = len(doc_tokens)
    tf_map = {}
    for t in doc_tokens:
        tf_map[t] = tf_map.get(t, 0) + 1

    score = 0.0
    for term in set(query_tokens):
        tf = tf_map.get(term, 0)
        if tf == 0:
            continue
        df = doc_freqs.get(term, 0)
        idf = math.log((total_docs - df + 0.5) / (df + 0.5) + 1)
        norm = tf * (k1 + 1) / (tf + k1 * (1 - b + b * doc_len / avg_doc_len))
        score += idf * norm

    return score


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

    Uses three-channel fusion: tiered relevance (score_entry), BM25 lexical,
    and RRF (Reciprocal Rank Fusion) to combine rankings.

    ctx keys used: score_threshold, escalation_threshold, max_warnings,
                   max_patterns, max_step, domain_mappings,
                   bm25_k1, bm25_b, rrf_k, stop_words,
                   + all keys used by score_entry and is_in_retention
    """
    _start = time.perf_counter()
    entries = read_learnings(paths)

    stop_words = ctx.get("stop_words", set())
    k1 = ctx.get("bm25_k1", 1.5)
    b = ctx.get("bm25_b", 0.75)
    rrf_k = ctx.get("rrf_k", 60)

    # --- Phase 1: Filter + tiered scoring (dual gate) ---
    candidates = []        # list of (tiered_score, entry)
    escalations = []
    retained = []          # all retained entries (for corpus stats)

    for entry in entries:
        if entry.get("resolved", False):
            continue

        if not is_in_retention(entry, current_step, ctx):
            continue

        retained.append(entry)
        score = score_entry(entry, current_step, task_components, task_files, task_domain, ctx)

        if entry["severity"] == "critical" or score >= ctx["score_threshold"]:
            candidates.append((score, entry))
            if entry["severity"] == "critical":
                step_diff = current_step - entry["step"]
                if step_diff >= ctx["escalation_threshold"]:
                    escalations.append(entry)

    # --- Phase 2: BM25 scoring ---
    doc_freqs, total_docs, avg_doc_len = _compute_corpus_stats(retained, stop_words)
    query_text = " ".join(task_components + ([task_domain] if task_domain else []) + task_files)
    query_tokens = _tokenize(query_text, stop_words)

    bm25_candidates = []   # list of (bm25_score, entry)
    for tiered_score, entry in candidates:
        entry_text = entry["trigger"] + " " + entry["action"] + " " + entry["reason"]
        doc_tokens = _tokenize(entry_text, stop_words)
        bm25 = bm25_score(query_tokens, doc_tokens, doc_freqs, total_docs, avg_doc_len, k1, b)
        bm25_candidates.append((bm25, entry))

    # --- Phase 3: RRF fusion ---
    # Rank by tiered score (desc), tie-break by timestamp (oldest first)
    tiered_ranked = sorted(candidates, key=lambda x: (-x[0], x[1].get("ts", "")))
    tiered_ranks = {}
    for rank, (score, entry) in enumerate(tiered_ranked, 1):
        tiered_ranks[id(entry)] = rank

    # Rank by BM25 score (desc), tie-break by timestamp (oldest first)
    bm25_ranked = sorted(bm25_candidates, key=lambda x: (-x[0], x[1].get("ts", "")))
    bm25_ranks = {}
    for rank, (score, entry) in enumerate(bm25_ranked, 1):
        bm25_ranks[id(entry)] = rank

    # Compute RRF scores and split by severity
    warnings = []   # (rrf_score, tiered_score, entry)
    patterns = []
    for tiered_score, entry in candidates:
        t_rank = tiered_ranks[id(entry)]
        b_rank = bm25_ranks[id(entry)]
        rrf = 1.0 / (rrf_k + t_rank) + 1.0 / (rrf_k + b_rank)
        if entry["severity"] == "critical":
            warnings.append((rrf, tiered_score, entry))
        else:
            patterns.append((rrf, tiered_score, entry))

    warnings.sort(key=lambda x: x[0], reverse=True)
    patterns.sort(key=lambda x: x[0], reverse=True)

    warnings = warnings[:ctx["max_warnings"]]
    patterns = patterns[:ctx["max_patterns"]]

    for _, _, entry in warnings + patterns:
        entry["access_count"] = entry.get("access_count", 0) + 1

    if warnings or patterns:
        write_learnings(paths, entries)

    print("## ⚠ WARNINGS — Read before starting")
    if warnings:
        for _, _, entry in warnings:
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
        for _, _, entry in patterns:
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
    sleep_cycle_due = unresolved_count > 50 or current_step in sleep_cycle_steps
    if sleep_cycle_due:
        if unresolved_count > 50:
            print(f"\n## SLEEP CYCLE DUE — {unresolved_count} unresolved entries exceed threshold of 50")
        if current_step in sleep_cycle_steps:
            print(f"\n## SLEEP CYCLE DUE — Sprint boundary at step {current_step} ({unresolved_count} unresolved entries)")
        print("Run the Sleep Cycle per AGENTS.md ## Memory before starting new work.")

    all_results = warnings + patterns
    top_score = all_results[0][1] if all_results else 0.0
    mean_score = sum(s for _, s, _ in all_results) / len(all_results) if all_results else 0.0
    top_rrf_score = all_results[0][0] if all_results else 0.0
    top_bm25 = max((bs for bs, _ in bm25_candidates), default=0.0)

    log_event(paths, "retrieval",
        query_step=current_step,
        query_components=task_components,
        query_files=task_files,
        query_domain=task_domain,
        total_entries=len(entries),
        unresolved_entries=unresolved_count,
        warnings_returned=len(warnings),
        patterns_returned=len(patterns),
        escalations_returned=len(escalations),
        top_score=round(top_score, 4),
        mean_score=round(mean_score, 4),
        top_bm25_score=round(top_bm25, 4),
        top_rrf_score=round(top_rrf_score, 6),
        profile_context_count=len(profile_context) if profile_context else 0,
        sleep_cycle_due=sleep_cycle_due,
        latency_ms=round((time.perf_counter() - _start) * 1000, 2),
    )

    return 0
