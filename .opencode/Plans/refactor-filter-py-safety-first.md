# Refactor `filter.py` — Safety-First Plan

> **Goal:** Split `filter.py` (1821 lines) into `filter.py` (<400 lines) + 9 `engine/` modules (each <400 lines). Zero behavioral drift.
> **Method:** Golden baseline → incremental extraction → diff gate after every phase.

---

## Contracts — What Must Not Change

### CLI flags & exit codes

| Flag combo | Exit | Notes |
|---|---|---|
| `--version` | 0 | To **stderr**, before `setup_paths`/`load_config` |
| `--step N --components/--files/--domain` | 0 | Retrieval; bumps `access_count` in learnings.jsonl |
| `--log` / `--log-file` | 0 (DUPLICATE/CONFLICT), 1 (quarantine) | |
| `--update <ts> --log/--log-file` | 0 / 1 not-found | |
| `--resolve <ts>` | 0 / 1 not-found | |
| `--stats` | 0 | |
| `--review-agents --step N` | 0 | |
| `--consolidate [--sprint N] [--force]` | 0 / 1 archive-exists | |
| `--consolidate --confirm-reset` | 0 | Clears learnings.jsonl |
| no args | 1 | |

Mutual exclusions, stdout section headers, stderr-only version/warnings, and file I/O paths are unchanged. See current `filter.py:1739–1816` for the authoritative dispatch.

### Existing infrastructure already in place

- `Paths` dataclass (frozen, line 45) — already parameterized, no dict conversion needed.
- `setup_paths()` returns `Paths`, `main()` stores it in `global PATHS` — extraction just passes it as argument instead.
- `shim.py` + `AGENT_MEMORY_DIR` env var support — shim cutover is shipped.
- `engine_version.py` — version is already externalized.

---

## Dependency Injection

All extracted modules receive `paths: Paths` and/or `ctx: dict` as parameters. No callable injection. No `globals().update`.

### `ctx` dict (replaces `globals().update(config)`)

```python
def build_default_ctx() -> dict:
    """Seed from engine.constants, then overlay load_config()."""
    from engine.constants import DEFAULTS
    ctx = dict(DEFAULTS)
    ctx.update(load_config() or {})
    return ctx
```

Keys: `decay_rate`, `score_threshold`, `component_weight`, `file_weight`, `domain_weight`, `no_match_weight`, `max_warnings`, `max_patterns`, `minor_retention`, `major_retention`, `escalation_threshold`, `max_step`, `session_expiry_minutes`, `valid_domains`, `valid_source_agents`, `valid_retrieval_agents`, `domain_mappings`, `valid_types`, `valid_severities`, `valid_scopes`, `valid_debt_levels`, `stop_words`.

---

## Phase 0: Golden Baseline (read-only, no code changes)

### 0.1 — Create fixture data

6 entries covering: component match, domain match, escalation path (step=0 + critical, queried at --step 30), empty match, no-domain, resolved entry. All fields legal per current schema. Save to `memory/.baseline/fixture-learnings.jsonl`.

Synthetic `AGENTS.md` with Physics/Tooling/Performance sections → `memory/.baseline/fixture-agents.md`.

### 0.2 — Capture baselines

One PowerShell script that `Restore-Fixture` before each call, captures stdout+stderr, and saves to `memory/.baseline/`:

