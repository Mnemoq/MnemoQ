# Agent Memory Engine — Development Plan

**Current version:** 1.15.0
**Last updated:** 2026-06-18
**Plan revision:** 2
**Scale assumption:** solo developer, 4 active projects (`magpie Swoop`, `Automo`, `Integrated AI UI`, `PixelPurge`).

---

## Vision

A self-improving episodic memory system for AI agents that learns from mistakes, consolidates knowledge across sessions, and shares insights across projects — with zero per-project configuration overhead.

---

## Operating Principles

- **JSONL is the contract.** `learnings.jsonl` schema is the stable boundary. Every architectural change must preserve it.
- **One real measurement beats three guessed metrics.** Don't claim coverage/relevance numbers without instruments to produce them.
- **Solo + 4 projects.** Defer anything that only pays off at team scale or 10+ projects.
- **Append-only data.** No git-like merging of learnings. Migrations transform in place; backups protect rollback.
- **Privacy-first.** All data stays local. No cloud sync, no external embedding APIs.

---

## Phase 1 — Foundation (v1.16 – v1.18)

**Goal:** End per-project file copies, give the engine real test/version instrumentation, and prepare for schema evolution. This phase removes the maintenance burden that's blocking everything else.

### 1.1 Hygiene baseline (v1.16.0)
Cheap, must-do-first cleanup.
- Prune `projects.txt`: remove stale `Temp\tmp*` entries; add a guard in `update.py` that skips paths under the system temp dir with a warning.
- Centralize the fallback version constant. Today `"1.15.0"` is hardcoded in `filter.py`, `update.py`, and `scaffold.py`. Move to a single helper (e.g., `engine_version.py`) imported by all three.
- Add `pytest-cov` to `pyproject.toml`. Run once, commit a `coverage-baseline.txt` so future targets are real numbers.
- `deploy.ps1` already runs `pytest`; extend it to fail the deploy on coverage regression > 5 points (after a baseline exists).

**Done when:** `projects.txt` lists only the 4 real projects, no project skips on a fresh `update.py` run, `pytest --cov` produces a baseline, version-bump touches one file.

### 1.2 Resolver refactor (v1.16.1)
Prepare `filter.py` for the shim without changing deployment yet.
- Introduce `resolve_memory_dir()` returning, in order: `--memory-dir` flag, `AGENT_MEMORY_DIR` env, `<cwd>/memory/`.
- Replace module-level globals (`MEMORY_DIR`, `CONFIG_PATH`, `LEARNINGS_PATH`, `QUARANTINE_PATH`, `ARCHIVE_DIR`, `SESSION_FILE`, `AGENTS_MD_PATH`) with a `Paths` dataclass returned by `setup_paths()`.
- Keep `update.py` copying full files for now. No external behavior change.

**Done when:** all 4 real projects pass `python memory/filter.py --stats` and the test suite is green.

### 1.3 Shim cutover (v1.17.0)
The deployment-model change.
- `scaffold.py` writes a ~12-line shim that sets `AGENT_MEMORY_DIR` and execs `~/.agent-memory/engine/filter.py`.
- `update.py --migrate-to-shim` replaces existing per-project `filter.py`/`profile.py` copies with shims (one project at a time, with backup).
- Drop the regex-based static-version rewrite in `update.py`; the shim reads the live `VERSION` file.
- Drop `clear_pycache` for shim-only projects (no compiled artifact in `memory/`).
- `verify_update` runs `python memory/filter.py --stats` against the shim path.

**Rollout:** canary one project (`PixelPurge`), validate for one work session, then migrate the others.

**Done when:** editing `src/filter.py` is reflected in all 4 projects on next invocation, no project carries a copy of `filter.py`.

### 1.4 Modularize `filter.py` (v1.17.1)
Now safe to split, since there's only one copy. Detailed plan: `.opencode/Plans/refactor-filter-py-safety-first.md`.
- `engine/retrieval.py` — scoring, filtering.
- `engine/validation.py` — schema validation, dedup, conflict detection.
- `engine/handlers.py` — orchestrators (`handle_log`, `handle_update`, `handle_resolve`, `handle_stats`).
- `engine/consolidation.py` — Sleep Cycle, promotion.
- `engine/agents_review.py` — AGENTS.md health.
- `engine/io.py` — file I/O, atomic writes, quarantine.
- `engine/git_utils.py` — `stamp_entry` + `check_staleness` (all git subprocess calls).
- `engine/constants.py` — default constant values + schema sets.
- `engine/profile.py` — developer profile loader (moved from `src/profile.py`).
- `engine/__init__.py` — empty.
- `filter.py` — CLI entry point only (arg parsing + `ctx`/`paths` construction + dispatch).

