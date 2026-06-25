# Architecture Overview

MnemoQ is a local-first memory engine for AI agents. It stores learnings as JSONL, retrieves them via a multi-channel scoring pipeline, and integrates with any MCP-compatible client. This doc gives newcomers a conceptual map and contributors a module-level reference.

> **Reading guide**: Sections 1–3 are for newcomers. Sections 4–6 are for contributors and integrators. Skip what you don't need.

---

## 1. What MnemoQ Is

MnemoQ gives AI coding agents persistent memory across sessions. When an agent learns a lesson — "this API rejects expired tokens silently" — it logs that learning. Next session, before starting work, the agent retrieves relevant learnings and avoids repeating mistakes.

**Three properties define the system:**

- **Local-first**: all data lives in your project's `memory/` directory as JSONL files. No cloud, no database server.
- **Multi-channel retrieval**: combines tiered scoring, BM25 lexical search, semantic embeddings, and optional reranking — each channel catches different failure modes.
- **MCP-native**: the primary integration path is the Model Context Protocol. Works with Claude Desktop, Cursor, Windsurf, VS Code, and any MCP-compatible client.

```
 ┌─────────┐     --log      ┌───────────┐     append     ┌──────────────────┐
 │  Agent   │──────────────▶│  MnemoQ   │──────────────▶│  learnings.jsonl  │
 │          │◀──────────────│  Engine   │◀──────────────│                  │
 └─────────┘    --retrieve  └───────────┘    read        └──────────────────┘
      │                            │
      │  MCP (stdio JSON-RPC)      │  HTTP API (FastAPI)
      │                            │
      ▼                            ▼
 ┌───────────┐              ┌──────────────┐
 │ mnemoq-mcp│              │ mnemoq       │
 │           │              │ --serve      │
 └───────────┘              └──────────────┘
```

**What problems it solves:**

- Agents repeating the same mistakes across sessions
- Hard-won debugging knowledge vanishing when context windows reset
- No feedback loop for retrieval quality (the grading harness closes this)

---

## 2. The Core Four Operations

MnemoQ has four primary operations. Each has a `*_core` function (returns a dict, used by all access surfaces) and a `handle_*` wrapper (prints output, used by the CLI).

### Log

Validates a new learning entry, checks for duplicates, stamps it with metadata, computes an embedding, and appends to `learnings.jsonl`.

```
Entry JSON
  │
  ▼
 validate_entry()          ← schema check (required fields, types, enums)
  │
  ▼
 find_semantic_duplicate() ← cosine similarity ≥ 0.85 → merge & reject
  │
  ▼
 find_best_match()         ← Jaccard similarity
  │                         ≥ 0.7  → duplicate, increment access_count
  │                         0.4–0.7 + opposing actions → conflict
  │                         < 0.4  → new entry
  ▼
 stamp_entry()             ← auto-inject ts, commit, access_count
  │
  ▼
 embed_entry()             ← sentence-transformers embedding (base64-stored)
  │
  ▼
 append_learning()         ← append to learnings.jsonl
```

### Retrieve

Scores all unresolved entries against the current task context and returns the most relevant warnings and patterns. Five-phase pipeline:

```
 learnings.jsonl
  │
  ▼  Phase 1: Filter + tiered scoring
  │             (recency × importance × relevance, retention window)
  │
  ▼  Phase 2: BM25 lexical scoring
  │             (tokenize trigger/action/reason, corpus stats)
  │
  ▼  Phase 3: Reciprocal Rank Fusion (RRF)
  │             (merge tiered + BM25 rankings)
  │
  ▼  Phase 4: Embedding hybrid scoring
  │             (alpha × normalized_rrf + (1-alpha) × cosine)
  │
  ▼  Phase 5: Optional reranking
  │             (cross-encoder or local LLM, top-N only)
  │
  ▼
 warnings (critical) + patterns (non-critical)
```

Each phase is tunable via `config.json`. Channels degrade gracefully — if embeddings aren't installed, the pipeline runs without the embedding channel at zero overhead. See [Phase 1 Retrieval Quality Summary](phase1-retrieval-quality-summary.md) for the evolution of this pipeline.

### Resolve

Marks an existing entry as resolved by its timestamp. Resolved entries are skipped during retrieval and included in consolidation archives. One-line operation — no scoring, no dedup.

### Consolidate (Sleep Cycle)

Archives all unresolved entries to `archive/sprint-N.jsonl`, then generates a report with promotion candidates, contradictions, stale entries, and quarantine review. After review, `--confirm-reset` clears `learnings.jsonl`.

