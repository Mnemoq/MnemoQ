# Roadmap

## Shipped (v1.17 – v1.22)

**Retrieval Quality (v1.17 – v1.19)**

- [x] BM25 lexical scoring + Reciprocal Rank Fusion (RRF)
- [x] Embedding-based retrieval (`sentence-transformers`, hybrid scoring)
- [x] Embedding-based semantic dedup (cosine ≥ 0.85 → merge)
- [x] Optional reranking pass (cross-encoder, LLM-local)
- [x] Grading harness (`--eval`)

**API, MCP & Dashboard (v1.20)**

- [x] CLI for logging, retrieving, and resolving learnings
- [x] HTTP API server (FastAPI, `--serve`)
- [x] MCP server (`mnemoq-mcp`)
- [x] Local web dashboard (`--dashboard`)
- [x] Consolidation (archive + promote)
- [x] Profile, agents review, migrate commands
- [x] Scaffold + update tooling

**SDK & Distribution (v1.21)**

- [x] Python SDK (`MemoryClient` / `AsyncMemoryClient`) with local + HTTP transports
- [x] pip packaging with console entry points (`mnemoq`, `mnemoq-mcp`, `mnemoq-scaffold`, `mnemoq-update`)

**1.22.x Hardening Line** — depth/quality polish to existing features before the SQLite store (which shifts to v1.23+)

- [x] v1.22.0 — Trustworthy grading harness: in-process eval (no per-fixture subprocess), `--match exact|fuzzy|semantic|auto`, MRR/nDCG + per-fixture diagnostics, `--eval-json`

## Tier 3 — Scalability & Intelligence (v1.22 – v1.28)

### Phase 3 — Storage & Scalability (v1.22 – v1.23)

**v1.22 — SQLite Storage Backend**

- [ ] `sqlite3` backend (stdlib, zero dependency) as alternative to JSONL
- [ ] FTS5 full-text search index on trigger + action + reason for BM25 first-stage filtering
- [ ] Migration command: `--migrate-to-sqlite` reads JSONL, writes DB
- [ ] Config: `storage_backend: "jsonl" | "sqlite"` (default: jsonl for backwards compat)
- [ ] JSONL remains as export/backup format; SQLite is the working store

**v1.22.1 — Incremental Index Updates**

- [ ] On `--log`, insert into SQLite + FTS5 index incrementally
- [ ] Retrieval: FTS5 BM25 ranking for first-stage filter → tiered scoring on top-N
- [ ] Reduces retrieval from O(n) to O(log n + k)
- [ ] Embedding similarity computed only on top-N candidates

**v1.23 — Auto-Archive**

- [ ] Config: `auto_archive_threshold: 200` (0 = disabled)
- [ ] When entry count exceeds threshold, auto-archive oldest resolved entries
- [ ] Prompt user to run full `--consolidate` when unresolved count > 50
- [ ] Archive entries moved to `archive/auto-N.jsonl` with metadata

### Phase 4 — Memory Intelligence (v1.24 – v1.26)

**v1.24 — Spaced Repetition Scheduling**

- [ ] SM-2 algorithm: each entry gets `ease_factor` (default 2.5) and `next_review_step`
- [ ] On retrieval hit: update ease factor, schedule next review
- [ ] On consolidation: surface entries due for review in promotion report
- [ ] Entries not accessed by `next_review_step` get accelerated decay
- [ ] Mirrors Ebbinghaus forgetting curve + OpenAI Dreaming's staleness handling

**v1.24.1 — Bi-Temporal Timestamps**

- [ ] Add `valid_from` (when learning became true) and `valid_until` (when superseded, default ∞)
- [ ] On conflict resolution: set old entry's `valid_until = now`, new entry's `valid_from = now`
- [ ] Retrieval supports `--as-of <step>` for historical state queries
- [ ] Enables temporal debugging: "what did we know at step 15?"

**v1.25 — Entity Extraction & Linking**

- [ ] Extract entities from trigger/action/reason text on `--log`
- [ ] Store in `entities` field: normalized names, types (class, file, concept)
- [ ] Entity normalization: lowercase, strip suffixes (Entity, Component, Manager), Levenshtein ≤2 fuzzy match
- [ ] Retrieval matches on entity overlap in addition to component overlap

**v1.25.1 — Memory Link Graph**

- [ ] On `--log`, compute links to existing entries: shared components, shared files, entity overlap, semantic similarity > 0.5
- [ ] Store links in SQLite link table: `(source_id, target_id, link_type, weight)`
- [ ] Link types: `same_component`, `same_file`, `semantic_similar`, `contradicts`, `supersedes`
- [ ] On retrieval, expand results: for each hit, pull top-2 linked entries (configurable `link_expansion: 2`)
- [ ] Inspired by A-Mem's Zettelkasten links and GAAMA's graph-augmented retrieval

