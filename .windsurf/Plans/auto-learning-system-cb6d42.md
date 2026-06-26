# Auto-Learning System

> Plan ID: `cb6d42` — saved in-repo for consistency with other plans.
> Audit pass `1d2cfd` applied: reworked the retrieval filter (data-loss fix), `check_staleness` ctx guard, consolidate gating, API cache/alert hooks, and accuracy notes. See the audit report for rationale.

AGENTS.md compliance is an optimization, not a dependency. The engine generating useful learnings from git history, retrieval gaps, and corpus analysis without any agent ever reading a prompt file is the feature. Agents that do read `AGENTS.md` get a better experience; agents that don't still benefit from a corpus that self-improves. This plan builds that system: three detection sources — system signals (meta-learnings), git history (code-change patterns), and retrieval-failure correlation — triggered during `--consolidate` and on-demand via `--auto-learn`.

## Decisions Locked (updated)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Trigger points | `--consolidate` (batch) + new `--auto-learn` (on-demand) |
| 2 | Retrieval-failure approach | A — learning content comes from the `bug_fix` entry, not from the empty retrieval |
| 3 | Code-change signals | A (repeated fixes, 3+ in last N commits) + B (revert commits) |
| 4 | Meta-learning signals | C (under-retrieved), E (conflicts), B-expanded (over-injected with A+C noise proxy) |
| 4b | Noise proxy for B | A path 2 (metrics.jsonl correlation — add `entry_components`/`entry_files_touched` to log events) + C (stale + high access + low reinforcement) |
| 5 | `source_agent: "system"` | Add `"system"` to `VALID_SOURCE_AGENTS` in `constants.py` — single validation path, no special ctx. **Risk:** projects that override `valid_source_agents` in `config.json` must include `"system"`. |
| 6 | `resolved` on creation | Meta-learnings: `resolved: true`. Codebase learnings: `resolved: false`. Set at generation layer by subtype. |
| 7 | Config toggle | `auto_learn_enabled: true` by default (opt-out) |
| 8 | `type` field | New `meta_learning` type. Excluded from retrieval **for free** via the existing `resolved: true` skip (`retrieval.py:287`). Do **not** reassign `entries` in `retrieve_core` (see Phase 1 retrieval note). |
| 9 | Dedup | Handled by existing `log_core()` pipeline — no special logic |
| 10 | API surface | CLI + HTTP API only. No MCP tool, no SDK method. |
| 11 | Output | `--auto-learn`: verbose (summary + entry details). `--consolidate`: compact block in existing report. |
| 12 | Testing | Unit tests for detection functions + one integration test |
| 13 | Git scan depth | Configurable: `auto_learn_git_scan_depth` default 20 |
| 14 | Retrieval-failure lookback | Since last consolidation (reuses `_last_consolidation_ts()`) |
| 15 | Thresholds | Configurable in `config.json` tuning section |
| 16 | Staleness threshold | `auto_learn_staleness_threshold` default 500; make `check_staleness()` accept this from `ctx` so it is consistent across the codebase. |
| 17 | Boolean config | Add boolean handling to `load_config()` in `cli.py` for `auto_learn_enabled`. |
| 18 | Capping priority | `retrieval-failure` → `repeated-fix` → `reverts` → `conflicts` → `under-retrieved` → `over-injected`. Stop when `auto_learn_max_per_run` is reached. |
| 19 | Domain for git-derived signals | Hardcoded `_PATH_DOMAIN_MAP` in `auto_learn.py` with `_derive_domain(file_path)` helper. Falls back to `"tooling"` for unmatched paths. Not project-configurable (YAGNI; add config key later if needed). |
| 20 | Step for git-derived signals | Corpus `max_step` (fallback to `1` if empty). Aligns generated entries with the current task context for retention purposes. |
| 21 | Conflict detection scope | `actions_oppose()` only (ALWAYS vs NEVER). Conservative, matches existing engine logic. Designed for future `detect_soft_conflicts()` extension without changing the strict detector. |

## Assumptions / Risks