```
 --consolidate
  │
  ▼
 archive unresolved entries → archive/sprint-N.jsonl
  │
  ▼
 score_for_promotion()      ← access_count, severity, recency
  │
  ▼
 detect_contradictions()    ← entries proposing supersession
  │
  ▼
 check_staleness()          ← git diff since entry's commit
  │
  ▼
 review_quarantine()        ← quarantine.jsonl breakdown
  │
  ▼
 save_session()             ← 10-min window for --confirm-reset
```

Sleep cycle triggers automatically flag when consolidation is due: >50 unresolved entries, >7 days since last consolidation, or quarantine count ≥ 20.

### Secondary Operations

| Command | Description | Details |
|---------|-------------|---------|
| `--update <ts>` | Amend an existing entry by timestamp | Re-validates, re-embeds if text changed, preserves access/reinforcement counts |
| `--stats` | Memory statistics summary | Severity/type/scope breakdowns, reinforcement patterns, sleep cycle status |
| `--review-agents --step N` | AGENTS.md section health report | Checks for learnings overlapping with AGENTS.md sections |
| `--metrics` | Retrieval/logging/consolidation metrics | Deep-dive flags: `--metrics-retrieval`, `--metrics-logging`, `--metrics-consolidation`, `--metrics-trend` |
| `--eval` | Retrieval quality grading harness | Runs fixtures from `memory/eval/grading.jsonl`, reports top-1/top-3 hit rates |
| `--migrate-schema` | Batch schema migration | Migrates all entries to current schema version, writes back |

See [CLI Reference](cli-reference.md) for all flags, mutual-exclusion rules, and examples.

---

## 3. Storage Model

All state lives in a single `memory/` directory inside your project:

```
memory/
├── learnings.jsonl              ← append-only entry log (the core data)
├── config.json                  ← project-specific tuning + constraints
├── metrics.jsonl                ← structured event log for every engine call
├── quarantine.jsonl             ← rejected entries with reasons
├── .consolidate_session.json    ← transient session file for --confirm-reset
├── archive/
│   ├── sprint-1.jsonl           ← archived entries from consolidation
│   └── sprint-2.jsonl
└── eval/
    └── grading.jsonl            ← retrieval quality fixtures
```

**learnings.jsonl** is the heart of the system. Each line is one JSON object — a learning entry. Entries follow a trigger/action/reason model:

- **trigger**: starts with "When" — describes the situation that triggers the learning
- **action**: contains "ALWAYS" or "NEVER" — the prescribed response
- **reason**: why this action is correct

Example entry (simplified):

```json
{
  "step": 3,
  "source_agent": "gm",
  "type": "bug_fix",
  "domain": "backend",
  "components": ["api", "auth"],
  "files_touched": ["src/auth.py"],
  "trigger": "When JWT validation fails on expired tokens",
  "action": "ALWAYS check expiry before signature verification",
  "reason": "PyJWT silently accepts expired tokens when verify_exp is not set",
  "importance": 8,
  "severity": "major",
  "ts": "2025-06-25T10:30:00Z",
  "commit": "a1b2c3d",
  "access_count": 0,
  "embedding": "<base64 float16 vector>"
}
```

The engine auto-stamps `ts`, `commit`, `access_count`, `reinforcement_count`, `embedding`, `schema_version`, and provenance fields (`project_id`, `origin_project`, `contributing_projects`, `contributors`) at log time.

See [Data Schema](data-schema.md) for the full field reference, enum values, and optional fields.

---

## 4. Module Map

Every module under `src/agent_memory/`, grouped by tier. The `*_core` convention: `core` functions return dicts (used by all access surfaces), `handle_*` wrappers print output (CLI only).

### Engine Core (`src/agent_memory/engine/`)