**v1.26 — Cue-Tag-Content Graph**

- [ ] Build lightweight graph: `Cue` (token) → `Tag` (concept/category) → `Content` (entry)
- [ ] Tags derived from domain + component clustering
- [ ] Retrieval traverses: query cues → find tags → find content via tags
- [ ] In-memory dict-of-lists, rebuilt on load (fast for <10k entries)
- [ ] Enables multi-hop: "What did we learn about physics that also affects rendering?"

**v1.26.1 — Graph Consolidation**

- [ ] During Sleep Cycle: merge tags with >0.8 Jaccard overlap
- [ ] Prune links below weight threshold
- [ ] Rebuild layout for cache locality (if using SQLite)
- [ ] Report graph stats: node count, edge count, density, orphans
- [ ] Inspired by SwiftMem's co-consolidation

### Phase 5 — Procedural Memory & Multi-Agent (v1.27 – v1.28)

**v1.27 — Procedural Memory Type**

- [ ] New entry type: `procedure` (alongside `bug_fix`, `optimization`, `architectural_pattern`)
- [ ] Additional fields: `preconditions`, `steps` (ordered list), `expected_outcome`
- [ ] Retrieval: when task matches preconditions, surface as "Suggested Procedure"
- [ ] Track `success_count` / `failure_count` when referenced
- [ ] Low success rate → flag for review in consolidation

**v1.27.1 — Actor-Aware Attribution**

- [ ] Add `observed_by` (list) and `verified_by` (list) fields
- [ ] Retrieval filter: `--trusted-agents gm,code-reviewer` surfaces only entries from those agents
- [ ] Metrics track per-agent contribution quality (quarantine rate, conflict rate, promotion rate)
- [ ] Inspired by Mem0's actor-aware memory

**v1.28 — Auto Conflict Resolution**

- [ ] When `actions_oppose` triggers at 0.4–0.7 similarity, auto-create proposed supersession entry
- [ ] LLM-assisted (local model): generate reason explaining why old rule no longer applies
- [ ] Require `--confirm` flag to apply, or auto-apply if `auto_resolve_conflicts: true` in config
- [ ] Set old entry's `valid_until`, new entry's `valid_from` (uses bi-temporal from v1.24.1)
- [ ] Human gate preserved: default is propose-only

**v1.28.1 — Cross-Project Learning Transfer**

- [ ] `--export --filter type=architectural_pattern,severity=major` → curated JSONL export
- [ ] `--import <file>` → validates, dedups (using embedding dedup), merges with project-specific stamping
- [ ] `--promote <ts>` → promote local learning to shared global store (`~/.agent-memory/shared-learnings.jsonl`)
- [ ] During consolidation, suggest cross-project candidates: entries with high access_count and `scope: system`
- [ ] Auto-promotion suggestions during Sleep Cycle: project-agnostic entries with ≥2 reinforcements

## Beyond Tier 3 — Future Phases

### Phase 6 — Cloud Sync Layer / Pro Tier (v2.0 – v2.2)

- [ ] Sync architecture: offline-first local store with cloud sync layer
- [ ] Team shared learnings with cloud-backed sync
- [ ] Cloud-hosted dashboard (same UI, cloud-backed data)
- [ ] Tauri desktop wrapper (Windows `.msi`, macOS `.dmg`, Linux `.AppImage`)

### Phase 7 — Advanced Memory (v2.3 – v2.5)

- [ ] Hierarchical memory levels (L0 raw → L1 sprint summaries → L2 project invariants)
- [ ] Adaptive retrieval: query-type detection + routing to appropriate index
- [ ] Background consolidation / "Dreaming": LLM-assisted merge, suggest/auto modes
- [ ] Multi-modal memory: code snippets, diffs, image references
- [ ] Textual TUI dashboard (vim-style keyboard navigation)

### Phase 8 — Production Hardening (v2.6 – v3.0)

- [ ] Concurrency & file locking (`fcntl` / `msvcrt`)
- [ ] Schema versioning & migration registry
- [ ] Backup & recovery (`--backup` / `--restore`)
- [ ] Plugin architecture (`MemoryPlugin` interface with lifecycle hooks)
- [ ] Advanced metrics & analytics system (health scores, alerting, recommendations)
- [ ] Structured error handling (error codes + suggested actions)
- [ ] Multi-tenant architecture (user/team/app scope, row-level security)

## Pro Tier (Separate Private Repo)

- [ ] Cloud sync server
- [ ] Multi-tenant layer (team/org management)
- [ ] Hosted dashboard backend
- [ ] Billing (subscription + usage metering)

See [open-core-architecture.md](open-core-architecture.md) for the full module boundary.
