"""Retrieval scoring and filtering for the memory engine.

Extracted from filter.py (Phase 3). All configurable values come from
the ctx dict; file I/O uses the paths object.
"""

from __future__ import annotations

import base64
import math
import os
import re
import time

from agent_memory.engine.io import read_learnings, write_learnings
from agent_memory.engine.metrics import log_event
from agent_memory.engine.profile import load_profile, get_profile_context
from agent_memory.engine.constants import EMBEDDING_MODEL, EMBEDDING_CACHE_DIR


_TOKEN_SPLIT = re.compile(r'[^a-z0-9]+')

# --- Embedding functions ---

_embedder_cache: dict[str, object | None] = {}


def _get_embedder(model_name=EMBEDDING_MODEL, cache_dir=EMBEDDING_CACHE_DIR):
    """Lazy singleton for sentence-transformers model, keyed by model_name.

    Returns model instance or None (cached per model_name so failed loads
    don't retry every call). If model_name changes between calls, re-initializes.
    """
    if model_name in _embedder_cache:
        return _embedder_cache[model_name]

    try:
        from sentence_transformers import SentenceTransformer
        expanded_cache = os.path.expanduser(cache_dir)
        model = SentenceTransformer(model_name, cache_folder=expanded_cache)
        _embedder_cache[model_name] = model
        return model
    except Exception:
        _embedder_cache[model_name] = None
        return None


def compute_embedding(text, model_name=EMBEDDING_MODEL, cache_dir=EMBEDDING_CACHE_DIR):
    """Compute embedding vector for text. Returns list[float] or None if model unavailable."""
    model = _get_embedder(model_name, cache_dir)
    if model is None:
        return None
    try:
        vec = model.encode(text, convert_to_numpy=True)
        return vec.tolist()
    except Exception:
        return None