| Module | Responsibility | Key functions | Called by |
|--------|---------------|---------------|-----------|
| `retrieval.py` | 5-phase retrieval pipeline, embeddings, BM25, RRF fusion | `retrieve_core()`, `score_entry()`, `bm25_score()`, `find_semantic_duplicate()`, `compute_embedding()` | cli.py, mcp_server.py, server.py, sdk/client.py |
| `handlers.py` | Log, update, resolve, stats operations | `log_core()`, `update_core()`, `resolve_core()`, `stats_core()` | cli.py, mcp_server.py, server.py, sdk/client.py |
| `validation.py` | Schema validation, Jaccard similarity, conflict detection | `validate_entry()`, `find_best_match()`, `actions_oppose()` | handlers.py |
| `consolidation.py` | Sleep Cycle: archive, promotion, contradictions, stale check | `consolidate_core()`, `score_for_promotion()`, `detect_contradictions()` | cli.py, mcp_server.py, server.py, sdk/client.py |
| `io.py` | File I/O: read, append, atomic write, quarantine | `read_learnings()`, `append_learning()`, `write_learnings()`, `quarantine()` | All engine modules |
| `constants.py` | Default values, valid enum sets, `DEFAULTS` dict | `DEFAULTS` dict | cli.py, mcp_server.py, sdk/client.py |
| `metrics.py` | Structured event logging and analysis | `log_event()`, `read_metrics()`, `_retrieval_stats()`, `_logging_stats()` | All engine modules |
| `reranker.py` | Optional Phase 5 reranking (cross-encoder, LLM-local) | `rerank()` | retrieval.py |
| `mcp_server.py` | MCP server: tool definitions, dispatch, resource templates | `run_server()`, `_call_tool()`, `_read_resource()` | mcp_main.py |
| `server.py` | FastAPI HTTP API: CRUD endpoints, WebSocket, EventHub | `create_app()` | cli.py (`--serve`) |
| `dashboard_api.py` | Dashboard router: analysis, charts, alerts endpoints | `create_dashboard_router()` | server.py (dashboard=True) |
| `analysis.py` | Dashboard analytics: metrics cache, alerts, trend data | `alerts_list()`, `get_metrics_data()`, `invalidate_metrics_cache()` | dashboard_api.py, server.py |
| `eval.py` | Grading harness for retrieval quality | `load_fixture()`, `run_grading()` | cli.py (`--eval`) |
| `git_utils.py` | Git stamping (commit, ts) and staleness checking | `stamp_entry()`, `check_staleness()` | handlers.py, consolidation.py |
| `migrate.py` | Schema migration (migrate-on-read, batch migration) | `migrate_entry()`, `migrate_all()`, `run_migration()` | io.py, cli.py (`--migrate-schema`) |
| `models.py` | Pydantic models for API request/response schemas | `LogRequest`, `UpdateRequest`, `ResolveRequest`, `ErrorResponse` | server.py, sdk/client.py |
| `profile.py` | Developer profile loader (global preferences) | `load_profile()`, `get_profile_context()` | retrieval.py |
| `triggers.py` | Sleep cycle trigger checks (threshold, time, quarantine) | `check_sleep_cycle()` | retrieval.py, handlers.py |
| `agents_review.py` | AGENTS.md conflict detection and section health review | `check_agents_conflict()`, `handle_review_agents()` | handlers.py, cli.py |
| `__init__.py` | Package marker | — | — |

### Entry Points & Tooling (`src/agent_memory/`)

| Module | Responsibility | Key functions | Called by |
|--------|---------------|---------------|-----------|
| `cli.py` | Thin CLI dispatcher: arg parsing, path setup, config load, mode dispatch | `main()`, `setup_paths()`, `load_config()`, `resolve_memory_dir()` | Console entry point `mnemoq` |
| `mcp_main.py` | MCP server entry point | `main()` | Console entry point `mnemoq-mcp` |
| `scaffold.py` | Project initialization (`mnemoq-scaffold`) | scaffold logic, config preset selection | Console entry point `mnemoq-scaffold` |
| `update.py` | Engine file updater for existing projects (`mnemoq-update`) | update logic, shim detection | Console entry point `mnemoq-update` |
| `shim.py` | Shim template for delegating to central engine | `SHIM_TEMPLATE`, `is_shim()` | scaffold.py, update.py |
| `engine_version.py` | Version resolution (VERSION file or package metadata) | `get_engine_version()` | cli.py, server.py |

### SDK (`src/agent_memory/sdk/`)

| Module | Responsibility | Key functions | Called by |
|--------|---------------|---------------|-----------|
| `client.py` | Programmatic access: local (direct file) and remote (HTTP) transports | `MemoryClient` class | User code |
| `exceptions.py` | SDK exception hierarchy | `APIError`, `ValidationError`, `ConflictError`, `NotFoundError` | client.py |

### Dashboard (`src/agent_memory/dashboard/`)

| Module | Responsibility | Called by |
|--------|---------------|-----------|
| `static/` | Static web assets (HTML, CSS, JS) for the built-in dashboard | server.py (dashboard=True) |

### Dispatch Pattern

`cli.py` is a thin dispatcher. It sets up `Paths` and `ctx`, then delegates to engine modules:

