# Agent Memory Engine — Consolidated Roadmap

A phased roadmap to evolve the Agent Memory Engine from a JSONL-based episodic memory system into a production-grade, local-first agent memory platform with a freemium product model. Informed by 2025–2026 agent memory research (AdMem, MRAgent, Mem0 v3, Zep/Graphiti, A-Mem, SwiftMem, E-mem, GAAMA, Engram, MemMachine, OpenAI Dreaming) and existing project plans.

---

## Product Vision

**The Obsidian of agent memory.** A privacy-first, local-store agent memory system that works with any MCP-compatible AI tool. Free forever for local use. Paid Pro tier adds cloud sync, team sharing, and a web dashboard.

### Why This Wins

- **Privacy gap**: Mem0 ($24M Series A) and Zep are cloud-first. No dominant local-first agent memory exists.
- **MCP distribution**: Once you're an MCP server, you work with Claude Desktop, Cursor, Windsurf, VS Code, and any MCP client. Instant market access.
- **Solo-dev viable**: Ship free tier immediately. Cloud sync is additive (sync layer over local store), not foundational. No infra costs until paying users.
- **Market is early**: Agent memory is crowded but no local-first winner has emerged.

### Tier Structure

| Feature | Free | Pro |
|---------|------|-----|
| Local JSONL/SQLite store | ✅ | ✅ |
| CLI (log, retrieve, consolidate, stats) | ✅ | ✅ |
| MCP server (local stdio) | ✅ | ✅ |
| Embedding-based retrieval | ✅ | ✅ |
| Developer profile | ✅ | ✅ |
| Cloud sync | ❌ | ✅ |
| Team shared learnings | ❌ | ✅ |
| Web dashboard | ❌ | ✅ |
| Cross-project global store | ❌ | ✅ |
| Advanced analytics | ❌ | ✅ |
| Priority support | ❌ | ✅ |

---

## Current State Summary

- **Storage**: Flat JSONL files (`learnings.jsonl`, `quarantine.jsonl`, `metrics.jsonl`)
- **Retrieval**: Linear scan, Jaccard similarity on trigger+action text, tiered relevance scoring (component > file > domain > no-match), exponential decay by step diff
- **Dedup**: Jaccard ≥0.7 = duplicate, 0.4–0.7 with opposing ALWAYS/NEVER = conflict
- **Consolidation**: Manual "Sleep Cycle" — archive to sprint files, promotion candidates, contradiction detection, git staleness check
- **Deployment**: Central engine + thin shims per project, `update.py` for propagation
- **Metrics**: Structured event logging per invocation, cross-project reporting
- **No embeddings, no vector store, no graph, no async, no API server**

---

## Phase 1 — Retrieval Quality (v1.17 – v1.19)

**Goal**: Make retrieval genuinely semantic. This is the core product — if retrieval isn't good, nothing else matters.

### 1.1 BM25 Lexical Retrieval (v1.17.0)
**Problem**: Jaccard similarity on whitespace tokens ignores term frequency and document length.

- Implement BM25 scoring on trigger+action+reason text (~20 lines, stdlib-only)
- Three-channel fusion: BM25 + current tiered relevance + Jaccard dedup, combined via Reciprocal Rank Fusion (RRF, k=60)
- No model download required — ships immediately as a pure quality improvement

**Files**: `retrieval.py`, `constants.py`, `config.json`

> 📊 Dashboard: Retrieval Explorer — show 3-channel fusion score breakdown

### 1.2 Embedding-Based Retrieval (v1.18.0)
**Problem**: Lexical matching misses semantic matches ("collision" vs "overlap").

- `sentence-transformers` `all-MiniLM-L6-v2` (~90MB), lazy download to `~/.agent-memory/models/`
- Generate embedding for `trigger + action + reason` at `--log` time. Store as base64 float16 array on entry
- Hybrid scoring: `final = alpha * bm25_score + (1-alpha) * embedding_cosine`, alpha configurable per project (default 0.5)
- Graceful degradation: if model unavailable, skip embedding scores entirely (alpha auto-set to 1.0, falls back to Jaccard — zero dependency by default)
- Schema versioning prerequisite: add `schema_version: 1` field + migration runner before shipping this (adds embedding field)

**Files**: `retrieval.py`, `validation.py`, `io.py`, `constants.py`, new `src/engine/migrate.py`, `config.json`

> 📊 Dashboard: Retrieval Explorer — hybrid score split (lexical vs semantic); Settings — alpha config; Learnings detail — embedding status

### 1.3 Embedding-Based Dedup (v1.19.0)
**Problem**: "Same lesson, different wording" creates duplicate entries.

- At `--log` time, cosine vs recent same-domain entries (threshold 0.85) before existing Jaccard check
- On match: merge — combine `access_count`, keep richer description, append dup's source agent to `contributors` list
- Quarantine rejected entry with reason `semantic_duplicate`
- Requires provenance fields: add `project_id`, `origin_project`, `contributing_projects` to schema

**Files**: `handlers.py`, `validation.py`, `io.py`, `migrate.py`

> 📊 Dashboard: Metrics > Dedup — semantic duplicate stats; Quarantine — new reason category; Learnings detail — contributors field

### 1.4 Reranking Pass (v1.19.1)
**Problem**: First-stage retrieval returns right candidates in wrong order.

