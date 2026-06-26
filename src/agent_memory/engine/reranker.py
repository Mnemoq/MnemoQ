"""Optional second-pass reranker for the memory engine.

Three backends: none (passthrough), cross-encoder (sentence-transformers),
llm-local (Ollama / LM Studio via HTTP). Dispatch via ctx["reranker"].

Zero overhead when "none" (default). Graceful fallback to passthrough
on any backend failure.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

from agent_memory.engine.constants import EMBEDDING_CACHE_DIR, RERANKER_MODEL

# --- Cross-encoder singleton cache (keyed by model name) ---

_ce_cache: dict[str, object | None] = {}


def _get_cross_encoder(model_name, cache_dir=EMBEDDING_CACHE_DIR):
    """Lazy singleton for cross-encoder model, keyed by model_name.

    Returns model instance or None (cached per model_name so failed loads
    don't retry every call).
    """
    if model_name in _ce_cache:
        return _ce_cache[model_name]

    try:
        from sentence_transformers import CrossEncoder
        expanded_cache = os.path.expanduser(cache_dir)
        model = CrossEncoder(model_name, cache_folder=expanded_cache)
        _ce_cache[model_name] = model
        return model
    except Exception:
        _ce_cache[model_name] = None
        return None


# --- LLM-local endpoint probe ---

_PROBE_CACHE = {"endpoint": None, "checked": False}

_PROBE_TARGETS = [
    ("http://localhost:11434", "/api/tags"),    # Ollama
    ("http://localhost:1234", "/v1/models"),    # LM Studio (OpenAI-compatible)
]


def _probe_llm_endpoint(configured=None):
    """Probe for a local LLM server. Returns base URL or None.

    If configured is provided, returns it directly (no probe).
    Otherwise tries Ollama then LM Studio, caches result.
    """
    if configured:
        return configured
    if _PROBE_CACHE["checked"]:
        return _PROBE_CACHE["endpoint"]
    _PROBE_CACHE["checked"] = True
    for base, path in _PROBE_TARGETS:
        try:
            req = urllib.request.Request(base + path, method="GET")
            urllib.request.urlopen(req, timeout=1)
            _PROBE_CACHE["endpoint"] = base
            return base
        except Exception:
            continue
    return None


# --- Score parsing ---

_SCORE_RE = re.compile(r'\d+(?:\.\d+)?')


def _parse_scores(response_text, expected_count):
    """Extract numeric scores from LLM response text.

    Returns list of floats, or None if fewer than expected_count numbers found.
    """
    matches = _SCORE_RE.findall(response_text)
    if len(matches) < expected_count:
        return None
    return [float(m) for m in matches[:expected_count]]


# --- Backend implementations ---

def rerank_none(query, candidates, ctx):
    """Passthrough — return candidates unchanged.

    Returns (candidates, True) to signal "active" (no-op but not a failure).
    """
    return candidates, True


def rerank_cross_encoder(query, candidates, ctx):
    """Rerank using a cross-encoder model.

    Returns (reranked_candidates, True) on success,
    (original_candidates, False) on failure.
    """
    if not candidates:
        return candidates, True

    model_name = ctx.get("reranker_model", RERANKER_MODEL)
    cache_dir = ctx.get("embedding_cache_dir", EMBEDDING_CACHE_DIR)
    model = _get_cross_encoder(model_name, cache_dir)

    if model is None:
        print(f"WARNING: Cross-encoder model '{model_name}' unavailable, skipping rerank.", file=sys.stderr)
        return candidates, False

    # Build (query, entry_text) pairs for cross-encoder scoring
    pairs = []
    for _, _, _, entry in candidates:
        entry_text = entry["trigger"] + " " + entry["action"] + " " + entry["reason"]
        pairs.append((query, entry_text))

    try:
        scores = model.predict(pairs)
    except Exception as e:
        print(f"WARNING: Cross-encoder scoring failed: {e}, skipping rerank.", file=sys.stderr)
        return candidates, False

    # Flatten in case model returns 2D column vector
    scores = scores.flatten() if hasattr(scores, "flatten") else scores

    # Sort by cross-encoder score (desc), preserve original order for ties
    scored = list(zip(scores, candidates))
    scored.sort(key=lambda x: (-x[0],))
    reranked = [c for _, c in scored]
    return reranked, True


def rerank_llm_local(query, candidates, ctx):
    """Rerank using a local LLM via HTTP (Ollama or LM Studio).

    Batches all candidates into a single prompt. Falls back to passthrough
    if the LLM is unreachable or returns malformed output.

    Returns (reranked_candidates, True) on success,
    (original_candidates, False) on failure.
    """
    if not candidates:
        return candidates, True

    endpoint = _probe_llm_endpoint(ctx.get("reranker_llm_endpoint"))
    if endpoint is None:
        print("WARNING: No local LLM found (tried Ollama :11434, LM Studio :1234), skipping rerank.", file=sys.stderr)
        return candidates, False

    llm_model = ctx.get("reranker_llm_model")

    # Build batch prompt
    lines = ["Rate the relevance of each learning to the query on a scale of 0 to 10."]
    lines.append(f"Query: {query}")
    lines.append("")
    for i, (_, _, _, entry) in enumerate(candidates):
        entry_text = entry["trigger"] + " " + entry["action"] + " " + entry["reason"]
        lines.append(f"[{i}] Learning: {entry_text}")
    lines.append("")
    lines.append(f"Respond with {len(candidates)} numbers separated by spaces, one per learning, in order.")
    prompt = "\n".join(lines)

    try:
        response_text = _call_llm(endpoint, llm_model, prompt)
    except Exception as e:
        print(f"WARNING: LLM reranker call failed: {e}, skipping rerank.", file=sys.stderr)
        return candidates, False

    scores = _parse_scores(response_text, len(candidates))
    if scores is None:
        print(f"WARNING: LLM returned malformed response (expected {len(candidates)} scores), skipping rerank.", file=sys.stderr)
        return candidates, False

    # Sort by LLM score (desc), preserve original order for ties
    scored = list(zip(scores, candidates))
    scored.sort(key=lambda x: (-x[0],))
    reranked = [c for _, c in scored]
    return reranked, True


def _call_llm(endpoint, model, prompt):
    """Call local LLM and return raw response text.

    Detects Ollama vs LM Studio by endpoint port/path.
    """
    if ":11434" in endpoint:
        # Ollama: /api/generate
        payload = json.dumps({"model": model or "", "prompt": prompt, "stream": False}).encode()
        req = urllib.request.Request(
            endpoint + "/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            return data.get("response", "")
    else:
        # LM Studio: /v1/chat/completions (OpenAI-compatible)
        payload = json.dumps({
            "model": model or "",
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            endpoint + "/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"]


# --- Dispatch ---

def rerank(query, candidates, ctx):
    """Dispatch reranking based on ctx["reranker"].

    Returns (reranked_candidates, active: bool).
    Falls back to passthrough on any backend failure.
    """
    mode = ctx.get("reranker", "none")

    if mode == "none":
        return rerank_none(query, candidates, ctx)
    elif mode == "cross-encoder":
        return rerank_cross_encoder(query, candidates, ctx)
    elif mode == "llm-local":
        return rerank_llm_local(query, candidates, ctx)
    else:
        print(f"WARNING: Unknown reranker mode '{mode}', skipping.", file=sys.stderr)
        return candidates, False