```powershell
function Restore-Fixture { Copy-Item memory\.baseline\fixture-learnings.jsonl memory\learnings.jsonl -Force }

# Read paths
Restore-Fixture; & python src\filter.py --stats                                            *> memory\.baseline\stats.txt
Restore-Fixture; & python src\filter.py --step 5 --components Player,Enemy --domain physics *> memory\.baseline\retrieval-component.txt
Restore-Fixture; & python src\filter.py --step 10 --domain tooling                         *> memory\.baseline\retrieval-domain.txt
Restore-Fixture; & python src\filter.py --step 1 --components NonExistent --domain nonexist *> memory\.baseline\retrieval-empty.txt
Restore-Fixture; & python src\filter.py --step 5 --components Player,Enemy                 *> memory\.baseline\retrieval-no-domain.txt
Restore-Fixture; & python src\filter.py --step 5                                           *> memory\.baseline\step-alone.txt
Restore-Fixture; & python src\filter.py --step 30 --domain tooling                         *> memory\.baseline\retrieval-escalation.txt

# Write paths (log, duplicate, quarantine, update, resolve)
Restore-Fixture; & python src\filter.py --log-file memory\.baseline\test-entry.json        *> memory\.baseline\log-new.txt
# ... (duplicate, quarantine, update, resolve baselines)

# Consolidation (consolidate, --force, --confirm-reset)
Restore-Fixture; Copy-Item memory\.baseline\fixture-agents.md memory\AGENTS.md -Force
& python src\filter.py --consolidate --sprint 1                                            *> memory\.baseline\consolidate.txt

# File mutation snapshots (learnings.jsonl after retrieval, after log, etc.)
# Version
& python src\filter.py --version 2>&1 > memory\.baseline\version-stderr.txt

# Review-agents
Restore-Fixture; Copy-Item memory\.baseline\fixture-agents.md memory\AGENTS.md -Force
& python src\filter.py --review-agents --step 5                                            *> memory\.baseline\review-agents.txt
```

**Gate:** All baseline files saved. No code changes.

---

## Extraction Phases 1–7

Each phase: extract functions → update imports in `filter.py` → run diff gate → `pytest tests/test_memory.py -v`.

### Diff gate (reusable, run after every phase)

```powershell
function Assert-NoDiff($expected, $actual) {
    $diff = Compare-Object (Get-Content $expected) (Get-Content $actual)
    if ($diff) { Write-Error "DIFF: $expected vs $actual"; $diff | Format-Table; exit 1 }
}
# Re-run all Phase 0.2 captures with "-pN" suffix, Assert-NoDiff against each baseline.
```

### Phase 1: `engine/io.py`

| Function | Current line | Signature change |
|---|---|---|
| `read_learnings` | 370 | `() → (paths: Paths)` |
| `append_learning` | 388 | `(entry) → (paths: Paths, entry)` |
| `write_learnings` | 407 | `(entries) → (paths: Paths, entries)` |
| `quarantine` | 439 | `(raw_input, reason) → (paths: Paths, raw_input, reason)` |

All currently read from `_get_paths()` / module globals. Replace with `paths` parameter.

### Phase 2: `engine/validation.py`

| Function | Current line | Signature change |
|---|---|---|
| `validate_entry` | 453 | `(entry) → (entry, ctx)` — reads `MAX_STEP`, `VALID_*` from globals |
| `jaccard_similarity` | 579 | No change (pure) |
| `actions_oppose` | 592 | No change (pure) |
| `find_best_match` | 603 | No change |

### Phase 3: `engine/retrieval.py`

| Function | Current line | Signature change |
|---|---|---|
| `score_entry` | 1006 | Add `ctx` param (reads scoring weights, decay) |
| `is_in_retention` | 1026 | Add `ctx` param (reads retention values) |
| `handle_retrieval` | 1041 | `(...) → (..., ctx, paths)` |

Imports `engine.io`, `engine.profile`.

### Phase 4: `engine/consolidation.py`

| Function | Current line | Signature change |
|---|---|---|
| `score_for_promotion` | 1224 | Add `ctx` |
| `is_promotion_candidate` | 1257 | Add `ctx` |
| `detect_contradictions` | 1279 | No change |
| `review_quarantine` | 1300 | `() → (paths)` |
| `get_agents_md_suggestions` | 1386 | `(entries) → (entries, paths)` |
| `infer_sprint_number` | 1407 | No change |
| `save_session` / `load_session` / `clear_session` | 1420–1459 | `() → (paths)` |
| `handle_consolidate` | 1468 | `(sprint_number, confirm_reset, force) → (sprint, force, paths, ctx)` |
| `handle_confirm_reset` | 1660 | `() → (paths)` |

`confirm_reset` removed from `handle_consolidate` — `main()` dispatches directly to `handle_confirm_reset`.

### Phase 5: `engine/agents_review.py`

| Function | Current line |
|---|---|
| `parse_agents_sections` | 629 |
| `extract_section_keywords` | 664 |
| `tokenize_keywords` | 700 |
| `handle_review_agents` | 718 — add `paths, ctx` |
| `check_agents_conflict` | 797 — add `paths` |