- After initial scoring, optional second-pass reranker on top-N (default 20)
- Configurable: `reranker: "none" | "cross-encoder" | "llm-local"`
- Cross-encoder: `ms-marco-MiniLM-L-12-v2` (local, no API)
- LLM-local: uses local model (Ollama/LM Studio) if available, prompt: "Rate relevance 0-10"
- Only enabled when model is present; zero overhead otherwise

**Files**: `retrieval.py`, `constants.py`, `config.json`

> 📊 Dashboard: Settings — reranker config; Retrieval Explorer — pre/post-rerank ordering

### 1.5 Grading Harness (v1.19.2)
**Problem**: No way to measure retrieval quality objectively.

- Per-project fixture: `memory/eval/grading.jsonl` — list of `(task_context, expected_entry_ts)` pairs
- `--eval` command: runs retrieval against each fixture, reports top-1 / top-3 hit rate
- Ship with a default fixture per project type (game, web, data)
- This becomes the real "retrieval relevance" metric — replaces guessed numbers

**Files**: new `src/engine/eval.py`, `filter.py`

> 📊 Dashboard: Metrics — new "Retrieval Grading" sub-tab (top-1/top-3 hit rates); Settings — run eval button

---

## Phase 2 — MCP Server & API (v1.20 – v1.21)

**Goal**: Make the memory system accessible from any MCP-compatible AI tool. This is the distribution channel.

### 2.1 HTTP API Server (v1.20.0)
**Problem**: CLI-only interface requires subprocess calls. No way to query from other tools.

- `--serve` mode: HTTP server using FastAPI (supports GUI dashboard integration)
- Endpoints:
  - `GET /retrieve?step=N&components=A,B&domain=D` → JSON array of learnings
  - `POST /log` → validate + append
  - `POST /resolve` → mark resolved
  - `GET /stats` → summary statistics
  - `GET /metrics?type=retrieval&since=2026-01-01` → filtered metrics
  - `POST /consolidate` → trigger sleep cycle
- JSON request/response, same logic as CLI handlers (shared code path)
- Configurable port (default 8765), localhost-only binding by default
- API key auth via `config.json: api_key` (optional, disabled by default for local use)

**Files**: new `src/engine/server.py`, `filter.py`, `config.json`

> **Note**: The HTTP API server must be implemented as FastAPI (not stdlib `http.server`) to support the GUI dashboard. See `memory-engine-gui-686f76.md` for the full GUI architecture (web dashboard, Tauri desktop wrapper, Textual TUI). The dashboard's `/api/metrics/*` endpoints wrap the analysis functions from `advanced-metrics-system-686f76.md`.

### 2.2 MCP Server (v1.20.1)
**Problem**: No standard protocol for agents to access memory.

- Implement MCP server interface (JSON-RPC over stdio)
- Tools exposed:
  - `retrieve_learnings(step, components, files, domain)` → relevant learnings
  - `log_learning(entry_json)` → validation result + dedup status
  - `resolve_learning(timestamp)` → success/failure
  - `get_stats()` → memory summary
  - `consolidate(sprint_number)` → consolidation report
- Resources exposed:
  - `learnings://project/<id>` → all learnings
  - `metrics://project/<id>` → metrics summary
- Works with Claude Desktop, Cursor, Windsurf, VS Code, and any MCP client
- Entry point: `python -m agent_memory.mcp` or `agent-memory mcp` (after packaging)
- Auto-discovery: reads `AGENT_MEMORY_DIR` env or discovers `memory/` in cwd

**Files**: new `src/engine/mcp_server.py`, new `src/mcp_main.py`

### 2.3 Web Dashboard (v1.20.2)
**Problem**: Stats and consolidation are CLI-only. No visual exploration of memory state.

- FastAPI-backed web dashboard served locally (free tier)
- 7 tabbed views: Dashboard, Learnings Browser, Retrieval Explorer, Metrics Deep-Dive, Consolidation Console, Cross-Project Fleet, Settings
- Vanilla JS SPA + Chart.js via CDN (no build step, no npm)
- WebSocket live event feed: log/resolve/consolidate/alert notifications
- `agent-memory dashboard` starts server on localhost:8766, auto-opens browser
- Cloud-hosted version for Pro tier (same UI, cloud-backed data)
- Full architecture in `memory-engine-gui-686f76.md` Phase 1
- Metrics Deep-Dive page wraps analysis functions from `advanced-metrics-system-686f76.md` Phases 1-3

**Files**: new `src/dashboard/` (FastAPI app + static frontend), `filter.py`

### 2.4 Python SDK (v1.21.0)
**Problem**: Developers integrating into custom agents need a programmatic API, not subprocess calls.

- `pip install agent-memory` → `from agent_memory import MemoryClient`
- `MemoryClient(memory_dir=...)` with methods: `.retrieve(...)`, `.log(...)`, `.resolve(...)`, `.stats()`, `.consolidate()`
- Thin wrapper over the HTTP API (when server running) or direct file access (when local)
- Async support: `AsyncMemoryClient` with `aiohttp` for non-blocking retrieval
- Type hints + Pydantic models for learning entries

**Files**: new `src/sdk/`, `pyproject.toml` (packaging)

### 2.5 pip Packaging (v1.21.1)
**Problem**: Currently requires manual file copying. No version management.

- Proper `pyproject.toml` with `console_scripts` entry point: `agent-memory`
- `agent-memory --step N` replaces `python memory/filter.py --step N`
- Shim mode preserved: existing projects keep working via `python memory/filter.py`
- `pip install agent-memory` for new users; `agent-memory scaffold <project>` for setup
- Published to PyPI (free tier)
- Optional: `brew install agent-memory` (Homebrew formula)