**Done when:** every module < 400 lines, all existing tests pass unchanged, `--stats`/`--log`/`--consolidate`/`--review-agents` outputs are byte-identical to v1.17.0, and no `globals().update(config)` remains in `filter.py`.

### 1.5 Atomic writes for `learnings.jsonl` (v1.17.2)
The one concurrency concession worth making at solo scale.
- Replace direct append with `tempfile + os.replace` write-rewrite for `learnings.jsonl` and `quarantine.jsonl`.
- Add an integration test: kill the writer mid-write (subprocess + signal), assert the file is still valid JSONL.

**Done when:** Ctrl-C during `--log` cannot leave a half-written entry.

### 1.6 Schema versioning + migration runner (v1.18.0)
Unblock every future schema change.
- Add `schema_version: 1` field to every learning entry on next write. One-shot backfill migration adds it to existing entries.
- `engine/migrate.py` with a registry of `(from_version, to_version, transform_fn)`.
- `update.py` runs pending migrations after an engine version bump (after backup, before verify).
- Per-entry version means migrations are idempotent and skip-safe.

**Done when:** can ship a schema change with one new transform function and have it land across all 4 projects via `deploy.ps1`.

### 1.7 Stats extension (v1.18.1)
Skip the dashboard. Just teach `--stats` four numbers.
- Learning velocity (entries per sprint, last 4 sprints).
- Retrieval hit rate (% of `--step` invocations that returned ≥1 warning/pattern).
- Quarantine rate over time.
- Promotion rate (% of learnings consolidated into invariants).
- Output stays human-readable; add `--stats --json` for machine consumption.

**Done when:** `--stats` shows trend numbers without a separate dashboard process.

---

## Phase 2 — Retrieval Intelligence (v1.19 – v2.0)

**Goal:** Make retrieval actually semantic. This is the only phase that visibly improves the agent-facing product.

### 2.1 Embedding-based retrieval (v1.19.0)
- Pick a local model: `sentence-transformers` `all-MiniLM-L6-v2` (~90MB). No API dependency.
- Lazy download on first use; cache under `~/.agent-memory/models/`.
- Generate an embedding for `trigger + action + description` at `--log` time. Store as a base64 float16 array on the entry. Migration backfills existing entries (Phase 1.6 makes this trivial).
- Retrieval embeds the task context once, computes cosine vs. all candidates after the cheap keyword pre-filter.
- Graceful degradation: if model isn't available, skip embedding scores entirely.

**Done when:** retrieval returns the right learning for paraphrased queries that today miss on keyword overlap.

### 2.2 Hybrid scoring (v1.19.1)
- `final = alpha * keyword_score + (1 - alpha) * embedding_score`.
- `alpha` per-project in `config.json`, default `0.5`. Auto-`1.0` when no embeddings are available.
- Ship a tiny grading harness: a fixture of (task, expected-learning-id) pairs per project; report top-1 / top-3 hit rate. This becomes the real "retrieval relevance" metric and replaces the made-up 60→80% target.

**Done when:** harness shows hybrid > keyword on at least one project's fixture.

### 2.3 Embedding-based dedup (v2.0.0)
- At `--log` time, run cosine vs. recent same-domain entries (threshold 0.85) before the existing Jaccard check.
- On match, merge: combine `access_count`, keep the entry with richer description, append the dup's source agent to a `contributors` list.
- Quarantine the rejected entry with reason `semantic_duplicate`.

**Done when:** "same lesson, different wording" no longer creates two entries.

---

## Phase 3 — Cross-Project Sharing (v2.1 – v2.3)

**Goal:** A learning logged in `PixelPurge` should help `magpie Swoop` when it's genuinely universal — without manual file shuffling.

### 3.1 Provenance (v2.1.0) — must come first
The cheapest item, and it unblocks 3.2 and 3.3.
- Add `project_id` (string slug from `config.json::project_name`) to every learning entry on write. Migration backfills.
- Add `origin_project` (set once, immutable) and `contributing_projects` (list, grows on cross-project import).
- `--lineage <ts>` shows: where it came from, which projects use it, when it was promoted.

**Done when:** every entry knows its origin and any entry can be traced through merges.

### 3.2 Global learning store (v2.1.1)
- New file: `~/.agent-memory/global-learnings.jsonl`. Same schema as project learnings.
- Retrieval merges `local + global`; on a tie, local wins.
- Domains are validated against the union of universal + project-extra (extends current Phaser-biased enum). `config.json` gains an optional `extra_domains: []`.