- **Config overrides.** `valid_source_agents` *is* project-overridable (`cli.py:269-273`), so an overriding project must include `"system"`. `valid_types` is **not** loaded from `config.json` — it is fixed in `constants.py` (`VALID_TYPES`), intentionally non-configurable per the rationale at `constants.py:47-61`. Adding `"meta_learning"` to that constant makes it universally available with no per-project override; a universal engine-internal type is consistent with the "universal schema" rationale.
- **Validation rules.** The generated entries must satisfy the same schema as hand-written entries (`trigger` starts with `When`, `action` contains `ALWAYS` or `NEVER`). This plan explicitly designs every generator to meet those rules.
- **No new embedding model.** Generated entries use the same embedding model configured in `ctx`.
- **Best-effort git.** If `git` is unavailable, the git-based detectors are skipped with a warning; the rest of the pipeline still runs.
- **`log_core()` contract.** `auto_learn_core()` treats `status` from `log_core()` as follows: `added` and `conflict` count as generated; `duplicate` and `semantic_duplicate` count as deduped; `quarantined` counts as skipped.
- **Cold start.** `detect_over_injected` and `detect_retrieval_failure` rely on `entry_components`/`entry_files_touched` in log events, which Phase 1 only begins emitting going forward (`_entry_meta()` in `handlers.py` omits them today). These two detectors produce nothing on historical-only data until new metrics accumulate; the git- and corpus-based detectors are unaffected.

## Implementation Plan

### Phase 1: Schema, Config, Retrieval Filter, and Staleness Wiring

#### `src/agent_memory/engine/constants.py`

- Add `"meta_learning"` to `VALID_TYPES`.
- Add `"system"` to `VALID_SOURCE_AGENTS`.
- Add new defaults:
  - `AUTO_LEARN_ENABLED = True`
  - `AUTO_LEARN_GIT_SCAN_DEPTH = 20`
  - `AUTO_LEARN_FIX_COMMIT_THRESHOLD = 3`
  - `AUTO_LEARN_UNDER_RETRIEVED_ACCESS = 2`
  - `AUTO_LEARN_UNDER_RETRIEVED_REINFORCEMENT = 5`
  - `AUTO_LEARN_OVER_INJECTED_ACCESS = 10`
  - `AUTO_LEARN_OVER_INJECTED_REINFORCEMENT = 2`
  - `AUTO_LEARN_STALENESS_THRESHOLD = 500` (lines changed)
  - `AUTO_LEARN_MAX_FILES_PER_COMMIT = 5` (skip broad commits)
  - `AUTO_LEARN_MAX_PER_RUN = 20` (cap generated entries per run)
  - `AUTO_LEARN_RETRIEVAL_FAILURE_CAP = 100` (max retrieval events scanned when `since_ts` is `None`)
- Add all to `DEFAULTS` dict.

#### `templates/config.json`

- Add to `tuning` section:
  ```json
  "auto_learn_enabled": true,
  "auto_learn_git_scan_depth": 20,
  "auto_learn_fix_commit_threshold": 3,
  "auto_learn_under_retrieved_access": 2,
  "auto_learn_under_retrieved_reinforcement": 5,
  "auto_learn_over_injected_access": 10,
  "auto_learn_over_injected_reinforcement": 2,
  "auto_learn_staleness_threshold": 500,
  "auto_learn_max_files_per_commit": 5,
  "auto_learn_max_per_run": 20,
  "auto_learn_retrieval_failure_cap": 100
  ```

#### `src/agent_memory/cli.py` (`load_config()`)

- Add boolean handling:
  ```python
  bool_params = {"auto_learn_enabled": "AUTO_LEARN_ENABLED"}
  for config_key, python_key in bool_params.items():
      if config_key in tuning:
          value = tuning[config_key]
          if not isinstance(value, bool):
              raise TypeError(f"tuning.{config_key} must be a boolean, got {type(value).__name__}")
          result[python_key] = value
  ```
- Add to `int_params`:
  - `auto_learn_git_scan_depth`
  - `auto_learn_fix_commit_threshold`
  - `auto_learn_under_retrieved_access`
  - `auto_learn_under_retrieved_reinforcement`
  - `auto_learn_over_injected_access`
  - `auto_learn_over_injected_reinforcement`
  - `auto_learn_staleness_threshold`
  - `auto_learn_max_files_per_commit`
  - `auto_learn_max_per_run`
  - `auto_learn_retrieval_failure_cap`

#### `src/agent_memory/engine/git_utils.py`