### Phase 6: `engine/handlers.py`

| Function | Current line | Signature change |
|---|---|---|
| `handle_log` | 854 | Add `paths, ctx` |
| `handle_update` | 917 | Add `paths, ctx` |
| `handle_resolve` | 972 | Add `paths` |
| `handle_stats` | 1138 | Add `paths` |

Imports from `engine.io`, `engine.validation`, `engine.git_utils`, `engine.agents_review`. Never imports from `filter.py`.

### Phase 7: `engine/constants.py` + `engine/git_utils.py` → slim `filter.py`

**`engine/constants.py`**: All default values (lines 71–134 of current filter.py). Exported as a `DEFAULTS` dict.

**`engine/git_utils.py`**: `stamp_entry(entry, repo_root)` (line 539) + `check_staleness(entry, repo_root)` (line 1341). Both take `repo_root` explicitly.

**`filter.py` retains (~300 lines)**:
1. `ENGINE_VERSION` via `engine_version.py`
2. `Paths` dataclass + `resolve_memory_dir` + `setup_paths`
3. `load_config()` → returns dict, **no `globals().update()`**
4. `build_default_ctx()` → seeds from `engine.constants`, overlays config
5. `main()` — argparse, ctx/paths construction, dispatch
6. Imports from `engine.*`

Delete `globals().update(config)` at line 1758.

---

## Phase 8: Update Consumers

### 8.1 — `tests/test_memory.py`
- Delete `assert (memory_dir / "profile.py").exists()` — post-shim, no per-project profile.py.
- Add shim assertion: file < 50 lines AND contains `AGENT_MEMORY_DIR`.
- Existing subprocess tests unchanged.

### 8.2 — Deploy scripts
- Copy `src/engine/` recursively to `~/.agent-memory/engine/engine/`.
- Drop `src/profile.py` copy — `profile.py` is now `engine/profile.py`.
- Backup `engine/` before overwriting.

### 8.3 — Import path for profile.py
Move `src/profile.py` → `src/engine/profile.py`. Internal: `from .profile import ...`. External: `from engine.profile import ...`.

---

## Target Layout

```
src/
  filter.py              # CLI entry (<400 lines)
  engine_version.py      # Unchanged
  scaffold.py            # No engine-copy logic (shim only)
  update.py              # No engine-copy logic (shim only)
  engine/
    __init__.py
    constants.py         # Default values + schema sets
    io.py                # File I/O (read/write/quarantine)
    validation.py        # Schema validation + dedup matching
    retrieval.py         # Scoring + filtering
    consolidation.py     # Sleep Cycle + promotion
    agents_review.py     # AGENTS.md health
    git_utils.py         # stamp_entry + check_staleness
    handlers.py          # handle_log, handle_update, handle_resolve, handle_stats
    profile.py           # Developer profile (moved from src/)
```

---

## Safety Rules

1. **Diff gate after every phase.** Re-run all baselines, `Assert-NoDiff` against Phase 0 captures.
2. **Fresh fixture per run.** Retrieval bumps `access_count`.
3. **Test suite after every phase.** `pytest tests/test_memory.py -v` green.
4. **No circular imports.** `engine/` modules never import from `filter.py`. Verify: `Select-String -Path src\engine\*.py -Pattern 'from filter|import filter'` returns empty.
5. **No `globals().update`.** Config flows through `ctx` dict. Verify: `Select-String -Path src\filter.py -Pattern 'globals\(\).update'` returns empty.
6. **Rollback:** `git checkout -- src/filter.py; Remove-Item -Recurse src/engine/<module>.py`

---

## Success Criteria

- [ ] `filter.py` < 400 lines
- [ ] Every `engine/` module < 400 lines
- [ ] All Phase 0 baseline diffs zero after Phase 7
- [ ] Test suite passes after every phase
- [ ] No `globals().update()` in post-refactor code
- [ ] No circular imports from `engine/` → `filter.py`
- [ ] `--version` handled before `setup_paths()`/`load_config()` in `main()`
- [ ] No new dependencies added