**Files**: `pyproject.toml`, new `src/cli.py` (thin entry point)

---

## Phase 3 — Storage & Scalability (v1.22 – v1.23)

**Goal**: Prepare the storage layer for scale (cross-project sharing, cloud sync, larger datasets).

### 3.1 SQLite Storage Backend (v1.22.0)
**Problem**: Linear scan of JSONL is O(n) per retrieval. At 1000+ entries or cross-project pools, latency matters.

- `sqlite3` backend (stdlib, zero dependency) as alternative to JSONL
- Schema: `learnings(id, ts, step, source_agent, type, domain, components_json, files_json, trigger, action, reason, importance, severity, scope, debt_level, verified, resolved, access_count, reinforcement_count, commit, symptoms, embedding_blob, project_id, origin_project, schema_version)`
- Indexes on: `domain`, `severity`, `resolved`, `step`, `project_id`
- FTS5 full-text search index on `trigger + action + reason` for BM25 first-stage filtering
- Migration command: `--migrate-to-sqlite` reads JSONL, writes DB
- Config: `storage_backend: "jsonl" | "sqlite"` (default: jsonl for backwards compat)
- JSONL remains as export/backup format; SQLite is the working store

**Files**: new `src/engine/storage.py`, `io.py`, `filter.py`, `config.json`

> 📊 Dashboard: Settings — storage backend selector; Dashboard — latency indicator

### 3.2 Incremental Index Updates (v1.22.1)
**Problem**: Every retrieval re-reads and re-scores all entries.

- On `--log`, insert into SQLite + FTS5 index incrementally
- On retrieval: FTS5 BM25 ranking for first-stage filter → tiered scoring on top-N
- Reduces retrieval from O(n) to O(log n + k)
- Embedding similarity computed only on top-N candidates (not all entries)

**Files**: `storage.py`, `retrieval.py`, `handlers.py`

### 3.3 Auto-Archive (v1.23.0)
**Problem**: `learnings.jsonl` grows unbounded between manual Sleep Cycles.

- Config: `auto_archive_threshold: 200` (0 = disabled)
- When entry count exceeds threshold, auto-trigger lightweight consolidation: archive oldest resolved entries, keep unresolved
- Prompt user to run full `--consolidate` when unresolved count > 50
- Archive entries moved to `archive/auto-N.jsonl` with metadata

**Files**: `handlers.py`, `consolidation.py`, `constants.py`

> 📊 Dashboard: Consolidation Console — auto-archive status; Settings — threshold config; Alerts — auto-archive notification

---

## Phase 4 — Memory Intelligence (v1.24 – v1.26)

**Goal**: Make the memory system actually smart — adaptive decay, temporal reasoning, entity awareness, and graph linking.

### 4.1 Spaced Repetition Scheduling (v1.24.0)
**Problem**: Fixed exponential decay ignores access patterns. Frequently-retrieved entries fade at the same rate as never-accessed ones.

- SM-2 algorithm: each entry gets `ease_factor` (default 2.5) and `next_review_step`
- On retrieval hit: update ease factor, schedule next review at `step + interval`
- On consolidation: surface entries due for review in promotion report
- Entries not accessed by `next_review_step` get accelerated decay
- Mirrors Ebbinghaus forgetting curve + OpenAI Dreaming's staleness handling

**Files**: `retrieval.py`, `consolidation.py`, `validation.py`, `constants.py`

> 📊 Dashboard: Learnings detail — SM-2 schedule; Metrics > Lifecycle — review schedule; Consolidation — "Due for Review" section

### 4.2 Bi-Temporal Timestamps (v1.24.1)
**Problem**: Single `ts` field conflates "when learned" with "when relevant." No "as-of" queries.

- Add `valid_from` (when learning became true) and `valid_until` (when superseded, default ∞)
- On conflict resolution: set old entry's `valid_until = now`, new entry's `valid_from = now`
- Retrieval supports `--as-of <step>` for historical state queries
- Enables temporal debugging: "what did we know at step 15?"

**Files**: `validation.py`, `handlers.py`, `retrieval.py`, `consolidation.py`, `migrate.py`

> 📊 Dashboard: Retrieval Explorer — "as-of" step input; Learnings — valid_from/valid_until + supersession chain; Consolidation — supersession history

### 4.3 Entity Extraction & Linking (v1.25.0)
**Problem**: Component matching is exact string. "Player" and "PlayerEntity" don't match.

- Extract entities from trigger/action/reason text on `--log`
- Store in `entities` field: normalized names, types (class, file, concept)
- Entity normalization: lowercase, strip suffixes (Entity, Component, Manager), Levenshtein ≤2 fuzzy match
- Retrieval matches on entity overlap in addition to component overlap
- Entity collection in SQLite for fast lookup

**Files**: new `src/engine/entities.py`, `validation.py`, `retrieval.py`, `handlers.py`, `storage.py`

> 📊 Dashboard: Learnings detail — extracted entities; Retrieval Explorer — entity match indicator; new entity filter

### 4.4 Memory Link Graph (v1.25.1)
**Problem**: Entries are isolated. No way to traverse from one learning to related ones.

- On `--log`, compute links to existing entries: shared components, shared files, entity overlap, semantic similarity > 0.5
- Store links in SQLite link table: `(source_id, target_id, link_type, weight)`
- Link types: `same_component`, `same_file`, `semantic_similar`, `contradicts`, `supersedes`
- On retrieval, expand results: for each hit, pull top-2 linked entries (configurable `link_expansion: 2`)
- Inspired by A-Mem's Zettelkasten links and GAAMA's graph-augmented retrieval