- Change `check_staleness(entry, repo_root)` to `check_staleness(entry, repo_root, ctx=None)`.
- **Guard the default first:** `ctx = ctx or {}` (the param defaults to `None`; calling `None.get(...)` would raise). Then use `ctx.get("auto_learn_staleness_threshold", 500)` instead of the hardcoded `500`.
- `check_staleness` returns a 3-tuple `(is_stale, lines_changed, error)` — callers building the staleness map must unpack it.
- Update existing call sites (both already unpack the 3-tuple):
  - `src/agent_memory/engine/consolidation.py:296`
  - `src/agent_memory/engine/dashboard_api.py:212`

#### `src/agent_memory/engine/handlers.py`

- In `log_core()`, add `entry_components` and `entry_files_touched` to the `_entry_meta()` lambda or directly to the `log_event()` calls:
  ```python
  "entry_components": entry.get("components", []),
  "entry_files_touched": entry.get("files_touched", []),
  ```
  This enables the A-path-2 noise proxy for over-injection detection.

#### `src/agent_memory/engine/validation.py`

- No schema change needed. `meta_learning` is accepted through `ctx["valid_types"]`, and `"system"` through `ctx["valid_source_agents"]`.
- **Important:** generated entries must still provide all required fields and meet the `ALWAYS`/`NEVER` and `When` rules. Phase 2 defines exactly how.

#### `src/agent_memory/engine/retrieval.py`

- **No code change.** Meta-learnings are generated `resolved: true`, and `retrieve_core()` already skips resolved entries at `retrieval.py:287`. That skip is data-safe because it filters *during scoring* without rebinding `entries`. **Decision: rely on the resolved-skip only (approach A).** No type-based filter in retrieval — if an unresolved `meta_learning` ever surfaces, that's a generator bug to fix at the source, not a retrieval-layer guard.
- **Do NOT reassign `entries`.** `retrieve_core()` rewrites the whole file at `retrieval.py:408` (`write_learnings(paths, entries)`) whenever results are returned. Any in-place filter on `entries` would **delete the filtered-out entries from `learnings.jsonl`** on the next hit.

### Phase 2: Core Detection Module (`src/agent_memory/engine/auto_learn.py`)

New module with pure detection functions, one domain-derivation helper, and one orchestration function.

#### Path-to-domain heuristic

Hardcoded module-level constant in `auto_learn.py`:

```python
_PATH_DOMAIN_MAP = {
    # database
    "db/": "database", "database/": "database", "sql": "database",
    "migration": "database", "schema": "database", "orm/": "database",
    "model/": "database", "repository/": "database", "dao/": "database",
    "entity/": "database", "prisma": "database", "drizzle": "database",
    "sequelize": "database", "knex": "database", "alembic": "database",

    # frontend
    "ui/": "frontend", "frontend/": "frontend", "components/": "frontend",
    "view/": "frontend", "views/": "frontend", "page/": "frontend",
    "pages/": "frontend", "component/": "frontend", "widget/": "frontend",
    "template/": "frontend", "templates/": "frontend", "src/app/": "frontend",
    "public/": "frontend", "assets/": "frontend", "static/": "frontend",
    "css/": "frontend", "scss/": "frontend", "sass/": "frontend",
    "tailwind": "frontend", "jsx": "frontend", "tsx": "frontend",
    "vue": "frontend", "svelte": "frontend",

    # api
    "api/": "api", "routes/": "api", "router/": "api",
    "endpoint/": "api", "endpoints/": "api", "controller/": "api",
    "controllers/": "api", "graphql": "api", "rest/": "api",
    "openapi": "api", "swagger": "api",

    # backend
    "services/": "backend", "service/": "backend", "handler/": "backend",
    "handlers/": "backend", "logic/": "backend", "domain/": "backend",
    "use_case/": "backend", "usecase/": "backend", "interactor/": "backend",
    "command/": "backend", "query/": "backend",

    # security
    "auth/": "security", "security/": "security", "crypto/": "security",
    "login/": "security", "oauth/": "security", "jwt/": "security",
    "token/": "security", "password/": "security", "permission/": "security",
    "rbac/": "security", "middleware/": "security",

    # deployment
    "deploy/": "deployment", "deployment/": "deployment", "infra/": "deployment",
    "infrastructure/": "deployment", "scripts/": "deployment",
    "docker": "deployment", "k8s/": "deployment", "kubernetes/": "deployment",
    "terraform/": "deployment", "ansible/": "deployment", "helm/": "deployment",
    "ci/": "deployment", "cd/": "deployment", ".github/": "deployment",
    "gitlab-ci": "deployment", "jenkins": "deployment",

    # testing
    "test/": "testing", "tests/": "testing", "spec/": "testing",
    "specs/": "testing", "__tests__/": "testing", "fixture/": "testing",
    "fixtures/": "testing", "mock/": "testing", "mocks/": "testing",
    "e2e/": "testing", "integration/": "testing",

    # performance
    "perf/": "performance", "performance/": "performance",
    "benchmark/": "performance", "benchmarks/": "performance",
    "cache/": "performance", "redis/": "performance",
    "worker/": "performance", "queue/": "performance",

    # documentation
    "docs/": "documentation", "doc/": "documentation",
    "documentation/": "documentation", "readme": "documentation",
    "changelog": "documentation", "license": "documentation",
}
```