```
 cli.py main()
  │
  ├── setup_paths()     → Paths dataclass (memory_dir, learnings_path, etc.)
  ├── load_config()     → dict of overrides from config.json
  ├── _CTX.update()     → merge overrides onto DEFAULTS (lowercased keys)
  │
  ├── --step N          → handle_retrieval()  → retrieve_core()
  ├── --log '<json>'    → handle_log()        → log_core()
  ├── --update <ts>     → handle_update()     → update_core()
  ├── --resolve <ts>    → handle_resolve()    → resolve_core()
  ├── --stats           → handle_stats()      → stats_core()
  ├── --consolidate     → handle_consolidate()→ consolidate_core()
  ├── --review-agents   → handle_review_agents()
  ├── --metrics         → metrics reporting
  ├── --eval            → run_grading()
  ├── --migrate-schema  → run_migration()
  ├── --serve           → create_app() + uvicorn
  └── --dashboard       → create_app(dashboard=True) + uvicorn
```

The `ctx` dict flows from `constants.py` → `cli.py` (overlaid with `config.json`) → every engine function. Engine modules never read module globals — they receive `ctx` and `paths` as parameters.

---

## 5. Access Surfaces

All five access surfaces converge on the same `*_core` functions. This means the CLI, MCP server, HTTP API, SDK, and dashboard all execute identical logic — no surface-specific code paths.

```
                         ┌─────────────────────────────────┐
                         │          *_core() functions      │
                         │  retrieve_core  log_core         │
                         │  resolve_core   update_core      │
                         │  stats_core     consolidate_core │
                         └────────▲────────────────▲───────┘
                                  │                │
              ┌───────────────────┼────────────────┼───────────┐
              │                   │                │           │
     ┌────────┴───────┐  ┌───────┴──────┐  ┌──────┴──────┐  ┌──┴──────────┐
     │  CLI (cli.py)  │  │ MCP server   │  │ HTTP API    │  │   SDK       │
     │  handle_*()    │  │ _call_tool() │  │ FastAPI     │  │ MemoryClient│
     │  prints output │  │ JSON-RPC     │  │ endpoints   │  │ local/HTTP  │
     └────────────────┘  └──────────────┘  └─────────────┘  └─────────────┘
                                              │
                                       ┌──────┴──────┐
                                       │  Dashboard  │
                                       │  router +   │
                                       │  EventHub   │
                                       └─────────────┘
```

### CLI

The `mnemoq` command is the primary interface. Flags map directly to `*_core` functions via `handle_*` wrappers that print formatted output. `--serve` and `--dashboard` launch the HTTP server and dashboard respectively. See [CLI Reference](cli-reference.md) for the full flag list.

### MCP Server

Runs over stdio using JSON-RPC 2.0 — no HTTP dependency. Launched via `mnemoq-mcp` or `python -m agent_memory.mcp_main`. Auto-discovers `memory/` in the current working directory.

**Five tools exposed:**

| Tool | Maps to | Description |
|------|---------|-------------|
| `retrieve_learnings` | `retrieve_core()` | Retrieve warnings and patterns for current task context |
| `log_learning` | `log_core()` | Log a new learning entry |
| `resolve_learning` | `resolve_core()` | Mark an entry resolved by timestamp |
| `get_stats` | `stats_core()` | Get memory statistics |
| `consolidate` | `consolidate_core()` | Trigger Sleep Cycle consolidation |

**Two resource templates:** `learnings://project/{project_id}` and `metrics://project/{project_id}`.

See [MCP Integration Guide](mcp-integration.md) for client configuration snippets.

### HTTP API

FastAPI server launched via `mnemoq --serve [--port 8765]`. Optional API key authentication via `X-API-Key` header (configured in `config.json`).

| Endpoint | Method | Maps to |
|----------|--------|---------|
| `/api/retrieve` | GET | `retrieve_core()` |
| `/api/log` | POST | `log_core()` |
| `/api/update` | POST | `update_core()` |
| `/api/resolve` | POST | `resolve_core()` |
| `/api/stats` | GET | `stats_core()` |
| `/api/metrics` | GET | `read_metrics()` + stats helpers |
| `/api/consolidate` | POST | `consolidate_core()` |
| `/api/health` | GET | version + status check |
| `/ws/events` | WebSocket | live event stream (EventHub) |

### SDK

`MemoryClient` in `sdk/client.py` supports two transports:

- **Local**: direct file access — imports `*_core` functions, builds `ctx` from `config.json`, operates on `learnings.jsonl` directly. No server needed.
- **Remote**: HTTP client — calls the REST API, handles auth, raises typed exceptions (`ValidationError`, `ConflictError`, `NotFoundError`).

```python
from agent_memory.sdk import MemoryClient

# Local (no server)
client = MemoryClient(memory_dir="/path/to/project/memory")

# Remote (needs --serve running)
client = MemoryClient(base_url="http://localhost:8765", api_key="secret")
```

### Dashboard

Built-in web UI launched via `mnemoq --dashboard [--port 8765]`. Mounts static files (HTML/CSS/JS) and a dashboard API router with analysis endpoints. `EventHub` broadcasts live events via WebSocket — log, resolve, consolidate, and alert events push to connected browsers in real time. A file watcher polls `metrics.jsonl` to capture CLI-triggered events.

### Eval Harness

The `--eval` flag runs retrieval quality measurement. It loads fixtures from `memory/eval/grading.jsonl` (pairs of task context + expected trigger), runs retrieval for each, and reports top-1 and top-3 hit rates. This is the feedback loop for tuning retrieval parameters — without it, adjusting `embedding_alpha` or `bm25_k1` is guesswork.

---

## 6. Key Design Decisions

These are deliberate architectural choices, not limitations to fix. See also [AGENTS.md](../AGENTS.md) § Intentional Design Decisions.

### Single validation path

`validate_entry()` in `validation.py` is the source of truth for schema enforcement. API models in `models.py` use `dict[str, Any]` to avoid duplicate validation drift — Pydantic models validate structure, but the engine always runs `validate_entry()` as the final gate. No second schema definition to keep in sync.

### `*_core` functions return dicts

`log_core()`, `retrieve_core()`, `resolve_core()`, `consolidate_core()`, `stats_core()`, `update_core()` all return plain dicts. This keeps the engine decoupled from the API layer — the CLI, MCP server, HTTP API, and SDK all consume the same dict output without any serialization adapter.

### Graceful degradation

Optional channels (embeddings, reranker) have zero overhead when disabled. If `sentence-transformers` isn't installed, the embedding channel silently skips — retrieval runs on tiered + BM25 + RRF alone. If the reranker is set to `"none"` (default), Phase 5 is a passthrough. No feature flags, no conditional imports at the call site.

### Append-only learnings.jsonl

`learnings.jsonl` is append-only. Use `--update` to amend, `--resolve` to mark resolved. Atomic writes via temp file with Windows retry (3 attempts, exponential backoff). Never edit by hand — schema validation will fail on next load.

### Migrate-on-read

`io.read_learnings()` calls `migrate_entry()` on every entry as it reads. Old entries get new fields backfilled automatically. `--migrate-schema` does an explicit batch migration that writes the updated file. Current schema version: 1.

### No file locking

The engine uses read-modify-write without file locking. Safe under the current sequential execution model. If parallel agent execution is added, file locking (`fcntl.flock()` or platform equivalent) would be needed. This is a documented tradeoff, not a bug.

### ctx dict is read-only in core functions

Core functions receive `ctx` and `paths` as parameters and never mutate them. No defensive copy is needed. If a core function mutates `ctx`, that's a bug to flag — not a reason to add copying overhead.

---

## What This Doc Is NOT

- **Not a code walkthrough** — point to source files for implementation details
- **Not a config tuning guide** — see [Config Tuning Guide](config-tuning.md)
- **Not a CLI reference** — see [CLI Reference](cli-reference.md)
- **Not a data schema reference** — see [Data Schema](data-schema.md)
- **Not an MCP integration guide** — see [MCP Integration](mcp-integration.md)
- **Not an open-core boundary doc** — see [Open-Core Architecture](open-core-architecture.md)

## Further Reading

| Doc | What it covers |
|-----|---------------|
| [README.md](../README.md) | Install, quick start, CLI commands |
| [Data Schema](data-schema.md) | Full field reference, enum values, sample entries |
| [Config Tuning](config-tuning.md) | All parameters, ranges, tuning recipes |
| [CLI Reference](cli-reference.md) | All flags, mutual-exclusion rules, examples |
| [MCP Integration](mcp-integration.md) | Client setup for Claude Desktop, Cursor, Windsurf, VS Code |
| [Open-Core Architecture](open-core-architecture.md) | AGPL core vs proprietary Pro boundary |
| [ROADMAP](ROADMAP.md) | Current status and planned features |
| [AGENTS.md](../AGENTS.md) | Coding conventions, intentional design decisions |
