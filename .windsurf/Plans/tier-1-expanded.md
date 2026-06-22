# Tier 1 — Free Tier Quality Foundation (v1.17 – v1.19)

Improve the core product before any distribution work. No deps, high impact.

---

## 1.1 BM25 Lexical Retrieval (v1.17.0)

**Problem**: Jaccard similarity on whitespace tokens ignores term frequency and document length.

- Implement BM25 scoring on trigger+action+reason text (~20 lines, stdlib-only)
- Three-channel fusion: BM25 + current tiered relevance + Jaccard dedup, combined via Reciprocal Rank Fusion (RRF, k=60)
- No model download required — ships immediately as a pure quality improvement

**Files**: `retrieval.py`, `constants.py`, `config.json`

---

## 1.2 Embedding-Based Retrieval (v1.18.0)

**Problem**: Lexical matching misses semantic matches ("collision" vs "overlap").

- `sentence-transformers` `all-MiniLM-L6-v2` (~90MB), lazy download to `~/.agent-memory/models/`
- Generate embedding for `trigger + action + reason` at `--log` time. Store as base64 float16 array on entry
- Hybrid scoring: `final = alpha * bm25_score + (1-alpha) * embedding_cosine`, alpha configurable per project (default 0.5)
- Graceful degradation: if model unavailable, skip embedding scores entirely (alpha auto-set to 1.0, falls back to Jaccard — zero dependency by default)
- Schema versioning prerequisite: add `schema_version: 1` field + migration runner before shipping this (adds embedding field)

**Files**: `retrieval.py`, `validation.py`, `io.py`, `constants.py`, new `src/engine/migrate.py`, `config.json`

---

## 1.3 Embedding-Based Dedup (v1.19.0)

**Problem**: "Same lesson, different wording" creates duplicate entries.

- At `--log` time, cosine vs recent same-domain entries (threshold 0.85) before existing Jaccard check
- On match: merge — combine `access_count`, keep richer description, append dup's source agent to `contributors` list
- Quarantine rejected entry with reason `semantic_duplicate`
- Requires provenance fields: add `project_id`, `origin_project`, `contributing_projects` to schema

**Files**: `handlers.py`, `validation.py`, `io.py`, `migrate.py`

---

## 1.4 Reranking Pass (v1.19.1)

**Problem**: First-stage retrieval returns right candidates in wrong order.

- After initial scoring, optional second-pass reranker on top-N (default 20)
- Configurable: `reranker: "none" | "cross-encoder" | "llm-local"`
- Cross-encoder: `ms-marco-MiniLM-L-12-v2` (local, no API)
- LLM-local: uses local model (Ollama/LM Studio) if available, prompt: "Rate relevance 0-10"
- Only enabled when model is present; zero overhead otherwise

**Files**: `retrieval.py`, `constants.py`, `config.json`

---

## 1.5 Grading Harness (v1.19.2)

**Problem**: No way to measure retrieval quality objectively.

- Per-project fixture: `memory/eval/grading.jsonl` — list of `(task_context, expected_entry_ts)` pairs
- `--eval` command: runs retrieval against each fixture, reports top-1 / top-3 hit rate
- Ship with a default fixture per project type (game, web, data)
- This becomes the real "retrieval relevance" metric — replaces guessed numbers

**Files**: new `src/engine/eval.py`, `filter.py`