```python
def _derive_domain(file_path: str) -> str:
    """Derive a domain tag from a file path using keyword matching.

    Falls back to 'tooling' for unmatched paths.
    """
    path_lower = file_path.lower().replace("\\", "/")
    for keyword, domain in _PATH_DOMAIN_MAP.items():
        if keyword in path_lower:
            return domain
    return "tooling"
```

#### Generated entry schema

Every candidate passed to `log_core()` must be a valid learning entry. The detection functions build the candidate dict; `log_core()` will stamp `ts`, `commit`, `access_count`, and `reinforcement_count`.

| Field | Value |
|-------|-------|
| `ts` | omitted (filled by `stamp_entry()`) |
| `step` | Source entry step for meta-learnings; corpus max step for git-derived entries (`1` if corpus empty) |
| `source_agent` | `"system"` |
| `type` | `meta_learning` (meta-signals) or `bug_fix` (code-signals) |
| `domain` | Inherit from source entry for meta-learnings; derived via `_derive_domain(file_path)` for git-derived signals (falls back to `"tooling"`) |
| `components` | Source entry components; intersection for conflicts; file stem / module for git-derived |
| `files_touched` | Source entry files; target file for repeated-fix; commit files for reverts |
| `trigger` | Must start with `When` (guaranteed by construction) |
| `action` | Must contain `ALWAYS` or `NEVER` (guaranteed by construction) |
| `reason` | Human-readable explanation of why the system generated the learning |
| `importance` | `5` for meta-learning, `7` for code-signals, `8` for conflicts/reverts |
| `severity` | `"minor"` for meta-learning, `"major"` for code-signals, `"critical"` for reverts |
| `resolved` | `true` for `meta_learning`, `false` for `bug_fix` |

#### Detection functions (pure — receive pre-computed data, no I/O)

`auto_learn_core()` handles all I/O and passes plain data to these functions.

```python
detect_under_retrieved(entries, ctx) -> list[dict]
```
- Scan entries for `access_count <= auto_learn_under_retrieved_access` and `reinforcement_count >= auto_learn_under_retrieved_reinforcement`.
- For each candidate, produce a `meta_learning` entry:
  - `trigger`: the original entry's `trigger` (already starts with `When`)
  - `action`: `"ALWAYS consider this rule: " + original["action"]`
  - `reason`: `"Under-retrieved learning: access_count={n}, reinforcement_count={m}. The rule is frequently reinforced but rarely retrieved."`
  - `resolved`: `true`

```python
detect_conflicts(entries, ctx) -> list[dict]
```
- For each pair of unresolved entries with overlapping `components`:
  - If `actions_oppose(entry_i["action"], entry_j["action"])` is true, generate a `meta_learning` entry:
    - `step`: `max(entry_i["step"], entry_j["step"])`
    - `components`: intersection of components
    - `files_touched`: union of files_touched
    - `trigger`: `"When working with {components}, both '{entry_i[trigger]}' and '{entry_j[trigger]}' apply"`
    - `action`: `"NEVER apply both '{entry_i[action]}' and '{entry_j[action]}' for the same context; resolve the conflict before proceeding"`
    - `reason`: `"Auto-detected conflict between {entry_i[source_agent]} and {entry_j[source_agent]}"`
    - `resolved`: `true`