**Files**: new `src/engine/graph.py`, `handlers.py`, `retrieval.py`, `storage.py`

> 📊 Dashboard: Learnings detail — linked entries with link type; Retrieval Explorer — expanded results from graph; new graph visualization

### 4.5 Cue-Tag-Content Graph (v1.26.0)
**Problem**: Flat keyword matching can't do multi-hop reasoning.

- Build lightweight graph: `Cue` (token) → `Tag` (concept/category) → `Content` (entry)
- Tags derived from domain + component clustering
- Retrieval traverses: query cues → find tags → find content via tags
- In-memory dict-of-lists, rebuilt on load (fast for <10k entries)
- Enables multi-hop: "What did we learn about physics that also affects rendering?"

**Files**: `graph.py`, `retrieval.py`

> 📊 Dashboard: Retrieval Explorer — tag traversal path; new tag map visualization

### 4.6 Graph Consolidation (v1.26.1)
**Problem**: Graph grows fragmented over time.

- During Sleep Cycle: merge tags with >0.8 Jaccard overlap
- Prune links below weight threshold
- Rebuild layout for cache locality (if using SQLite)
- Report graph stats: node count, edge count, density, orphans
- Inspired by SwiftMem's co-consolidation

**Files**: `graph.py`, `consolidation.py`

> 📊 Dashboard: Metrics — new "Graph Stats" sub-tab; Consolidation Console — graph consolidation report

---

## Phase 5 — Procedural Memory & Multi-Agent (v1.27 – v1.28)

**Goal**: Support richer memory types and multi-agent attribution.

### 5.1 Procedural Memory Type (v1.27.0)
**Problem**: System stores "what was learned" but not "how to do things" as reusable workflows.

- New entry type: `procedure` (alongside `bug_fix`, `optimization`, `architectural_pattern`)
- Additional fields: `preconditions`, `steps` (ordered list), `expected_outcome`
- Retrieval: when task matches preconditions, surface as "Suggested Procedure"
- Track `success_count` / `failure_count` when referenced
- Low success rate → flag for review in consolidation

**Files**: `constants.py`, `validation.py`, `retrieval.py`, `handlers.py`, `consolidation.py`

> 📊 Dashboard: Learnings — new type filter + specialized procedure detail panel; Retrieval Explorer — "Suggested Procedure" card; Consolidation — low success-rate flag

### 5.2 Actor-Aware Attribution (v1.27.1)
**Problem**: `source_agent` records who logged, but not who observed or verified.

- Add `observed_by` (list) and `verified_by` (list) fields
- Retrieval filter: `--trusted-agents gm,code-reviewer` surfaces only entries from those agents
- Metrics track per-agent contribution quality (quarantine rate, conflict rate, promotion rate)
- Inspired by Mem0's actor-aware memory

**Files**: `validation.py`, `retrieval.py`, `metrics.py`, `constants.py`

> 📊 Dashboard: Learnings detail — observed_by/verified_by; Retrieval Explorer — trusted-agents filter; Metrics > Agents — enhanced breakdown

### 5.3 Auto Conflict Resolution (v1.28.0)
**Problem**: Conflict detection is informational only — requires manual Challenge Protocol.

- When `actions_oppose` triggers at 0.4–0.7 similarity, auto-create proposed supersession entry
- LLM-assisted (local model): generate reason explaining why old rule no longer applies
- Require `--confirm` flag to apply, or auto-apply if `auto_resolve_conflicts: true` in config
- Set old entry's `valid_until`, new entry's `valid_from` (uses bi-temporal from 4.2)
- Log to metrics with `outcome: AUTO_SUPERSEDED`
- Human gate preserved: default is propose-only

**Files**: `handlers.py`, `consolidation.py`, `metrics.py`

> 📊 Dashboard: Consolidation Console — proposed supersessions with approve/reject; WebSocket — supersession event; Settings — auto-resolve toggle

### 5.4 Cross-Project Learning Transfer (v1.28.1)
**Problem**: Each project's memory is isolated. Learnings about general patterns can't be shared.

- `--export --filter type=architectural_pattern,severity=major` → curated JSONL export
- `--import <file>` → validates, dedups (using embedding dedup), merges with project-specific stamping
- `--promote <ts>` → promote local learning to shared global store (`~/.agent-memory/shared-learnings.jsonl`); synced to team store once cloud sync is available
- During consolidation, suggest cross-project candidates: entries with high access_count and `scope: system`
- Optional: shared `~/.agent-memory/shared-learnings.jsonl` that retrieval merges with project entries
- Auto-promotion suggestions during Sleep Cycle: project-agnostic entries with ≥2 reinforcements

**Files**: new `src/engine/transfer.py`, `filter.py`, `retrieval.py`, `consolidation.py`

> 📊 Dashboard: Fleet — export/import/promote buttons; Learnings — "Shared Learnings" filter; Consolidation — cross-project promotion suggestions

---

## Phase 6 — Cloud Sync Layer / Pro Tier (v2.0 – v2.2)

**Goal**: Add cloud sync as the paid Pro tier. Local store remains source of truth; cloud is a sync layer. The local web dashboard (2.3) becomes cloud-hosted for Pro tier.

### 6.1 Sync Architecture (v2.0.0)
**Problem**: No cross-device or cross-team sharing mechanism.

