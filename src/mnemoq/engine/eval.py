# Copyright (C) 2026 Mnemoq
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Grading harness for retrieval quality evaluation.

Loads a fixture of (task_context, expected_trigger) pairs from
memory/eval/grading.jsonl, runs retrieval **in-process** for each, and
reports top-1 / top-3 hit rate plus MRR and nDCG.

Earlier versions shelled out to a fresh ``python -m mnemoq.cli`` subprocess
per fixture (reloading config and the embedding model every time) and matched
with a brittle substring test. This version calls ``retrieve_core`` directly —
the model loads once — and supports three matching modes:

    exact     substring of (trigger + action + reason), case-insensitive.
              This is the historical behaviour and the default, so existing
              fixtures grade identically.
    fuzzy     token-overlap: fraction of the expected trigger's content tokens
              present in the entry, compared against a threshold.
    semantic  cosine similarity of the expected trigger's embedding against the
              entry's embedding, compared against a threshold. Falls back to
              ``fuzzy`` per-comparison when no embedding model is available.
    auto      semantic when an embedding model is available, else fuzzy.

Fixture format (one JSON object per line):
    {"step": N, "components": "A,B", "files": "", "domain": "D",
     "expected_trigger": "text expected to be retrieved"}
"""

from __future__ import annotations

import json
import math
import os
import re
import sys

from mnemoq.engine.retrieval import (
    compute_embedding,
    cosine_similarity,
    decode_embedding,
    retrieve_core,
)

VALID_MATCH_MODES = ("exact", "fuzzy", "semantic", "auto")

DEFAULT_FUZZY_THRESHOLD = 0.6
DEFAULT_SEMANTIC_THRESHOLD = 0.7

_WORD_RE = re.compile(r"[^a-z0-9]+")


# --- Fixture loading ---

def load_fixture(fixture_path):
    """Load grading fixtures from a JSONL file.

    Returns list of dicts with keys: step, components, files, domain, expected_trigger.
    Skips blank lines and comments (#).
    """
    if not os.path.exists(fixture_path):
        return []

    fixtures = []
    with open(fixture_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                fixtures.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"WARNING: Skipping malformed fixture line: {e}", file=sys.stderr)
    return fixtures


# --- Matching ---

def _tokens(text, stop_words):
    """Lowercase, split on non-alphanumeric, drop stop words. Returns a set."""
    return {t for t in _WORD_RE.split(text.lower()) if t and t not in stop_words}


def _entry_text(entry):
    """Concatenate the searchable fields of an entry."""
    return f"{entry.get('trigger', '')} {entry.get('action', '')} {entry.get('reason', '')}"


def _match_exact(expected, entry):
    return expected.lower() in _entry_text(entry).lower()


def _match_fuzzy(expected, entry, stop_words, threshold):
    """Overlap coefficient: fraction of expected tokens present in the entry."""
    exp = _tokens(expected, stop_words)
    if not exp:
        return False
    cand = _tokens(_entry_text(entry), stop_words)
    return len(exp & cand) / len(exp) >= threshold


def _match_semantic(expected_vec, entry, model, cache, threshold):
    """Cosine of expected vs entry embedding. Returns bool, or None if unavailable."""
    if expected_vec is None:
        return None
    vec = decode_embedding(entry.get("embedding"))
    if vec is None:
        vec = compute_embedding(_entry_text(entry), model, cache)
    if vec is None:
        return None
    return cosine_similarity(expected_vec, vec) >= threshold


def _is_match(expected, entry, mode, ctx, expected_vec):
    """Decide whether `entry` satisfies `expected` under `mode`.

    `semantic` falls back to `fuzzy` for any comparison where embeddings are
    unavailable, so a partially-embedded corpus still grades sensibly.
    """
    if not expected:
        return False
    if mode == "exact":
        return _match_exact(expected, entry)

    stop_words = ctx.get("stop_words", set())
    fuzzy_threshold = ctx.get("eval_fuzzy_threshold", DEFAULT_FUZZY_THRESHOLD)

    if mode == "semantic":
        sem_threshold = ctx.get("eval_semantic_threshold", DEFAULT_SEMANTIC_THRESHOLD)
        model = ctx.get("embedding_model")
        cache = ctx.get("embedding_cache_dir")
        verdict = _match_semantic(expected_vec, entry, model, cache, sem_threshold)
        if verdict is not None:
            return verdict
        # embeddings unavailable for this comparison — fall back to fuzzy
    return _match_fuzzy(expected, entry, stop_words, fuzzy_threshold)


def _resolve_mode(mode, ctx):
    """Resolve `auto` to a concrete mode by probing model availability.

    Returns (resolved_mode, note). Non-auto modes pass through unchanged.
    """
    if mode != "auto":
        return mode, None
    probe = compute_embedding("probe", ctx.get("embedding_model"), ctx.get("embedding_cache_dir"))
    if probe is not None:
        return "semantic", "auto -> semantic (embedding model available)"
    return "fuzzy", "auto -> fuzzy (no embedding model)"


# --- Grading ---

def _aggregate(ranks):
    """Compute hit/MRR/nDCG aggregates from per-fixture ranks (0 = miss)."""
    total = len(ranks)
    top1 = sum(1 for r in ranks if r == 1)
    top3 = sum(1 for r in ranks if r and r <= 3)
    mrr = sum(1.0 / r for r in ranks if r) / total if total else 0.0
    # Binary single-relevant nDCG: ideal DCG is 1.0, so nDCG == 1/log2(rank+1).
    ndcg = sum(1.0 / math.log2(r + 1) for r in ranks if r) / total if total else 0.0
    return {
        "fixtures": total,
        "top1_hits": top1,
        "top3_hits": top3,
        "top1_rate": top1 / total if total else 0.0,
        "top3_rate": top3 / total if total else 0.0,
        "mrr": mrr,
        "ndcg": ndcg,
    }


def grade_fixtures(paths, ctx, fixtures, mode="exact"):
    """Run retrieval for each fixture in-process and grade it.

    Returns a metrics dict: the `_aggregate` fields plus `match_mode` and a
    `per_fixture` list of {n, status, rank, expected, returned, top3}.
    """
    ranks = []
    per_fixture = []

    for i, fx in enumerate(fixtures, 1):
        step = fx.get("step", 1)
        components = [c for c in fx.get("components", "").split(",") if c]
        files = [f for f in fx.get("files", "").split(",") if f]
        domain = fx.get("domain", "")
        expected = fx.get("expected_trigger", "")

        result = retrieve_core(step, components, files, domain, ctx, paths, no_profile=True)
        # Warnings (critical) rank ahead of patterns, mirroring retrieval output.
        ranked = result["warnings"] + result["patterns"]

        expected_vec = None
        if mode == "semantic" and expected:
            expected_vec = compute_embedding(
                expected, ctx.get("embedding_model"), ctx.get("embedding_cache_dir")
            )

        rank = 0
        for idx, entry in enumerate(ranked, 1):
            if _is_match(expected, entry, mode, ctx, expected_vec):
                rank = idx
                break

        ranks.append(rank)
        per_fixture.append({
            "n": i,
            "status": f"HIT@{rank}" if rank else "MISS",
            "rank": rank,
            "expected": expected,
            "returned": len(ranked),
            "top3": [
                {"trigger": e.get("trigger", ""), "score": e.get("_final_score", 0.0)}
                for e in ranked[:3]
            ],
        })

    metrics = _aggregate(ranks)
    metrics["match_mode"] = mode
    metrics["per_fixture"] = per_fixture
    return metrics


# --- Reporting ---

def _print_report(metrics, mode_note=None):
    """Print the human-readable grading report.

    Preserves the legacy `## Grading Harness Results`, `Fixtures:`,
    `Top-1 hit rate:` and `### Per-fixture breakdown` lines so existing CLI
    tests keep passing; MRR/nDCG/match-mode are additive.
    """
    total = metrics["fixtures"]
    print("## Grading Harness Results")
    print()
    print(f"Fixtures: {total}")
    mode_label = metrics["match_mode"] + (f"  ({mode_note})" if mode_note else "")
    print(f"Match mode: {mode_label}")
    print(f"Top-1 hit rate: {metrics['top1_hits']}/{total} ({metrics['top1_rate'] * 100:.1f}%)")
    print(f"Top-3 hit rate: {metrics['top3_hits']}/{total} ({metrics['top3_rate'] * 100:.1f}%)")
    print(f"MRR:  {metrics['mrr']:.3f}")
    print(f"nDCG: {metrics['ndcg']:.3f}")
    print()
    print("### Per-fixture breakdown")
    print(f"{'#':>3}  {'Result':<10}  Expected")
    print("-" * 80)
    for rec in metrics["per_fixture"]:
        print(f"{rec['n']:>3}  {rec['status']:<10}  {rec['expected'][:60]}")


def run_eval(paths, ctx, match="exact", as_json=False):
    """Run the grading harness.

    Reads memory/eval/grading.jsonl, grades each fixture in-process, and prints
    a report (text by default, JSON when as_json=True).

    Returns 0 on success, 1 if no fixtures found or the match mode is invalid.
    """
    if match not in VALID_MATCH_MODES:
        print(f"ERROR: --match must be one of {', '.join(VALID_MATCH_MODES)}", file=sys.stderr)
        return 1

    fixture_path = os.path.join(paths.memory_dir, "eval", "grading.jsonl")
    fixtures = load_fixture(fixture_path)

    if not fixtures:
        print("No grading fixtures found.")
        print(f"  Expected: {fixture_path}")
        print("  Create one with lines like:")
        print('    {"step": 5, "components": "Player,Collision", "files": "", '
              '"domain": "gameplay", "expected_trigger": "When AABB collision detected"}')
        return 1

    mode, mode_note = _resolve_mode(match, ctx)
    metrics = grade_fixtures(paths, ctx, fixtures, mode)

    if as_json:
        print(json.dumps(metrics, indent=2))
    else:
        _print_report(metrics, mode_note)

    return 0