```python
detect_over_injected(entries, log_events, staleness_map, ctx) -> list[dict]
```
- `staleness_map`: pre-computed `{entry_ts: bool}` from `auto_learn_core()` calling `check_staleness()`.
- `log_events`: pre-read `event_type == "log"` events from `metrics.jsonl`.
- Core signals (all required):
  - `access_count >= auto_learn_over_injected_access`
  - `reinforcement_count <= auto_learn_over_injected_reinforcement`
  - No subsequent log event has overlapping `entry_components` with the entry (i.e., no later learning in the same component area).
- Staleness boost: if `staleness_map[entry_ts]` is `True`, lower the access threshold by 50% (`max(1, threshold // 2)`). Staleness is a boost, not a gate.
- For each candidate, produce a `meta_learning` entry:
  - `trigger`: the original entry's `trigger`
  - `action`: `"NEVER rely on this rule as the primary signal unless it has been reinforced: " + original["action"]`
  - `reason`: `"Over-injected: high access but low reinforcement. No subsequent learning covers the same components."`
  - `resolved`: `true`

```python
detect_retrieval_failure(entries, retrieval_events, log_events, since_ts, ctx) -> list[dict]
```
- `since_ts`: pre-computed `datetime` or `None` from `auto_learn_core()` calling `_last_consolidation_ts()` and parsing the ISO string.
- `log_events`: pre-read `event_type == "log"` events.
- `retrieval_events`: pre-read `event_type == "retrieval"` events.
- Filter `log_events` to `entry_type == "bug_fix"`.
- When `since_ts is None`:
  - Use all `bug_fix` log events.
  - Cap `retrieval_events` to the most recent `auto_learn_retrieval_failure_cap` events to bound cost on large corpora.
- When `since_ts` is set:
  - Use `bug_fix` log events and `retrieval_events` at or after `since_ts`.
- For each bug_fix event:
  - Find prior retrieval events (by `ts`) with overlapping `query_components` and the bug_fix entry's `components`, where `warnings_returned == 0` and `patterns_returned == 0`.
  - If found, generate a `bug_fix` entry from the bug_fix entry itself:
    - `source_agent`: `"system"`
    - `type`: `"bug_fix"`
    - `resolved`: `false`
    - `trigger`: `"When retrieving learnings for {components}"`
    - `action`: `"ALWAYS include the pattern from entry ts:{bug_fix_ts} — prior retrieval queries for these components returned zero results"`
    - `reason`: `"Retrieval-failure correlation: a prior query with matching components returned zero results."`
  - The trigger/action/reason come from the bug_fix, not from the empty retrieval.

```python
detect_repeated_fixes(commits, ctx) -> list[dict]
```
- `commits`: pre-parsed list of `{hash, message, files}` dicts from `auto_learn_core()` running `git log`.
- Parse commit messages for fix keywords: `fix`, `bug`, `error`, `broken`, `crash`.
- Skip commits touching more than `auto_learn_max_files_per_commit` files.
- Group by file.
- Files with `auto_learn_fix_commit_threshold`+ fix commits → generate `type: "bug_fix"`, `resolved: false`:
  - `trigger`: `"When modifying {file}"`
  - `action`: `"ALWAYS verify the area around {file} — this file has been fixed repeatedly in recent commits"`
  - `reason`: `"Detected {count} fix commits touching {file} in the last {depth} commits."`
  - `components`: derived from the file path (e.g., directory stem or module name)
  - `files_touched`: `[file]`
  - `domain`: `_derive_domain(file)`
  - `importance`: `7`, `severity`: `"major"`

```python
detect_reverts(commits, ctx) -> list[dict]
```
- `commits`: same pre-parsed list.
- Filter for revert commits (`"revert"` in `message.lower()`).
- Only generate learnings for reverts with a parseable quoted subject (e.g., `Revert "feat: add Redis cache"`). Skip hash-only reverts (e.g., `Revert commit abc1234`) — count them in output but don't generate learnings.
- For parseable reverts, extract the quoted subject and use it directly:
  - `trigger`: `"When attempting {extracted_subject}"`
  - `action`: `"NEVER attempt {extracted_subject} — this approach was reverted"`
  - `reason`: `"Git revert detected: this approach was reverted, indicating it caused problems."`
  - `type`: `"bug_fix"`, `resolved`: `false`
  - `importance`: `8`, `severity`: `"critical"`
  - `domain`: `_derive_domain(commit_files[0])` if commit files else `"tooling"`
  - `files_touched`: commit files
  - `components`: derived from subject or files