- Sync agent: background process that pushes local changes to cloud and pulls remote changes
- Conflict resolution: last-write-wins for entry fields, append-only for new entries
- Sync protocol: REST API to cloud service (PostgreSQL + Redis backend)
- Local SQLite store is source of truth; cloud is a derived replica
- Offline-first: all operations work without network; sync queues changes
- Auth: API key per user, OAuth for team management

**Files**: new `src/engine/sync.py`, new `src/cloud/` (server-side), `config.json`

> 📊 Dashboard: Dashboard header — sync status indicator; Settings — sync config; WebSocket — sync events

### 6.2 Team Shared Learnings (v2.1.0)
**Problem**: Team members can't share learnings across their individual stores.

- Team scope: `team_id` in config, learnings tagged with `team_id` on sync
- Global team store: `~/.agent-memory/global-learnings.jsonl` synced to cloud
- Retrieval merges `local + team_global`; on tie, local wins
- Builds on cross-project transfer (5.4) with cloud-backed sync

**Files**: `transfer.py`, `sync.py`, `filter.py`, `retrieval.py`, `consolidation.py`

> 📊 Dashboard: Learnings — scope filter (local/team/global); Fleet — team scope selector

### 6.3 Cloud-Hosted Dashboard (v2.2.0)
**Problem**: Local web dashboard (2.3) only works on the machine running the engine. Pro tier users need browser access from anywhere.

- Deploy the same FastAPI dashboard app (from 2.3) to cloud
- Cloud-backed data via sync layer (6.1)
- Multi-project fleet view across all synced projects
- Historical metrics charts (data persisted in PostgreSQL, not just in-memory)
- User auth: OAuth login for team access
- Full architecture in `memory-engine-gui-686f76.md` Phase 1 (same UI, cloud-hosted)

**Files**: `src/dashboard/` (existing, deployed to cloud), `src/cloud/dashboard.py`

> 📊 Dashboard: New login page (OAuth); Dashboard — historical metrics charts; Fleet — multi-project across synced projects; Settings — user/team management

### 6.4 Tauri Desktop Wrapper (v2.2.1)
**Problem**: Web dashboard requires a browser tab. Desktop users want a native app feel.

- Tauri 2.x wraps the web dashboard in a native desktop window (~10MB binary)
- System tray icon: Open Dashboard, Run Consolidation, View Alerts, Quit
- OS-native notifications for alerts (not just in-browser toasts)
- Auto-start option, dark/light theme following system preference
- Offline indicator when cloud sync unavailable
- Packaging: Windows `.msi`, macOS `.dmg`, Linux `.AppImage`
- Full architecture in `memory-engine-gui-686f76.md` Phase 2

**Files**: new `src/desktop/` (Tauri project)

> 📊 Dashboard: Theme toggle (dark/light); OS-native notifications; system tray — no content changes

---

## Phase 7 — Advanced Memory (v2.3 – v2.5)

**Goal**: Research-grade features that differentiate from competitors.

### 7.1 Hierarchical Memory Levels (v2.3.0)
**Problem**: All entries at same abstraction level. No way to go from specific bugs to general principles.

- Three levels: `L0` (raw episodic), `L1` (sprint summaries), `L2` (project invariants)
- L0→L1: During consolidation, cluster entries by component/domain, generate summary entries via local LLM
- L1→L2: High-reinforcement promotion candidates become invariants
- Retrieval searches all levels, weighted by level (L2 > L1 > L0 for relevance, L0 > L1 > L2 for specificity)
- `memory_level` field on entries

**Files**: `consolidation.py`, `retrieval.py`, `validation.py`, `constants.py`

> 📊 Dashboard: Learnings — level filter (L0/L1/L2); Retrieval Explorer — level-weighted results; new "Memory Hierarchy" view

### 7.2 Adaptive Retrieval (v2.4.0)
**Problem**: Every retrieval uses the same strategy regardless of query type.

- Query type detection: temporal, component-specific, domain-broad, conflict-check
- Route to appropriate index: temporal → step-sorted, component → component-indexed, domain → domain-filtered
- Query-aware tag routing: map query to semantic tags, search only entries under those tags
- Reduces retrieval from O(n) to O(k log n) where k << n
- Inspired by SwiftMem's query-aware DAG-tag indexing

**Files**: `retrieval.py`, `storage.py`, new `src/engine/router.py`

> 📊 Dashboard: Retrieval Explorer — show detected query type + routing; Settings — adaptive retrieval toggle

### 7.3 Background Consolidation / "Dreaming" (v2.5.0)
**Problem**: Consolidation is manual. Memory grows stale between Sleep Cycles.

- Background process (configurable interval): re-evaluate entry relevance, merge similar entries, prune low-value entries
- LLM-assisted merge (local model): "Given these 3 similar learnings, write a single consolidated rule"
- Writes to `dreaming.jsonl` audit trail before modifying `learnings.jsonl`
- User controls: `dreaming_mode: "off" | "suggest" | "auto"`, default "suggest"
- In "suggest" mode: generates proposals, user approves via web dashboard (2.3) or CLI
- In "auto" mode: applies low-risk merges (similarity > 0.9, same severity), quarantines uncertain ones
- Inspired by OpenAI Dreaming V3

**Files**: new `src/engine/dreaming.py`, `consolidation.py`

> 📊 Dashboard: New "Dreaming" tab/sub-tab in Consolidation Console — proposals with approve/reject; WebSocket — dreaming events; Settings — mode toggle

