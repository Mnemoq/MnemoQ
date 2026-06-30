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

import dataclasses
import json
import math
import os
import re
import sys
from types import SimpleNamespace

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


# --- A/B config comparison ---

def _swap_config_path(paths, config_path):
    """Return a paths-like object identical to `paths` but with config_path swapped.

    Works whether `paths` is the frozen Paths dataclass (production) or the
    namedtuple used in tests; load_config only reads `.config_path`.
    """
    if dataclasses.is_dataclass(paths):
        fields = dataclasses.asdict(paths)
    elif hasattr(paths, "_asdict"):
        fields = dict(paths._asdict())
    else:
        fields = dict(getattr(paths, "__dict__", {}))
    fields["config_path"] = str(config_path)
    return SimpleNamespace(**fields)


def _ctx_from_config(paths, config_path):
    """Build a ctx from DEFAULTS overlaid with a config file, via the real loader.

    Reuses ``cli.load_config()`` (whitelist + tuning block + validation) by
    temporarily pointing the cli ``PATHS`` singleton at ``config_path``, so an
    A/B config grades exactly as it would in a real run. Propagates
    TypeError/ValueError if the config is invalid.
    """
    import mnemoq.cli as cli
    from mnemoq.engine.constants import DEFAULTS

    ctx = {k.lower(): v for k, v in DEFAULTS.items()}
    old = cli.PATHS
    try:
        cli.PATHS = _swap_config_path(paths, config_path)
        raw = cli.load_config()
    finally:
        cli.PATHS = old
    ctx.update({k.lower(): v for k, v in raw.items()})
    return ctx


_SUMMARY_KEYS = ("fixtures", "top1_hits", "top3_hits", "top1_rate",
                 "top3_rate", "mrr", "ndcg", "match_mode")
_DELTA_KEYS = ("top1_rate", "top3_rate", "mrr", "ndcg")


def _summary(metrics):
    """Project the headline metrics (drop the per-fixture detail) for JSON output."""
    return {k: metrics[k] for k in _SUMMARY_KEYS}


def _compare_configs(paths, fixtures, match, cfg_a, cfg_b):
    """Grade `fixtures` (same corpus) under two config files. Returns (metrics_a, metrics_b)."""
    ctx_a = _ctx_from_config(paths, cfg_a)
    ctx_b = _ctx_from_config(paths, cfg_b)
    mode_a, _ = _resolve_mode(match, ctx_a)
    mode_b, _ = _resolve_mode(match, ctx_b)
    return (grade_fixtures(paths, ctx_a, fixtures, mode_a),
            grade_fixtures(paths, ctx_b, fixtures, mode_b))


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


def _print_compare(cfg_a, metrics_a, cfg_b, metrics_b):
    """Print a side-by-side A/B grading comparison with B-A deltas."""
    print("## Grading Harness A/B Comparison")
    print()
    print(f"A: {cfg_a}  (match: {metrics_a['match_mode']})")
    print(f"B: {cfg_b}  (match: {metrics_b['match_mode']})")
    print(f"Fixtures: {metrics_a['fixtures']}")
    print()
    print(f"{'Metric':<16}{'A':>10}{'B':>10}{'delta(B-A)':>14}")
    print("-" * 50)
    for label, key in [("Top-1 hit rate", "top1_rate"), ("Top-3 hit rate", "top3_rate")]:
        a, b = metrics_a[key], metrics_b[key]
        print(f"{label:<16}{a * 100:>9.1f}%{b * 100:>9.1f}%{(b - a) * 100:>+13.1f}%")
    for label, key in [("MRR", "mrr"), ("nDCG", "ndcg")]:
        a, b = metrics_a[key], metrics_b[key]
        print(f"{label:<16}{a:>10.3f}{b:>10.3f}{b - a:>+14.3f}")


def run_eval(paths, ctx, match="exact", as_json=False, compare=None):
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

    if compare:
        cfg_a, cfg_b = compare
        try:
            metrics_a, metrics_b = _compare_configs(paths, fixtures, match, cfg_a, cfg_b)
        except (TypeError, ValueError) as e:
            print(f"ERROR: invalid config in --compare: {e}", file=sys.stderr)
            return 1
        if as_json:
            print(json.dumps({
                "a": {"config": str(cfg_a), **_summary(metrics_a)},
                "b": {"config": str(cfg_b), **_summary(metrics_b)},
                "delta": {k: metrics_b[k] - metrics_a[k] for k in _DELTA_KEYS},
            }, indent=2))
        else:
            _print_compare(cfg_a, metrics_a, cfg_b, metrics_b)
        return 0

    mode, mode_note = _resolve_mode(match, ctx)
    metrics = grade_fixtures(paths, ctx, fixtures, mode)

    if as_json:
        print(json.dumps(metrics, indent=2))
    else:
        _print_report(metrics, mode_note)

    return 0
