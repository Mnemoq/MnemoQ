# Config Tuning Guide

`config.json` provides project-specific tuning overlaid on engine defaults. Every parameter below has a sensible default in `src/agent_memory/engine/constants.py`; your `memory/config.json` overrides only what you need. See `templates/config.json` for the full template and `templates/config-presets/` for ready-made presets.

## Parameter Reference

### Top-Level Metadata (3 params)

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `project_name` | string | `"<PROJECT_NAME>"` | any string | Project identifier used in stats and reports |
| `engine_min_version` | string | `"1.15.0"` | semver | Minimum engine version required to load this config |
| `schema_version` | int | `1` | `1` | Config schema version (currently 1) |

### Access Control / Validation (4 params)

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `valid_domains` | string[] \| null | `null` | engine defaults | Whitelist of accepted domain values; `null` = use engine defaults |
| `valid_source_agents` | string[] \| null | `null` | engine defaults | Whitelist of accepted agent names; `null` = use engine defaults |
| `retrieval_only_agents` | string[] \| null | `null` | engine defaults | Agents that can retrieve but not log; `null` = use engine defaults |
| `domain_mappings` | object \| null | `null` | — | Maps custom domains to canonical tags for profile context; `null` = use profile defaults |

### API Security (1 param)

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `api_key` | string \| null | `null` | any string | API key for HTTP server authentication; `null` = no auth. Clients must send `X-API-Key` header |

### Retrieval Scoring (6 params, under `tuning`)

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `decay_rate` | float | `0.995` | (0.0, 1.0) excl | Exponential decay per step for recency factor; lower = faster forgetting |
| `score_threshold` | float | `0.15` | [0.0, 1.0] | Minimum tiered score for non-critical entries to become candidates |
| `component_weight` | float | `1.0` | ≥ 0.0 | Relevance multiplier when task components match |
| `file_weight` | float | `0.7` | ≥ 0.0 | Relevance multiplier when task files match (fallback from component) |
| `domain_weight` | float | `0.4` | ≥ 0.0 | Relevance multiplier when domain matches (fallback from file) |
| `no_match_weight` | float | `0.1` | ≥ 0.0 | Relevance multiplier when nothing matches |

### Result Limits (2 params, under `tuning`)

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `max_warnings` | int | `5` | ≥ 0 | Max critical-severity entries returned per retrieval |
| `max_patterns` | int | `15` | ≥ 0 | Max non-critical entries returned per retrieval |

### Retention & Escalation (3 params, under `tuning`)

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `minor_retention` | int | `5` | ≥ 0 | Step window for minor-severity entries before expiry (access_count > 3 extends) |
| `major_retention` | int | `20` | ≥ 0 | Step window for major-severity entries before expiry |
| `escalation_threshold` | int | `30` | ≥ 0 | Step age for critical entries to be flagged as escalations |

### BM25 / RRF Fusion (3 params, under `tuning`)

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `bm25_k1` | float | `1.5` | > 0.0 | Term frequency saturation in BM25 scoring |
| `bm25_b` | float | `0.75` | [0.0, 1.0] | Document length normalization in BM25 scoring |
| `rrf_k` | int | `60` | ≥ 1 | Reciprocal rank fusion constant; higher = smoother rank merging |

### Embedding Channel (4 params, top-level + tuning)

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `embedding_model` | string | `"all-MiniLM-L6-v2"` | model name | sentence-transformers model name for semantic search |
| `embedding_cache_dir` | string | `"~/.agent-memory/models/"` | path | Local cache path for model files |
| `embedding_alpha` | float | `0.5` | [0.0, 1.0] | Blend weight: `alpha * rrf + (1-alpha) * cosine` (under `tuning`) |
| `semantic_dedup_threshold` | float | `0.85` | [0.0, 1.0] | Cosine similarity above which new entries are flagged as duplicates (under `tuning`) |

### Reranking (5 params, top-level)

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `reranker` | string | `"none"` | `"none"`, `"cross-encoder"`, `"llm-local"` | Reranker mode |
| `reranker_top_n` | int | `20` | ≥ 1 | Number of top results to rerank |
| `reranker_model` | string | `"cross-encoder/ms-marco-MiniLM-L-12-v2"` | model name | Cross-encoder model name (for `cross-encoder` mode) |
| `reranker_llm_endpoint` | string \| null | `null` | URL | LLM endpoint URL (for `llm-local` mode); `null` = auto-probe Ollama/LM Studio |
| `reranker_llm_model` | string \| null | `null` | model name | LLM model name (for `llm-local` mode) |

### Sleep Cycle / Consolidation (3 params, under `tuning`)

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `sleep_cycle_days` | int | `1` | ≥ 0 | Days between automatic consolidation triggers (`0` disables) |
| `sleep_cycle_quarantine_threshold` | int | `20` | ≥ 0 | Quarantine entry count that triggers consolidation (`0` disables) |
| `sleep_cycle_unresolved_threshold` | int | `20` | ≥ 0 | Unresolved entry count that triggers consolidation (`0` disables) |

### Step Bound (1 param, top-level)

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `max_step` | int \| null | `null` | ≥ 0 | Cap on step values; `null` = no cap |

## Tuning Recipes