### 7.4 Multi-Modal Memory (v2.5.1)
**Problem**: Only text-based learnings. Can't store code snippets, diffs, or image references.

- `content_type: "text" | "code" | "image_ref" | "diff"` field
- Code entries: executable snippets with language tag
- Image entries: path to screenshot + description
- Diff entries: before/after code with explanation
- Retrieval formats output based on content type
- Code entries can be executed as sanity checks during consolidation

**Files**: `validation.py`, `retrieval.py`, `handlers.py`, `constants.py`

> 📊 Dashboard: Learnings — content type filter + specialized rendering (code/diff/image); Retrieval Explorer — format by content type

### 7.5 Textual TUI Dashboard (v2.3.1)
**Problem**: Web dashboard requires a browser. Terminal users need inline exploration.

- Rich terminal UI using [Textual](https://textual.textualize.io/) framework
- Mirrors web dashboard tab structure: Dashboard, Learnings, Retrieval, Metrics, Consolidation, Fleet
- CSS-like styling, rich text rendering, mouse support, responsive layout
- Connects to the FastAPI API server (2.1) if running, otherwise starts it in background
- Full keyboard navigation (vim-style: j/k scroll, enter select, q quit)
- `agent-memory tui` or `python memory/filter.py --tui`
- Full architecture in `memory-engine-gui-686f76.md` Phase 3

**Files**: new `src/tui/app.py`, `src/tui/views/` (one file per view)

---

## Phase 8 — Production Hardening (v2.6 – v3.0)

**Goal**: Ship a reliable, extensible, well-tested product.

### 8.1 Concurrency & File Locking (v2.6.0)
**Problem**: I/O has no locking. Comment in `handlers.py:197` acknowledges this.

- File locking: `fcntl` (Unix) / `msvcrt` (Windows) with context manager
- Lock granularity: per-file, 5s timeout (configurable)
- Acquire before read-modify-write cycles in `handle_log`, `handle_update`, `handle_resolve`
- SQLite backend uses WAL mode for concurrent reads

**Files**: `io.py`, `handlers.py`, `storage.py`

### 8.2 Schema Versioning & Migrations (v2.6.1)
**Problem**: No schema version field. Adding fields requires manual migration.

- `schema_version` on every entry (default: 1 for current format)
- Migration registry: `{1: migrate_v1_to_v2, 2: migrate_v2_to_v3, ...}`
- Auto-migrate on load: add missing fields with defaults
- `--migrate-schema` command: preview and apply migrations
- Version in `config.json` as `schema_version`

**Files**: new `src/engine/migrations.py`, `io.py`, `validation.py`

> 📊 Dashboard: Settings — schema version display + migration preview/apply

### 8.3 Backup & Recovery (v2.6.2)
**Problem**: No general-purpose backup/restore for data files.

- `--backup` command: snapshot all memory files to `memory/backups/<timestamp>/`
- `--restore <backup-dir>`: restore from backup
- Auto-backup before consolidation (`--consolidate --no-backup` to skip)
- Retention: keep last N backups (default 10), prune older
- Verify: checksum + test load

**Files**: new `src/engine/backup.py`, `filter.py`

> 📊 Dashboard: Settings — backup/restore buttons + history + auto-backup toggle

### 8.4 Plugin Architecture (v2.7.0)
**Problem**: All logic hardcoded. Can't extend without modifying engine source.

- `MemoryPlugin` interface with hooks: `pre_log`, `post_log`, `pre_retrieve`, `post_retrieve`, `pre_consolidate`, `post_consolidate`
- Plugins loaded from `memory/plugins/` directory (Python files)
- Built-in plugins: `embedding_plugin`, `graph_plugin`, `alert_plugin`, `dreaming_plugin`
- Plugin config: `"plugins": ["embeddings", "graph", "alerts"]` in `config.json`
- Third-party plugins via pip: `pip install agent-memory-graph-plugin`

**Files**: new `src/engine/plugins.py`, `filter.py`, `config.json`

> 📊 Dashboard: New "Plugins" tab or Settings sub-tab — enable/disable, config, installed list

### 8.5 Advanced Metrics & Analytics System (v2.7.1)
**Problem**: Metrics are raw event logs. No derived insights, health scoring, or alerts.

- Comprehensive metrics overhaul per `advanced-metrics-system-686f76.md`
- **Data harvesting** (Phase 1): learnings snapshot, enriched retrieval/logging instrumentation, quarantine deep harvest, archive harvest
- **Analysis engine** (Phase 2): memory health score (0-100), retrieval quality analysis, entry lifecycle tracking, agent quality scoring, dedup & conflict analysis, consolidation effectiveness, cross-project comparative analysis
- **Alerting** (Phase 3): threshold-based alerts with configurable thresholds in `config.json`, smart recommendations engine, config tuning suggestions
- **Output formats** (Phase 4): CSV/HTML/markdown export, ASCII visualization (sparklines, bar charts, heat maps), CLI dashboard mode
- **Cross-project hub** (Phase 5): global metrics aggregation, project interaction analysis, fleet report
- New CLI flags: `--metrics-snapshot`, `--metrics-health`, `--metrics-alerts`, `--metrics-retrieval-quality`, `--metrics-lifecycle`, `--metrics-quarantine`, `--metrics-archive`, `--metrics-dedup`, `--metrics-consolidation-quality`, `--metrics-agents`, `--metrics-config-tuning`, `--metrics-recommendations`, `--metrics-dashboard`, `--metrics-fleet`
- Pro tier: metrics pushed to cloud dashboard with historical charts (via 6.3)
- GUI integration: all analysis functions exposed via `/api/metrics/*` endpoints in the dashboard (2.3)

**Files**: `metrics.py`, `constants.py`, `config.json`, new `src/engine/snapshot.py`, `src/engine/health.py`, `src/engine/analysis.py`, `src/engine/recommendations.py`, `src/engine/visualize.py`, `src/engine/export.py`

> 📊 Dashboard: Metrics Deep-Dive — all sub-tabs enriched; Dashboard — real health score; Fleet — full cross-project analysis. **Biggest single dashboard update**

### 8.6 Structured Error Handling (v2.8.0)
**Problem**: Errors are unparseable strings. Hard to handle programmatically.

- Error codes + suggested actions in exceptions
- `MemoryError` base class with subclasses: `ValidationError`, `StorageError`, `SyncError`, `QuarantineError`
- Each error includes: `code`, `message`, `suggested_action`, `entry_ref`
- SDK exposes typed exceptions for programmatic handling

**Files**: new `src/engine/exceptions.py`, all engine modules

> 📊 Dashboard: Dashboard — error toasts show code + suggested_action

### 8.7 Multi-Tenant Architecture (v3.0.0)
**Problem**: Cloud sync needs tenant isolation for Pro tier.

- `user_id`, `team_id`, `app_id` scope fields on entries (Mem0's multi-scope model)
- Retrieval scoping: `--scope user`, `--scope team`, `--scope app`, composable
- Cloud service: PostgreSQL with row-level security per tenant
- Rate limiting per tier (free: 1000 entries, pro: unlimited)
- Data residency: choose region for cloud sync

**Files**: `validation.py`, `retrieval.py`, `sync.py`, new `src/cloud/tenant.py`

> 📊 Dashboard: New auth UI; Dashboard header — scope selector; Learnings — scope filter; Settings — tenant management; Fleet — cross-tenant view (admin)

---

## Priority Matrix (Product-First Ordering)

| Phase | Effort | Impact | Revenue | Dependencies | Order |
|-------|--------|--------|---------|-------------|-------|
| 1.1 BM25 | Low | High | Free tier quality | None | 1st |
| 1.2 Embedding Retrieval | Medium | High | Free tier quality | Schema versioning | 2nd |
| 1.3 Embedding Dedup | Medium | High | Free tier quality | 1.2 | 3rd |
| 2.1 HTTP API | Medium | High | Distribution | None | 4th |
| 2.2 MCP Server | Medium | Critical | Distribution channel | 2.1 | 5th |
| 2.3 Web Dashboard | Medium | Critical | Free tier UX | 2.1 | 6th |
| 2.5 pip Packaging | Medium | Critical | Distribution | None | 7th |
| 1.4 Reranking | Low | Medium | Free tier quality | 1.2 | 8th |
| 1.5 Grading Harness | Low | High | Quality measurement | None | 9th |
| 2.4 Python SDK | Medium | High | Developer adoption | 2.1 | 10th |
| 3.1 SQLite Backend | Medium | High | Scalability | None | 11th |
| 4.1 Spaced Repetition | Medium | High | Differentiation | None | 12th |
| 4.2 Bi-Temporal | Low | Medium | Differentiation | None | 13th |
| 4.3 Entity Extraction | Medium | High | Differentiation | None | 14th |
| 4.4 Memory Link Graph | Medium | High | Differentiation | 4.3 | 15th |
| 5.1 Procedural Memory | Medium | High | Differentiation | None | 16th |
| 3.2 Incremental Index | Medium | High | Scalability | 3.1 | 17th |
| 5.2 Actor-Aware | Low | Medium | Multi-agent | None | 18th |
| 3.3 Auto-Archive | Low | Medium | UX | None | 19th |
| 5.4 Cross-Project Transfer | Medium | High | Multi-agent | 1.3 | 20th |
| 6.1 Cloud Sync | High | Critical | **Pro revenue** | 3.1 | 21st |
| 6.2 Team Shared | Medium | High | **Pro revenue** | 6.1, 5.4 | 22nd |
| 6.3 Cloud Dashboard | Medium | High | **Pro revenue** | 6.1, 2.3 | 23rd |
| 6.4 Tauri Desktop | Medium | Medium | Pro tier UX | 2.3 | 24th |
| 4.5 Cue-Tag-Content Graph | High | High | Advanced diff | 4.4 | 25th |
| 4.6 Graph Consolidation | Medium | Medium | Maintenance | 4.4 | 26th |
| 5.3 Auto Conflict Resolution | Medium | High | UX | 4.2 | 27th |
| 7.1 Hierarchical Memory | High | High | Advanced diff | 3.1 | 28th |
| 7.5 Textual TUI | Medium | Low | UX | 2.1 | 29th |
| 7.2 Adaptive Retrieval | High | High | Performance | 3.1, 4.5 | 30th |
| 7.3 Dreaming | High | High | Advanced diff | 4.1, 5.3 | 31st |
| 7.4 Multi-Modal | High | Low | Niche | None | 32nd |
| 8.1 File Locking | Low | Essential | Reliability | None | 33rd |
| 8.2 Schema Versioning | Medium | Essential | Reliability | None | 34th |
| 8.3 Backup & Recovery | Low | Medium | Reliability | None | 35th |
| 8.4 Plugin Architecture | High | Medium | Extensibility | None | 36th |
| 8.5 Advanced Metrics | Medium | High | Observability | None | 37th |
| 8.6 Structured Errors | Medium | Medium | DX | None | 38th |
| 8.7 Multi-Tenant | High | Critical | **Pro scale** | 6.1 | 39th |

---

## Version Milestones

| Version | Theme | Ships With | Revenue Impact |
|---------|-------|-----------|----------------|
| v1.17 | BM25 retrieval | 1.1 | Free tier quality |
| v1.18 | Semantic retrieval | 1.2 + schema versioning | Free tier quality |
| v1.19 | Dedup + reranking + grading | 1.3, 1.4, 1.5 | Free tier quality |
| v1.20 | API + MCP + web dashboard | 2.1, 2.2, 2.3 | **Distribution channel** |
| v1.21 | SDK + pip | 2.4, 2.5 | Developer adoption |
| v1.22 | SQLite | 3.1, 3.2 | Scalability |
| v1.23 | Auto-archive | 3.3 | UX |
| v1.24 | Spaced repetition + bi-temporal | 4.1, 4.2 | Differentiation |
| v1.25 | Entity + graph | 4.3, 4.4 | Differentiation |
| v1.26 | Cue-tag graph | 4.5, 4.6 | Advanced |
| v1.27 | Procedural + actor-aware | 5.1, 5.2 | Multi-agent |
| v1.28 | Auto conflict + cross-project | 5.3, 5.4 | UX + multi-agent |
| **v2.0** | **Cloud sync** | **6.1** | **Pro tier launch** |
| v2.1 | Team sharing | 6.2 | Pro revenue |
| v2.2 | Cloud dashboard + Tauri | 6.3, 6.4 | Pro revenue |
| v2.3 | Hierarchical memory + TUI | 7.1, 7.5 | Advanced |
| v2.4 | Adaptive retrieval | 7.2 | Performance |
| v2.5 | Dreaming + multi-modal | 7.3, 7.4 | Advanced |
| v2.6 | Hardening | 8.1, 8.2, 8.3 | Reliability |
| v2.7 | Plugins + advanced metrics | 8.4, 8.5 | Extensibility |
| v2.8 | Error handling | 8.6 | DX |
| **v3.0** | **Multi-tenant** | **8.7** | **Pro scale** |

---

## Competitive Differentiation

| Feature | Agent Memory Engine | Mem0 | Zep/Graphiti | Letta | A-Mem |
|---------|---------------------|------|-------------|-------|-------|
| Local-first | ✅ | ❌ (cloud) | ❌ (cloud) | ✅ | ✅ (research) |
| Privacy (no cloud required) | ✅ | ❌ | ❌ | ✅ | ✅ |
| MCP compatible | ✅ (Phase 2) | ✅ | ❌ | ❌ | ❌ |
| CLI + SDK | ✅ | ✅ | ✅ | ✅ | ❌ |
| Episodic + procedural | ✅ (Phase 5) | ✅ | ✅ | ✅ | ✅ |
| Graph linking | ✅ (Phase 4) | ✅ (v3) | ✅ | ❌ | ✅ |
| Bi-temporal | ✅ (Phase 4) | ❌ | ✅ | ❌ | ❌ |
| Spaced repetition | ✅ (Phase 4) | ❌ | ❌ | ❌ | ❌ |
| Hierarchical levels | ✅ (Phase 7) | ❌ | ❌ | ❌ | ❌ |
| Background consolidation | ✅ (Phase 7) | ✅ | ✅ | ❌ | ❌ |
| Plugin architecture | ✅ (Phase 8) | ❌ | ❌ | ❌ | ❌ |
| Open source free tier | ✅ | ✅ (OSS) | ✅ (OSS) | ✅ (OSS) | ✅ (research) |
| Self-hosted Pro | ✅ (future) | ❌ | ❌ | ❌ | ❌ |

**Key differentiators**: Local-first privacy, spaced repetition, hierarchical memory, MCP-native, plugin architecture.

---

## Research References

- **AdMem** (arXiv:2606.06787) — Bi-level short/long-term memory, procedural memory type, reward-based pruning
- **MRAgent** (arXiv:2606.06036) — Cue-Tag-Content graph, active reconstruction, +23% on LoCoMo
- **OpenAI Dreaming V3** — Background memory synthesis, staleness handling, automatic curation
- **Mem0 v3** — Entity linking, multi-scope memory (user/agent/run/app), actor-aware attribution, async default, reranking, procedural memory
- **Zep / Graphiti** — Bi-temporal knowledge graph, fact validity windows, entity resolution
- **A-Mem** (arXiv:2502.12110) — Zettelkasten linked notes, dynamic memory evolution
- **SwiftMem** (arXiv:2601.08160) — Query-aware DAG-tag indexing, co-consolidation, sub-linear retrieval
- **E-mem** (arXiv:2601.21714) — Episodic context reconstruction, hierarchical architecture
- **GAAMA** (arXiv:2603.27910) — Graph-augmented associative memory, edge-type-aware PPR
- **Engram** (arXiv:2606.09900) — Bi-temporal graph, hybrid facts+chunks, conflict resolution policy
- **MemMachine** (arXiv:2604.04853) — Ground-truth-preserving, contextualized retrieval, multi-hop agent
- **Hierarchical Memory Theory** (arXiv:2603.21564) — (α,C,τ) decomposition, information monotonicity
- **Ebbinghaus / SM-2** — Forgetting curve, spaced repetition scheduling