def cosine_similarity(vec_a, vec_b):
    """Stdlib cosine similarity. No numpy required for small vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    mag_a = math.sqrt(sum(a * a for a in vec_a))
    mag_b = math.sqrt(sum(b * b for b in vec_b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def embed_entry(entry, model_name=EMBEDDING_MODEL, cache_dir=EMBEDDING_CACHE_DIR):
    """Embed entry trigger + action + reason. Returns list[float] or None."""
    text = entry["trigger"] + " " + entry["action"] + " " + entry["reason"]
    return compute_embedding(text, model_name, cache_dir)


def encode_embedding(vec):
    """Encode embedding vector to base64 float16 string for compact JSONL storage.

    Falls back to plain list if numpy unavailable. Returns None if vec is None.
    """
    if vec is None:
        return None
    try:
        import numpy as np
        return base64.b64encode(np.array(vec, dtype=np.float16).tobytes()).decode()
    except ImportError:
        return vec  # plain list — larger but works


def decode_embedding(stored):
    """Decode embedding from base64 float16 string, plain list, or None.

    Returns list[float] or None.
    """
    if stored is None:
        return None
    if isinstance(stored, list):
        return stored
    if isinstance(stored, str):
        try:
            import numpy as np
            arr = np.frombuffer(base64.b64decode(stored), dtype=np.float16)
            return arr.astype(np.float32).tolist()
        except ImportError:
            return None  # can't decode base64 float16 without numpy
        except Exception:
            return None
    return None


def find_semantic_duplicate(entry, existing_entries, ctx):
    """Find the highest cosine similarity match among same-domain unresolved entries.

    Returns (best_cosine, best_match_entry) or (0.0, None) if no match above threshold.
    Skips entries without embeddings. Falls back to None if embedding model unavailable.
    """
    threshold = ctx.get("semantic_dedup_threshold", 0.85)
    entry_domain = entry.get("domain")
    entry_vec = decode_embedding(entry.get("embedding"))

    if entry_vec is None:
        _emb_model = ctx.get("embedding_model")
        _emb_cache = ctx.get("embedding_cache_dir")
        entry_vec = embed_entry(entry, _emb_model, _emb_cache)

    if entry_vec is None:
        return 0.0, None

    best_cosine = 0.0
    best_match = None

    for existing in existing_entries:
        if existing.get("resolved", False):
            continue
        if existing.get("domain") != entry_domain:
            continue

        existing_vec = decode_embedding(existing.get("embedding"))
        if existing_vec is None:
            _emb_model = ctx.get("embedding_model")
            _emb_cache = ctx.get("embedding_cache_dir")
            existing_vec = embed_entry(existing, _emb_model, _emb_cache)
            if existing_vec is not None:
                existing["embedding"] = encode_embedding(existing_vec)

        if existing_vec is None:
            continue

        cos = cosine_similarity(entry_vec, existing_vec)
        if cos > best_cosine:
            best_cosine = cos
            best_match = existing

    if best_cosine >= threshold:
        return best_cosine, best_match
    return 0.0, None


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


def retrieve_core(current_step, task_components, task_files, task_domain, ctx, paths):
    """Shared logic for retrieval mode. Returns dict, no printing.

    Returns:
        {
            "warnings": list[dict],      # scored warning entries
            "patterns": list[dict],      # scored pattern entries
            "escalations": list[dict],
            "total_entries": int,
            "unresolved_count": int,
            "sleep_cycle_due": bool,
            "profile_context": list[dict] | None,
            "scores": {...},             # top/mean scores for metrics
        }
    """
    _start = time.perf_counter()
    entries = read_learnings(paths)

    stop_words = ctx.get("stop_words", set())
    k1 = ctx.get("bm25_k1", 1.5)
    b = ctx.get("bm25_b", 0.75)
    rrf_k = ctx.get("rrf_k", 60)

    # --- Phase 1: Filter + tiered scoring (dual gate) ---
    candidates = []
    escalations = []
    retained = []

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

    bm25_candidates = []
    for tiered_score, entry in candidates:
        entry_text = entry["trigger"] + " " + entry["action"] + " " + entry["reason"]
        doc_tokens = _tokenize(entry_text, stop_words)
        bm25 = bm25_score(query_tokens, doc_tokens, doc_freqs, total_docs, avg_doc_len, k1, b)
        bm25_candidates.append((bm25, entry))

    # --- Phase 3: RRF fusion ---
    tiered_ranked = sorted(candidates, key=lambda x: (-x[0], x[1].get("ts", "")))
    tiered_ranks = {id(e): r for r, (_, e) in enumerate(tiered_ranked, 1)}

    bm25_ranked = sorted(bm25_candidates, key=lambda x: (-x[0], x[1].get("ts", "")))
    bm25_ranks = {id(e): r for r, (_, e) in enumerate(bm25_ranked, 1)}

    warnings = []
    patterns = []
    for tiered_score, entry in candidates:
        t_rank = tiered_ranks[id(entry)]
        b_rank = bm25_ranks[id(entry)]
        rrf = 1.0 / (rrf_k + t_rank) + 1.0 / (rrf_k + b_rank)
        if entry["severity"] == "critical":
            warnings.append((rrf, tiered_score, entry))
        else:
            patterns.append((rrf, tiered_score, entry))

    # --- Phase 4: Embedding channel (hybrid scoring) ---
    embedding_alpha = ctx.get("embedding_alpha", 0.5)
    embedding_model = ctx.get("embedding_model", EMBEDDING_MODEL)
    embedding_cache_dir = ctx.get("embedding_cache_dir", EMBEDDING_CACHE_DIR)
    embedder = _get_embedder(embedding_model, embedding_cache_dir)
    embedding_channel_active = embedder is not None
    top_embedding_cosine = 0.0

    query_embed = None
    if embedding_channel_active:
        query_embed = compute_embedding(query_text, embedding_model, embedding_cache_dir)
        if query_embed is None:
            embedding_channel_active = False

    if embedding_channel_active and query_embed is not None:
        alpha = embedding_alpha
        for rrf, tiered_score, entry in warnings + patterns:
            vec = decode_embedding(entry.get("embedding"))
            if vec is None:
                vec = embed_entry(entry, embedding_model, embedding_cache_dir)
                if vec is not None:
                    entry["embedding"] = encode_embedding(vec)
            cos = cosine_similarity(query_embed, vec) if vec else 0.0
            entry["_cosine"] = cos
            if cos > top_embedding_cosine:
                top_embedding_cosine = cos

        all_results = warnings + patterns
        max_rrf = max((r for r, _, _ in all_results), default=0.0)
        fused = []
        for rrf, tiered_score, entry in all_results:
            cos = entry.pop("_cosine", 0.0)
            if max_rrf > 0:
                final = alpha * (rrf / max_rrf) + (1 - alpha) * cos
            else:
                final = (1 - alpha) * cos
            fused.append((final, rrf, tiered_score, entry))

        warnings = [(f, r, t, e) for f, r, t, e in fused if e["severity"] == "critical"]
        patterns = [(f, r, t, e) for f, r, t, e in fused if e["severity"] != "critical"]
        warnings.sort(key=lambda x: x[0], reverse=True)
        patterns.sort(key=lambda x: x[0], reverse=True)
    else:
        alpha = 1.0
        warnings = [(r, r, t, e) for r, t, e in warnings]
        patterns = [(r, r, t, e) for r, t, e in patterns]
        warnings.sort(key=lambda x: x[0], reverse=True)
        patterns.sort(key=lambda x: x[0], reverse=True)

    # --- Phase 5: Optional reranking ---
    reranker_mode = ctx.get("reranker", "none")
    reranker_top_n = ctx.get("reranker_top_n", 20)
    reranker_active = False
    reranker_latency_ms = 0.0

    if reranker_mode != "none":
        from agent_memory.engine.reranker import rerank as _rerank
        combined = warnings + patterns
        if len(combined) >= 3:
            top_n = combined[:reranker_top_n]
            rest = combined[reranker_top_n:]
            _rr_start = time.perf_counter()
            reranked_top, reranker_active = _rerank(query_text, top_n, ctx)
            reranker_latency_ms = round((time.perf_counter() - _rr_start) * 1000, 2)
            combined = reranked_top + rest
        else:
            reranker_active = False
            reranker_latency_ms = 0.0
        warnings = [t for t in combined if t[3]["severity"] == "critical"]
        patterns = [t for t in combined if t[3]["severity"] != "critical"]

    warnings = warnings[:ctx["max_warnings"]]
    patterns = patterns[:ctx["max_patterns"]]

    for _, _, _, entry in warnings + patterns:
        entry["access_count"] = entry.get("access_count", 0) + 1

    if warnings or patterns:
        write_learnings(paths, entries)

    profile = load_profile()
    profile_context = get_profile_context(profile, task_domain, domain_mappings=ctx.get("domain_mappings"))

    unresolved_count = sum(1 for e in entries if not e.get("resolved", False))
    max_step = ctx.get("max_step")
    sleep_cycle_steps = {10, 20, max_step} if max_step is not None else {10, 20}
    sleep_cycle_due = unresolved_count > 50 or current_step in sleep_cycle_steps
    sleep_cycle_reasons = []
    if unresolved_count > 50:
        sleep_cycle_reasons.append("threshold")
    if current_step in sleep_cycle_steps:
        sleep_cycle_reasons.append("sprint_boundary")

    all_results = warnings + patterns
    top_score = all_results[0][2] if all_results else 0.0
    mean_score = sum(s for _, _, s, _ in all_results) / len(all_results) if all_results else 0.0
    top_rrf_score = all_results[0][1] if all_results else 0.0
    top_final_score = all_results[0][0] if all_results else 0.0
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
        top_final_score=round(top_final_score, 6),
        embedding_channel_active=embedding_channel_active,
        embedding_alpha_used=alpha,
        top_embedding_cosine=round(top_embedding_cosine, 6),
        reranker_mode=reranker_mode,
        reranker_active=reranker_active,
        reranker_top_n=reranker_top_n,
        reranker_latency_ms=reranker_latency_ms,
        profile_context_count=len(profile_context) if profile_context else 0,
        sleep_cycle_due=sleep_cycle_due,
        latency_ms=round((time.perf_counter() - _start) * 1000, 2),
    )

    # Strip tuple wrapper, return plain entries with scores
    def _unwrap(results):
        out = []
        for final, rrf, tiered, entry in results:
            out.append({**entry, "_final_score": round(final, 6), "_rrf_score": round(rrf, 6), "_tiered_score": round(tiered, 4)})
        return out

    return {
        "warnings": _unwrap(warnings),
        "patterns": _unwrap(patterns),
        "escalations": escalations,
        "total_entries": len(entries),
        "unresolved_count": unresolved_count,
        "sleep_cycle_due": sleep_cycle_due,
        "sleep_cycle_reasons": sleep_cycle_reasons,
        "profile_context": profile_context,
        "scores": {
            "top_score": round(top_score, 4),
            "mean_score": round(mean_score, 4),
            "top_bm25_score": round(top_bm25, 4),
            "top_rrf_score": round(top_rrf_score, 6),
            "top_final_score": round(top_final_score, 6),
            "top_embedding_cosine": round(top_embedding_cosine, 6),
            "embedding_channel_active": embedding_channel_active,
            "reranker_active": reranker_active,
        },
    }


def handle_retrieval(current_step, task_components, task_files, task_domain, ctx, paths):
    """Handle retrieval mode: score, filter, and print relevant learnings. CLI wrapper."""
    result = retrieve_core(current_step, task_components, task_files, task_domain, ctx, paths)

    print("## ⚠ WARNINGS — Read before starting")
    if result["warnings"]:
        for entry in result["warnings"]:
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

    print("\n## 🎯 DEVELOPER PREFERENCES")
    if result["profile_context"]:
        for pref in result["profile_context"]:
            print(f"- [{pref['source']}] {pref['trigger']}: {pref['action']}")
            print(f"  Reason: {pref['reason']}")
    else:
        print("(none)")

    print("\n## RELEVANT PATTERNS")
    if result["patterns"]:
        for entry in result["patterns"]:
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

    if result["escalations"]:
        print("\n## ⚡ ESCALATION — Critical entries unresolved for 30+ steps")
        for entry in result["escalations"]:
            print(f"- [step-{entry['step']}, {entry['domain']}, {entry['source_agent']}] {entry['trigger']}: {entry['action']}")

    print(f"\n## STATS: {result['total_entries']} total entries, {result['unresolved_count']} unresolved")

    if result["sleep_cycle_due"]:
        reasons = result.get("sleep_cycle_reasons", [])
        if "threshold" in reasons:
            print(f"\n## SLEEP CYCLE DUE — {result['unresolved_count']} unresolved entries exceed threshold of 50")
        if "sprint_boundary" in reasons:
            print(f"\n## SLEEP CYCLE DUE — Sprint boundary at step {current_step} ({result['unresolved_count']} unresolved entries)")
        print("Run the Sleep Cycle per AGENTS.md ## Memory before starting new work.")

    return 0
