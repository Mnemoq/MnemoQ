# Phase 1 — Retrieval Quality (v1.17.0 → v1.19.2)

The memory engine started with a single-channel scoring system (tiered relevance based on step proximity, component overlap, and domain matching). Phase 1 transformed it into a multi-channel retrieval pipeline with objective quality measurement.

## 1.1 — BM25 Lexical Retrieval (v1.17.0)

Added a second scoring channel using BM25 (Best Matching 25) — the standard text-search ranking algorithm. This catches lexical matches that tiered scoring misses: if a learning's trigger text contains query keywords, BM25 surfaces it even if the component/domain tags don't overlap.

- Tokenizes trigger/action/reason text, builds corpus stats (doc frequencies, avg doc length)
- Tunable `bm25_k1` (term frequency saturation) and `bm25_b` (length normalization)
- Fused with tiered scores via **Reciprocal Rank Fusion (RRF)** — a rank-based merge that doesn't require score calibration

## 1.2 — Embedding-Based Retrieval (v1.18.0)

Added a third channel: semantic similarity via sentence embeddings. Catches matches where the wording differs but the meaning is the same (e.g., "AABB collision" matches "bounding box overlap").

- Uses `sentence-transformers` models (default: `all-MiniLM-L6-v2`), cached locally
- Embeddings computed on log and stored in `learnings.jsonl` (base64-encoded)
- **Hybrid fusion**: `final = alpha * normalized_rrf + (1 - alpha) * cosine_similarity`
- Tunable `embedding_alpha` (default 0.5), `embedding_model`, `embedding_cache_dir`
- Graceful fallback: if model not installed, pipeline runs without the embedding channel (zero overhead)

## 1.3 — Embedding-Based Dedup (v1.19.0)

Uses the same embeddings to prevent duplicate learnings. Before logging a new entry, computes cosine similarity against existing entries in the same domain. If similarity ≥ 0.85, the entry is rejected (or merged with provenance tracking).

- Prevents knowledge drift from near-duplicate entries
- Cross-references provenance fields (`duplicate_of_ts`, `dedup_method`)
- Falls back gracefully when no embedding model is available

## 1.4 — Reranking Pass (v1.19.1)

Added an optional **Phase 5** reranker that re-scores the top-N candidates (default 20) after initial retrieval. First-stage retrieval casts a wide net; the reranker applies a more expensive model to get the order right.

- Three modes via `config.json`:
  - **`none`** — passthrough, zero overhead (default)
  - **`cross-encoder`** — `sentence-transformers CrossEncoder` (default: `ms-marco-MiniLM-L-12-v2`), local, no API
  - **`llm-local`** — HTTP calls to a local LLM (Ollama/LM Studio) with a "rate relevance 0-10" prompt
- Auto-probe for Ollama (`localhost:11434/api/tags`) and LM Studio (`localhost:1234/v1/models`) during scaffold
- Min-candidate threshold (skips reranking if <5 candidates)
- Latency tracked in metrics (`reranker_latency_ms`)

## 1.5 — Grading Harness (v1.19.2)

The capstone: **objective measurement** of retrieval quality. Without this, tuning parameters (alpha, k1, thresholds) is guesswork.

- `memory/eval/grading.jsonl` — per-project fixtures of `(task_context, expected_trigger)` pairs
- `--eval` CLI flag runs retrieval for each fixture and checks if the expected trigger appears in the results
- Reports **top-1** and **top-3** hit rates with per-fixture breakdown
- Ships with a template fixture; scaffold creates `memory/eval/` on new projects
- Comment/blank-line support in fixture files

## Architecture Throughout

All five steps followed the same pattern:

- **`filter.py`** stays a thin dispatcher — all logic in `src/engine/` modules
- **`config.json`** holds per-project tuning; `templates/config.json` for new projects
- **`constants.py`** defines defaults; `DEFAULTS` dict for scaffold
- **Graceful degradation** — every optional channel (embeddings, reranker) has zero overhead when disabled
- **Metrics logging** — each phase contributes to the `log_event` call in retrieval, enabling `--metrics-retrieval` analysis
- **87 tests** covering all channels, fallbacks, config validation, and the grading harness

## Net Result

The engine went from a single-channel proximity scorer to a **five-phase retrieval pipeline** (tiered → BM25 → RRF fusion → embedding hybrid → optional reranking) with **objective quality measurement**. Each channel catches different failure modes, and the grading harness provides the feedback loop to tune them.

## Files Modified/Created

| File | Step | Purpose |
|------|------|---------|
| `src/engine/retrieval.py` | 1.1, 1.2, 1.4 | BM25 scoring, RRF fusion, embedding hybrid, reranker integration |
| `src/engine/constants.py` | 1.1–1.5 | Defaults for BM25, embeddings, reranker |
| `src/engine/reranker.py` | 1.4 | New: cross-encoder and LLM-local reranking logic |
| `src/engine/eval.py` | 1.5 | New: grading harness with fixture loading and hit-rate reporting |
| `src/filter.py` | 1.1–1.5 | Config loading, CLI flags (`--eval`), dispatch |
| `src/scaffold.py` | 1.4, 1.5 | Reranker mode picker, eval fixture directory creation |
| `src/engine_version.py` | All | Fallback version tracking |
| `VERSION` | All | 1.17.0 → 1.19.2 |
| `memory/config.json` | 1.1–1.4 | Project tuning (BM25, RRF, embeddings, reranker) |
| `templates/config.json` | 1.1–1.4 | Template for new projects |
| `templates/eval/grading.jsonl` | 1.5 | New: fixture template with field documentation |
| `tests/test_memory.py` | All | 87 tests covering all channels and edge cases |