### Maximize Precision

Reduce false positives in retrieval results.

```json
{
  "tuning": {
    "score_threshold": 0.3,
    "embedding_alpha": 0.3,
    "max_patterns": 10
  },
  "reranker": "cross-encoder",
  "reranker_top_n": 20
}
```

- Higher `score_threshold` filters weak matches.
- Lower `embedding_alpha` weights lexical (BM25) signal more heavily.
- Enable cross-encoder reranking for precise reordering.

### Maximize Recall

Surface more potentially relevant learnings.

```json
{
  "tuning": {
    "score_threshold": 0.05,
    "minor_retention": 10,
    "major_retention": 40,
    "max_patterns": 30,
    "max_warnings": 10
  }
}
```

- Lower `score_threshold` lets more candidates through.
- Higher retention windows keep entries alive longer.
- Higher result limits return more entries per retrieval.

### Fast / Cheap Mode

Minimize compute overhead when retrieval speed matters more than precision.

```json
{
  "tuning": {
    "max_patterns": 5,
    "max_warnings": 2,
    "embedding_alpha": 1.0
  },
  "reranker": "none"
}
```

- Disable reranker entirely.
- Set `embedding_alpha` to `1.0` to skip cosine similarity (pure BM25 + RRF).
- Lower result limits reduce output size.

### Strict Domain Enforcement

Lock down which domains and agents are accepted.

```json
{
  "valid_domains": ["backend", "api", "database", "security"],
  "valid_source_agents": ["gm", "code-reviewer", "test-writer"],
  "retrieval_only_agents": ["basic-reviewer"],
  "domain_mappings": {
    "api": ["backend", "rest"],
    "database": ["backend", "sql"]
  }
}
```

- `valid_domains` rejects entries with unlisted domains.
- `valid_source_agents` rejects entries from unlisted agents.
- `domain_mappings` adds canonical tag context for profile-based retrieval.

## Preset Reference

Ready-made config presets live in `templates/config-presets/`:

- **`generic.json`** — Minimal config with engine defaults, no embeddings or reranking. Good starting point for new projects.

Copy a preset to your project's `memory/config.json` and customize as needed.

## Auto-Learning Parameters (11 params, under `tuning`)

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `auto_learn_enabled` | bool | `true` | bool | Master toggle for auto-learning (opt-out) |
| `auto_learn_git_scan_depth` | int | `20` | >= 1 | Number of recent git commits to scan |
| `auto_learn_fix_commit_threshold` | int | `3` | >= 1 | Min fix commits for a file to trigger repeated-fix detection |
| `auto_learn_under_retrieved_access` | int | `2` | >= 0 | Max access_count for under-retrieved detection |
| `auto_learn_under_retrieved_reinforcement` | int | `5` | >= 0 | Min reinforcement_count for under-retrieved detection |
| `auto_learn_over_injected_access` | int | `10` | >= 0 | Min access_count for over-injection detection |
| `auto_learn_over_injected_reinforcement` | int | `2` | >= 0 | Max reinforcement_count for over-injection detection |
| `auto_learn_staleness_threshold` | int | `500` | >= 1 | Lines changed for staleness (also used by `check_staleness`) |
| `auto_learn_max_files_per_commit` | int | `5` | >= 1 | Skip commits touching more than this many files |
| `auto_learn_max_per_run` | int | `20` | >= 1 | Cap on generated entries per run |
| `auto_learn_retrieval_failure_cap` | int | `100` | >= 1 | Max retrieval events scanned when `since_ts` is `None` |

## Per-Prompt Evaluation Parameters (3 params, under `tuning`)

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `evaluate_enabled` | bool | `true` | bool | Master toggle for per-prompt evaluation (opt-out) |
| `evaluate_auto_log_threshold` | float | `0.9` | [0.0, 1.0] | Confidence threshold above which detected signals are auto-logged via `log_core` |
| `evaluate_max_per_turn` | int | `3` | >= 1 | Cap on signals processed per prompt evaluation (highest-confidence first) |

## Conversation Capture Parameters (7 params, top-level + tuning)

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `capture_mode` | string | `"heuristic"` | `"online"`, `"offline"`, `"heuristic"` | Which extraction tier to try first |
| `capture_llm_endpoint` | string \| null | `null` | URL | Override for offline LLM probe (skip auto-probe); `null` = auto-probe Ollama/LM Studio |
| `capture_llm_model` | string \| null | `null` | model name | LLM model name for offline extraction (for `offline` mode) |
| `capture_online_endpoint` | string \| null | `null` | URL | OpenAI-compatible API base URL (for `online` mode) |
| `capture_online_model` | string \| null | `null` | model name | Online model name (e.g. `"gpt-4o-mini"`) |
| `capture_online_api_key` | string \| null | `null` | any string | API key for online endpoint; falls back to env `CAPTURE_API_KEY` |
| `capture_enabled` | bool | `true` | bool | Master toggle for conversation capture (under `tuning`) |
| `capture_always_log` | bool | `true` | bool | Log even "none" outcomes — the LOTS mandate (under `tuning`) |
| `capture_max_summaries` | int | `10` | >= 1 | Max summaries per interaction, prevents runaway logging (under `tuning`) |