#### Orchestration function

```python
auto_learn_core(paths, ctx) -> dict
```

- Check `ctx.get("auto_learn_enabled", True)` — return early with an empty `generated` list if disabled.
- Read `learnings.jsonl` via `read_learnings(paths)`.
- Read `metrics.jsonl` via `read_metrics(paths)`.
- Compute `max_step` from the corpus (or `1` if empty).
- Run `git log --format=%H%n%s%n%b%n--COMMIT--%n --name-only -n {auto_learn_git_scan_depth}` and parse into `commits` list. Wrap in `try/except` — if git is unavailable or not a repo, skip `detect_repeated_fixes` and `detect_reverts` and set `git_available: false`.
- Compute `staleness_map` (`{entry_ts: bool}`) for each entry with high `access_count` (≥ `auto_learn_over_injected_access`), unpacking `is_stale, _, err = check_staleness(entry, paths.repo_root, ctx)`. Treat `err is not None` (and any exception) as `False`.
- Get `since_ts` via `_last_consolidation_ts(paths)` (imported from `agent_memory.engine.triggers`) and parse the ISO string to a UTC `datetime` if present.
- Split metrics events into `retrieval_events` and `log_events` by `event_type`.
- Call the six detection functions in priority order, stopping when `auto_learn_max_per_run` generated entries are reached:
  1. `detect_retrieval_failure`
  2. `detect_repeated_fixes`
  3. `detect_reverts`
  4. `detect_conflicts`
  5. `detect_under_retrieved`
  6. `detect_over_injected`
- For each candidate, call `log_core(json.dumps(candidate), paths, ctx)` and track `status`:
  - `added` / `conflict` → `generated` list
  - `duplicate` / `semantic_duplicate` → `deduped` count
  - `quarantined` → `skipped` count, include in `skipped_details` if useful
- If the cap is hit, set `capped: true` and include a warning in the result.
- Return result dict:
  ```python
  {
      "exit_code": 0,
      "status": "ok",
      "scanned": {
          "metrics_events": N,
          "learnings": N,
          "git_commits": N,
          "retrieval_events": N,
      },
      "generated": [...],        # list of {type, trigger, components/files}
      "deduped": N,
      "skipped": N,
      "capped": bool,
      "git_available": bool,
  }
  ```

### Phase 3: CLI Integration

#### `src/agent_memory/cli.py`

- Add `--auto-learn` flag to argparse.
- Import `auto_learn_core` from `agent_memory.engine.auto_learn`.
- Add mutual exclusion: `--auto-learn` cannot combine with `--consolidate`, `--step`, `--log`, `--log-file`, `--resolve`, `--update`, `--review-agents`, `--stats`, `--metrics`, `--serve`, `--dashboard`, `--mcp`, `--eval`, `--migrate-schema`, `--verify`.
- In main dispatch:
  - If `args.auto_learn`, call `auto_learn_core(_get_paths(), _build_ctx())` and print verbose output.
- In `--consolidate` handler:
  - **Gate:** only run auto-learn when `not args.confirm_reset` (a `--confirm-reset` run clears `learnings.jsonl` via `handle_confirm_reset`; repopulating it immediately would be wrong). A `no_entries` consolidation may still run auto-learn — it also draws on git/metrics, so generating from those is acceptable.
  - Capture the `handle_consolidate()` exit code; when gated in and it returned `0`, call `auto_learn_core()` **wrapped in `try/except`** — auto-learning is best-effort — then return the original exit code.
  - On success, print the compact block.
  - On failure, print the error in the compact block but do not change the consolidation exit code.

#### Verbose output (`--auto-learn` standalone)