**Done when:** a learning written to the global store appears in retrieval across all 4 projects with no per-project change.

### 3.3 Export / import (v2.2.0)
The thin manual interface to the global store.
- `filter.py --export --domain X --min-importance N --out path.jsonl`.
- `filter.py --import path.jsonl` validates, dedups (using Phase 2.3's embedding dedup), and merges.
- `--import --to-global` writes into `~/.agent-memory/global-learnings.jsonl` instead of project local.

**Done when:** can hand-curate cross-project learnings between sessions without touching files directly.

### 3.4 Auto-promotion suggestions (v2.3.0)
The heuristic, last because it needs real data to tune.
- During Sleep Cycle, flag learnings that look project-agnostic: no project-specific file paths in `affected_files`, no project-specific component names, ≥2 reinforcement events.
- Print a "promotion candidates" section in the consolidation report. Never auto-write to global; always require `filter.py --promote <ts>` from the developer.
- Track decline reasons over time to refine the heuristic.

**Done when:** the consolidation report surfaces real promotion candidates and false positives are rare enough to skim.

---

## Anti-Goals

1. **Cloud-hosted memory.** Privacy-first; all data stays local.
2. **Automatic learning generation.** Agents must explicitly log; no background magic.
3. **Natural-language CLI queries.** CLI-first; NLP layer is complexity without payoff at this scale.
4. **Multi-developer / team sync.** Single-user system. Reconsider only if a teammate joins.
5. **Concurrent multi-agent locking, trust scores, consensus.** Agents run sequentially under one orchestrator. Atomic writes (Phase 1.5) are sufficient.
6. **Dashboards, IDE plugins, CI memory checks, `pip` packaging.** All deferred until something on the critical path actually demands them.
7. **Branching/merging of learnings.** Append-only. Disagreement is resolved by quarantine + human review, not version control.

---

## Decision Log

Add an entry only when a decision is *changed* or *re-affirmed under pressure*. Don't duplicate the plan body here.

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-06-18 | Drop concurrency/dashboard/presets/CI from the roadmap | Solo + 4 projects; these solve problems we don't have. Atomic writes (1.5) cover the only real risk. |
| 2026-06-18 | Provenance (3.1) before global store (3.2) | `project_id` is a one-line schema add and is a prerequisite for every other 3.x item. |
| 2026-06-18 | Embeddings before cross-project sharing | Embeddings are the only feature that visibly improves retrieval; sharing without semantic dedup creates duplicate noise. |

---

## Open Questions

1. **Embedding model size:** `all-MiniLM-L6-v2` (~90MB) vs. `bge-small-en` (~130MB)? Lean MiniLM for size; benchmark on the 2.2 fixture before committing.
2. **Universal domain enum:** stay hardcoded with `extra_domains` per project (current direction in 3.2), or move the whole list into config? Decide when `Automo` / `Integrated AI UI` start logging non-game learnings.
3. **Where does the grading fixture live?** Per-project under `memory/eval/` or central under `~/.agent-memory/eval/`? Probably per-project, since each project's tasks are different.

---

## Success Metrics

Only metrics with a real instrument. No guessed numbers.

| Metric | Instrument | Phase 1 target | Phase 2 target | Phase 3 target |
|--------|-----------|----------------|----------------|----------------|
| Test coverage (line) | `pytest-cov` | baseline + holds | baseline + 5 | baseline + 10 |
| Projects on shim | `update.py --status` | 4 / 4 | 4 / 4 | 4 / 4 |
| `filter.py` longest module | `wc -l engine/*.py` | < 400 | < 400 | < 400 |
| Retrieval top-3 hit rate | grading fixture (2.2) | n/a | > keyword baseline | hold |
| Cross-project entries used | `--lineage` audit | 0 | 0 | ≥ 5 in active retrieval |
| Half-written JSONL on Ctrl-C | kill-mid-write test | 0 occurrences | 0 | 0 |

---

## How to Use This Plan

1. **Sequential within a phase.** Don't skip an item; later items assume earlier ones.
2. **One sub-item per minor version bump.** Keeps the rollback story clean.
3. **Phase boundaries are firm.** Don't start Phase 2 until Phase 1.6 (schema versioning) is shipped — embeddings need it. Don't start Phase 3 until 2.3 (semantic dedup) is shipped — cross-project sharing needs it.
4. **Wishlist parking.** New ideas go into `Anti-Goals` or `Open Questions`. Don't expand phase scope mid-flight.
5. **Quarterly review.** Archive completed items, refresh open questions, add Decision Log entries only for changes of mind.