```
## AUTO-LEARNING
Scanned: 142 metrics events, 38 learnings, 20 git commits

Generated: 3 learnings
  [retrieval-failure] components: auth, session_store
    → "When retrieving learnings for auth+session_store, ALWAYS include the pattern from entry ts:1234 — prior retrieval queries returned zero results"
  [repeated-fix] file: src/db/connection.py (4 fix commits in 20) [database]
    → "When modifying src/db/connection.py, ALWAYS verify the area around connection.py — this file has been fixed repeatedly in recent commits"
  [conflict] trigger: "When working with cache, both '...' and '...' apply"
    → "NEVER apply both '...' and '...' for the same context; resolve the conflict before proceeding"

Deduped: 2 (incremented access_count on existing entries)
Skipped: 0
```

#### Compact output (inside `--consolidate`)

```
## AUTO-LEARNING
  Scanned: 142 events / 38 learnings / 20 commits
  Generated: 3  |  Deduped: 2  |  Skipped: 0
  Types: 1 retrieval-failure, 1 repeated-fix, 1 conflict
```

### Phase 4: HTTP API

#### `src/agent_memory/engine/server.py`

- Add `POST /api/auto-learn` endpoint:
  - Calls `auto_learn_core(paths, ctx)`.
  - Returns the result dict directly (no new Pydantic model, consistent with the existing API surface).
  - Because it writes new learnings, mirror the other write endpoints (`server.py:271,319,395`): `await event_hub.broadcast({...})`, then `invalidate_metrics_cache()`, then `await _check_and_broadcast_alerts(paths, ctx, event_hub)` when `dashboard`.
- Modify `POST /api/consolidate`:
  - **Only when `result["status"] == "reported"`**, call `auto_learn_core()` and include the compact summary in the response (skip for `confirm_reset` / `no_entries` / `archive_exists`).
  - Broadcast via `event_hub` and call `invalidate_metrics_cache()` (consolidate already does so).

#### `src/agent_memory/engine/models.py`

- No new request model needed — `POST /api/auto-learn` takes no body. The response uses `dict[str, Any]` to avoid duplicate validation drift, per the architecture decision in `AGENTS.md`.

### Phase 5: Tests

#### `tests/test_pure_functions.py`

> **AGENTS.md exception:** this file is currently sanctioned to import only from `(validation, retrieval, consolidation)`. Adding direct `auto_learn` imports requires extending that exception in `AGENTS.md` § Testing (see Phase 6). Otherwise, route these tests through the CLI instead.

Add unit tests for the pure detection functions (no I/O, no mocking):

- `detect_under_retrieved` — feed fixture entries, verify correct candidates returned.
- `detect_conflicts` — feed entries with opposing actions, verify conflict detected.
- `detect_over_injected` — feed entries + log events + staleness_map, verify noise proxy requires all three signals.
- `detect_repeated_fixes` — feed pre-parsed `commits` list, verify threshold logic and `_derive_domain()` mapping.
- `detect_reverts` — feed pre-parsed `commits` list, verify revert detection, quoted-subject extraction, and `_derive_domain()` mapping.
- `_derive_domain` — feed various file paths, verify correct domain mapping and `tooling` fallback.
- `detect_retrieval_failure` — feed entries + retrieval events + log events + `since_ts`, verify correlation logic.
- Retrieval filter test — verify `meta_learning` entries (which are `resolved: true`) are excluded from retrieval results **and still present in `learnings.jsonl` after a retrieval that returns hits** (regression guard for the write-back at `retrieval.py:408`).
- `auto_learn_enabled: false` test — verify early return when disabled.
- `check_staleness` threshold test — verify `ctx["auto_learn_staleness_threshold"]` is respected.

#### `tests/test_auto_learn.py`

New file, integration test via CLI (no direct import of engine module, per `AGENTS.md`):

- Create a temp `memory/` dir with fixture `learnings.jsonl`, `metrics.jsonl`, `config.json`.
- Run `python -m agent_memory.cli --auto-learn --memory-dir <temp_dir>` as a subprocess.
- Verify generated entries appear in `learnings.jsonl`.
- Run `--auto-learn` again, verify dedup (access_count incremented, no new duplicates).
- Verify `meta_learning` entries are `resolved: true`.
- Verify `bug_fix` entries are `resolved: false`.

#### `tests/test_server.py`

- Add HTTP endpoint test:
  - `POST /api/auto-learn` returns 200 with the result dict.
  - `POST /api/consolidate` response includes the auto-learning summary.

### Phase 6: Documentation, Versioning, and Deployment

- `docs/architecture-overview.md` — add Auto-Learning section with value-proposition framing.
- `docs/cli-reference.md` — add `--auto-learn` flag.
- `docs/config-tuning.md` — add Auto-Learning parameter table.
- `templates/agents-memory-section.md` — note that auto-learning is built-in; agents don't need to trigger it.
- `AGENTS.md` § Testing — extend the `tests/test_pure_functions.py` import exception to include `auto_learn` (Phase 5 adds direct imports of the pure detectors).
- `CHANGELOG.md` — add a v1.21.x entry describing the new feature.
- `VERSION` — bump the minor version (e.g., `1.21.0`).
- `scripts/deploy.ps1` — run after tests pass, per `AGENTS.md`.

## File Change Summary

| File | Change |
|------|--------|
| `src/agent_memory/engine/constants.py` | Add `meta_learning` type, `system` agent, auto-learn defaults |
| `src/agent_memory/engine/auto_learn.py` | **NEW** — detection functions + orchestration |
| `src/agent_memory/engine/git_utils.py` | Make `check_staleness()` accept `ctx` for configurable threshold |
| `src/agent_memory/engine/consolidation.py` | Update `check_staleness()` call to pass `ctx` |
| `src/agent_memory/engine/dashboard_api.py` | Update `check_staleness()` call to pass `ctx` |
| `src/agent_memory/engine/handlers.py` | Add `entry_components`/`entry_files_touched` to `log_event()` call |
| `src/agent_memory/engine/retrieval.py` | None required (the `resolved: true` skip already excludes meta-learnings); if a filter is added it must **not** reassign `entries` (write-back at line 408) |
| `src/agent_memory/engine/server.py` | Add `POST /api/auto-learn`, integrate into `POST /api/consolidate` |
| `src/agent_memory/cli.py` | Add `--auto-learn` flag, `load_config()` bool/int params, integrate into `--consolidate` |
| `templates/config.json` | Add auto-learn tuning keys |
| `tests/test_pure_functions.py` | Add detection function unit tests + threshold/filter tests |
| `tests/test_auto_learn.py` | **NEW** — integration test via CLI subprocess |
| `tests/test_server.py` | Add `POST /api/auto-learn` endpoint test |
| `AGENTS.md` | Extend `test_pure_functions.py` import exception to include `auto_learn` |
| `docs/architecture-overview.md` | Add Auto-Learning section |
| `docs/cli-reference.md` | Add `--auto-learn` flag docs |
| `docs/config-tuning.md` | Add Auto-Learning parameter table |
| `CHANGELOG.md` | Add feature entry |
| `VERSION` | Bump minor version |

## Execution Order

1. **Phase 1** — schema/config, staleness wiring, retrieval filter, and metrics logging changes.
2. **Phase 2** — `auto_learn.py` core detection module.
3. **Phase 3** — CLI integration.
4. **Phase 4** — HTTP API integration.
5. **Phase 5** — tests.
6. **Phase 6** — docs, version bump, changelog, deployment.

Phase 3 and Phase 4 can proceed in parallel after Phase 2 is complete.

## Resolved Design Decisions (from deep-dive)

1. **Domain for git-derived signals** — Hardcoded `_PATH_DOMAIN_MAP` in `auto_learn.py` with `_derive_domain()` helper. Falls back to `"tooling"`. Not configurable (YAGNI; add a config key later if a real need emerges). Rationale: a best-effort path heuristic doesn't warrant config surface; most projects won't customize it anyway.
2. **Step for git-derived signals** — Corpus `max_step` (fallback to `1`). Rationale: simplest defensible choice; aligns generated entries with the current task context for retention. Reverts are `critical` so retention is unconditional regardless of step.
3. **Conflict detection scope** — `actions_oppose()` only (ALWAYS vs NEVER). Rationale: avoids flooding the corpus with false-positive `meta_learning` entries; matches existing engine logic in `log_core()`. The detector structure supports a future `detect_soft_conflicts()` without changing the strict detector.

---

*Original plan: `C:\Users\Admin\.windsurf\plans\auto-learning-system-cb6d42.md`*  
*Revised plan saved in-repo: `C:\AgentMemoryEngine\.windsurf\Plans\auto-learning-system-cb6d42.md`*
