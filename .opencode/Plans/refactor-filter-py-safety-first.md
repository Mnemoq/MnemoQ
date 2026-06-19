# Refactor `filter.py` — Safety-First Plan

> **Goal:** Modularize `filter.py` (1786 lines → 9 modules, each < 400 lines) with zero behavioral drift.
> **Method:** Golden baseline → incremental extraction → byte-identical diff gate.
> **Prerequisite (BLOCKING):** Phase 1.3 (shim cutover) from `Memory-development-plan.md` is shipped — every active project (`magpie Swoop`, `Automo`, `Integrated AI UI`, `PixelPurge`) executes `~/.agent-memory/engine/filter.py` via shim, and no project carries its own copy of `filter.py` or `profile.py`. If this is not true, stop and complete Phase 1.3 first.
> **Prerequisite:** Existing test suite passes. Fixture data is reproducible from the spec in Phase 0.

---

## Contract Surface — What Must Not Change

### 1. CLI Interface

| Flag | Behavior |
|------|----------|
| `--version` | `agent-memory-engine v{VERSION}` to **stderr**, exit 0 |
| `--step N --components A,B [--files f1,f2] [--domain d]` | Retrieval: score, filter, print warnings/patterns/preferences |
| `--log '<json>'` / `--log-file PATH` | Validate, dedup-check, append to learnings.jsonl |
| `--update <ts> --log/--log-file` | Amend existing entry by timestamp |
| `--resolve <ts>` | Mark entry as resolved (partial update) |
| `--stats` | Print memory statistics |
| `--review-agents --step N [--threshold T]` | AGENTS.md section health report |
| `--consolidate [--sprint N] [--force]` | Sleep Cycle: archive, promotion report (force overwrites archive) |
| `--consolidate --confirm-reset` | Clear learnings.jsonl |
| `--memory-dir PATH` | Override memory directory |

**Mutual exclusions** unchanged from current behaviour at `filter.py:1740-1758`.

### 2. Stdout Format

Section headers (`## ⚠ WARNINGS`, `## 🎯 DEVELOPER PREFERENCES`, `## RELEVANT PATTERNS`, `## ⚡ ESCALATION`, `## STATS:`, `## SLEEP CYCLE DUE`, `## MEMORY STATS`) and exact line shapes must be byte-identical to the Phase 0 baseline. Log output emits `ADDED` / `DUPLICATE` / `CONFLICT` / `QUARANTINED` exactly.

### 3. Stderr Usage

`--version`, `WARNING:`, `ERROR:` go to stderr only. Stdout never carries version info.

### 4. File I/O Paths

| File | Read | Write |
|------|------|-------|
| `learnings.jsonl` | Always | `--log`, `--update`, `--resolve`, `--consolidate --confirm-reset`, retrieval access_count bump (atomic via tmp+replace) |
| `quarantine.jsonl` | `--consolidate` | `--log`/`--log-file` on rejection (append-only) |
| `config.json` | startup | never |
| `archive/sprint-N.jsonl` | never | `--consolidate` |
| `.consolidate_session.json` | `--confirm-reset` | `--consolidate` (10-min expiry) |
| `AGENTS.md` | `--review-agents`, `--consolidate` | never |
| `~/.agent-memory/developer-profile.json` | startup via `engine.profile` | never |

### 5. Config-Overridable Values

The current `globals().update(config)` call at `filter.py:1723` is **removed in Phase 7**. All overrides flow through the `ctx` dict (see Dependency Injection Shapes). Keys: `DECAY_RATE`, `SCORE_THRESHOLD`, `COMPONENT_WEIGHT`, `FILE_WEIGHT`, `DOMAIN_WEIGHT`, `NO_MATCH_WEIGHT`, `MAX_WARNINGS`, `MAX_PATTERNS`, `MINOR_RETENTION`, `MAJOR_RETENTION`, `ESCALATION_THRESHOLD`, `MAX_STEP`, `VALID_DOMAINS`, `VALID_SOURCE_AGENTS`, `VALID_RETRIEVAL_ONLY_AGENTS`, `DOMAIN_MAPPINGS`.

### 6. Return Codes

`--version`/`--step`/`--stats`/`--review-agents` → 0. `--log` → 0 even on DUPLICATE/CONFLICT, 1 on quarantine. `--update`/`--resolve` → 0 success, 1 not-found. `--consolidate` → 0 success, 1 archive-exists / no-entries. No args → 1.

### 7. Consumer Contracts

| Consumer | Post-shim contract |
|----------|--------------------|
| `scaffold.py` | Writes a ~12-line shim into `memory/filter.py`. Does NOT copy engine code. Verifies via `python memory/filter.py --step 1 --domain tooling`. |
| `update.py` | No-op for engine code in projects. Owns `projects.txt` registration + config-schema migration. Verifies via `--stats` through the shim. |
| `tests/test_memory.py` | Subprocess calls to `engine_dir / "filter.py"`. |
| `opencode.json` | `"python memory/filter.py *": "allow"` (unchanged — covers shim). |
| Agent prompts | `python memory/filter.py --step ...` and `--log-file` (unchanged). |

### 8. Version Embed Contract — RETIRED post-shim

Phase 1.3 removed the per-project copy of `filter.py`. `scaffold.py` / `update.py` no longer rewrite `_read_engine_version()`. The function may be renamed, moved, or refactored freely. Safety Rules #6 reflects this.

---

## Import Strategy

`filter.py:37` currently uses `from profile import ...`. Move `profile.py` into `engine/` as `engine/profile.py`:

- `engine/retrieval.py` → `from .profile import load_profile, get_profile_context`
- `filter.py` → `from engine.profile import load_profile, get_profile_context`
- Deploy script copies `src/engine/` recursively to `~/.agent-memory/engine/engine/`. No top-level `profile.py` after this refactor.

---

## Dependency Injection Shapes

All extracted modules receive dependencies via two dicts (`paths`, `ctx`) plus direct imports of sibling `engine/` modules. **No callable injection. No `globals().update`.**

### `paths` dict (built once after `setup_paths()`)

```python
paths = {
    "memory_dir": MEMORY_DIR, "learnings": LEARNINGS_PATH,
    "quarantine": QUARANTINE_PATH, "config": CONFIG_PATH,
    "archive_dir": ARCHIVE_DIR, "session_file": SESSION_FILE,
    "agents_md": AGENTS_MD_PATH, "repo_root": REPO_ROOT,
}
```

### `ctx` dict (seeded from `engine.constants`, then `ctx.update(load_config())`)

```python
ctx = {
    "decay_rate": ..., "score_threshold": ..., "component_weight": ...,
    "file_weight": ..., "domain_weight": ..., "no_match_weight": ...,
    "max_warnings": ..., "max_patterns": ...,
    "minor_retention": ..., "major_retention": ..., "escalation_threshold": ...,
    "max_step": ..., "session_expiry_minutes": ...,
    "valid_domains": ..., "valid_source_agents": ..., "valid_retrieval_agents": ...,
    "domain_mappings": ..., "valid_types": ..., "valid_severities": ...,
    "valid_scopes": ..., "valid_debt_levels": ..., "stop_words": ...,
}
```

### Target `main()` shape

```python
def main():
    args = parser.parse_args()

    # --version handled BEFORE setup_paths()/load_config() — zero-dependency
    if args.version:
        print(f"agent-memory-engine v{ENGINE_VERSION}", file=sys.stderr)
        sys.exit(0)

    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr.encoding != "utf-8":
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    setup_paths(args.memory_dir)
    paths = build_paths()

    overrides = load_config()        # returns dict; does NOT mutate globals
    ctx = build_default_ctx()        # seeded from engine.constants
    ctx.update(overrides)            # config overrides merged in

    if args.step is not None and (args.components or args.files or args.domain):
        return handle_retrieval(args.step, args.components, args.files, args.domain, ctx, paths)
    elif args.log or args.log_file:
        json_str = args.log or open(args.log_file).read()
        if args.update:
            return handle_update(args.update, json_str, paths, ctx)
        return handle_log(json_str, paths, ctx)
    elif args.resolve:
        return handle_resolve(args.resolve, paths)
    elif args.stats:
        return handle_stats(paths)
    elif args.review_agents:
        return handle_review_agents(args.step, args.threshold, paths, ctx)
    elif args.consolidate:
        if args.confirm_reset:
            return handle_confirm_reset(paths)
        return handle_consolidate(args.sprint, args.force, paths, ctx)
```

### `check_staleness` and `stamp_entry` cwd forwarding

Both live in `engine/git_utils.py` (Phase 7). Each takes `repo_root` explicitly:

```python
from engine.git_utils import check_staleness, stamp_entry

def handle_consolidate(sprint, force, paths, ctx):
    repo_root = paths["repo_root"]
    for entry in read_learnings(paths["learnings"]):
        staleness = check_staleness(entry, repo_root)
```

---

## Phase 0: Golden Baseline (Read-Only)

### Step 0.0: Source fixture data

> Fixture entries are loaded by file copy, not via `--log`, so they bypass `validate_entry`. Even so, all field values are kept legal per `VALID_TYPES` (`bug_fix` / `optimization` / `architectural_pattern`), `VALID_DOMAINS`, `VALID_SEVERITIES` (`minor` / `major` / `critical`), `VALID_SCOPES` (`file` / `module` / `system`), `VALID_DEBT_LEVELS` (`proper` / `workaround` / `temporary`) — fixture is re-derivable from the schema.

**Fixture (6 entries):**

| # | type | domain | severity | step | components | resolved | Purpose |
|---|------|--------|----------|------|------------|----------|---------|
| 1 | bug_fix | physics | minor | 5 | Player,Enemy | false | Component match; resolvable; updatable |
| 2 | architectural_pattern | tooling | critical | 0 | Build,Config | false | Domain match + **escalation** path (`--step 30` → step_diff=30 with `severity=critical`) |
| 3 | bug_fix | physics | minor | 3 | Player | false | Second physics entry for multi-match |
| 4 | architectural_pattern | tooling | minor | 1 | Test | false | Developer preference display |
| 5 | bug_fix | performance | major | 20 | Shader,Light | true | Resolved entry for stats count |
| 6 | architectural_pattern | physics | minor | 8 | Player,Physics | false | Reinforcement candidate for promotion |

**JSON template** — every field stays legal per the schema:

```json
{
  "timestamp": "2026-01-15T10:00:00Z",
  "step": 5, "source_agent": "gm", "type": "bug_fix", "domain": "physics",
  "components": ["Player", "Enemy"], "files_touched": ["src/physics/collision.py"],
  "trigger": "When two rigidbodies overlap",
  "action": "ALWAYS check penetration depth before resolving",
  "reason": "Tunneling causes objects to pass through walls",
  "importance": 4, "severity": "minor", "scope": "module", "debt_level": "proper",
  "resolved": false, "access_count": 0, "reinforced_count": 0, "verified": true,
  "symptoms": "objects pass through walls; collision missed"
}
```

Adjust per-row fields per the table. Entry #2 must have `step: 0` AND `severity: "critical"`. Entry #5 must have `resolved: true`.

**Synthetic AGENTS.md:**

```markdown
# Agent Guidelines

## Physics
Collision detection uses AABB broadphase. Always check penetration depth.

## Tooling
Build system uses CMake. Config lives in config.json.

## Performance
Shader pipeline: vertex -> fragment -> compute.
```

### Step 0.1: Read-only baselines

```bash
# Fresh fixture per run — retrieval mutates access_count
restore() { cp memory/.baseline/fixture-learnings.jsonl memory/learnings.jsonl; }

restore; python src/filter.py --stats > memory/.baseline/stats.txt 2>&1
restore; python src/filter.py --step 5 --components Player,Enemy --domain physics > memory/.baseline/retrieval-component.txt 2>&1
restore; python src/filter.py --step 10 --domain tooling > memory/.baseline/retrieval-domain.txt 2>&1
restore; python src/filter.py --step 1 --components NonExistent --domain nonexistent > memory/.baseline/retrieval-empty.txt 2>&1
restore; python src/filter.py --step 5 --components Player,Enemy > memory/.baseline/retrieval-no-domain.txt 2>&1

# Step alone (no qualifiers) — should print help and exit 1
restore; python src/filter.py --step 5 > memory/.baseline/step-alone.txt 2>&1

# Escalation path — entry #2 step=0 + critical, current_step=30 → step_diff=30
restore; python src/filter.py --step 30 --domain tooling > memory/.baseline/retrieval-escalation.txt 2>&1

# Capture file mutation (access_count bump)
restore; python src/filter.py --step 5 --components Player,Enemy --domain physics > /dev/null 2>&1
cp memory/learnings.jsonl memory/.baseline/learnings-after-retrieval.jsonl

restore; cp memory/.baseline/fixture-agents.md memory/AGENTS.md
python src/filter.py --review-agents --step 5 > memory/.baseline/review-agents.txt 2>&1
```

### Step 0.2: Write-path baselines

```bash
# Log new + capture resulting learnings.jsonl
restore
echo '{"step":1,"source_agent":"gm","type":"bug_fix","domain":"tooling","components":["Test"],"files_touched":["test.py"],"trigger":"baseline","action":"ALWAYS test","reason":"Baseline","importance":5,"severity":"minor"}' > memory/.baseline/test-entry.json
python src/filter.py --log-file memory/.baseline/test-entry.json > memory/.baseline/log-new.txt 2>&1
cp memory/learnings.jsonl memory/.baseline/learnings-after-log.jsonl

# Duplicate
restore; python src/filter.py --log-file memory/.baseline/test-entry.json > memory/.baseline/log-dup1.txt 2>&1
python src/filter.py --log-file memory/.baseline/test-entry.json > memory/.baseline/log-dup2.txt 2>&1

# Quarantine — capture stdout AND quarantine.jsonl
restore; rm -f memory/quarantine.jsonl
echo '{"step":-1}' > memory/.baseline/test-invalid.json
python src/filter.py --log-file memory/.baseline/test-invalid.json > memory/.baseline/log-quarantine.txt 2>&1
cp memory/quarantine.jsonl memory/.baseline/quarantine-after-log.jsonl

# Update + resolve as before (read FIRST_TS from learnings.jsonl)
```

### Step 0.3: Consolidation baselines

```bash
restore; cp memory/.baseline/fixture-agents.md memory/AGENTS.md; rm -rf memory/archive
python src/filter.py --consolidate --sprint 1 > memory/.baseline/consolidate.txt 2>&1
cp memory/learnings.jsonl memory/.baseline/learnings-after-consolidate.jsonl
[ -f memory/archive/sprint-1.jsonl ] && cp memory/archive/sprint-1.jsonl memory/.baseline/sprint-1.jsonl

# --force overwrite (archive already exists from previous step)
restore; cp memory/.baseline/fixture-agents.md memory/AGENTS.md
python src/filter.py --consolidate --sprint 1 --force > memory/.baseline/consolidate-force.txt 2>&1

# Confirm-reset
restore; python src/filter.py --consolidate --confirm-reset > memory/.baseline/confirm-reset.txt 2>&1
cp memory/learnings.jsonl memory/.baseline/learnings-after-reset.jsonl
```

### Step 0.4: Version baseline

```bash
python src/filter.py --version > memory/.baseline/version-stderr.txt 2>&1
```

### Step 0.5: PowerShell equivalents (Windows host)

The user's host is Windows. Every bash block above has a PowerShell equivalent — use these directly, do not require Git Bash / WSL.

```powershell
function Restore-Fixture { Copy-Item memory\.baseline\fixture-learnings.jsonl memory\learnings.jsonl -Force }
function Assert-NoDiff($expected, $actual) {
    $diff = Compare-Object (Get-Content $expected) (Get-Content $actual)
    if ($diff) { Write-Error "DIFF: $expected vs $actual"; $diff | Format-Table; exit 1 }
}

# 0.1 read-only
Restore-Fixture; & python src\filter.py --stats *> memory\.baseline\stats.txt
Restore-Fixture; & python src\filter.py --step 5 --components Player,Enemy --domain physics *> memory\.baseline\retrieval-component.txt
Restore-Fixture; & python src\filter.py --step 10 --domain tooling *> memory\.baseline\retrieval-domain.txt
Restore-Fixture; & python src\filter.py --step 1 --components NonExistent --domain nonexistent *> memory\.baseline\retrieval-empty.txt
Restore-Fixture; & python src\filter.py --step 5 --components Player,Enemy *> memory\.baseline\retrieval-no-domain.txt
Restore-Fixture; & python src\filter.py --step 5 *> memory\.baseline\step-alone.txt
Restore-Fixture; & python src\filter.py --step 30 --domain tooling *> memory\.baseline\retrieval-escalation.txt
Restore-Fixture; & python src\filter.py --step 5 --components Player,Enemy --domain physics *> $null
Copy-Item memory\learnings.jsonl memory\.baseline\learnings-after-retrieval.jsonl -Force

# 0.2 write-path (quarantine variant captures quarantine.jsonl too)
Restore-Fixture; Remove-Item memory\quarantine.jsonl -ErrorAction SilentlyContinue
'{"step":-1}' | Set-Content memory\.baseline\test-invalid.json -NoNewline
& python src\filter.py --log-file memory\.baseline\test-invalid.json *> memory\.baseline\log-quarantine.txt
Copy-Item memory\quarantine.jsonl memory\.baseline\quarantine-after-log.jsonl -Force

# 0.3 force
Restore-Fixture; Copy-Item memory\.baseline\fixture-agents.md memory\AGENTS.md -Force
& python src\filter.py --consolidate --sprint 1 --force *> memory\.baseline\consolidate-force.txt
```

**Gate:** All baseline files saved. No code changes yet.

---

## Phase 1: Extract I/O Layer → `engine/io.py`

### Functions to extract

```python
read_learnings(learnings_path)           # filter.py:340
append_learning(learnings_path, entry)   # filter.py:358
write_learnings(learnings_path, entries) # filter.py:377
quarantine(quarantine_path, raw_input, reason) # filter.py:409
```

### SIGNATURE CHANGES

**Current source**: `read_learnings()`, `append_learning(entry)`, `write_learnings(entries)`, `quarantine(raw_input, reason)` — read `LEARNINGS_PATH` / `QUARANTINE_PATH` from module globals.

**Target signatures**: All gain path parameters as shown above. When extracting, replace global reads with the corresponding parameters. Callers in `filter.py` pass `paths["learnings"]` / `paths["quarantine"]`.

**Why**: Extracted modules cannot read globals from `filter.py`. Path parameters make dependencies explicit and testable.

### Validation gate

```bash
restore; python src/filter.py --stats > memory/.baseline/stats-p1.txt 2>&1
diff memory/.baseline/stats.txt memory/.baseline/stats-p1.txt

restore; python src/filter.py --log-file memory/.baseline/test-entry.json > memory/.baseline/log-new-p1.txt 2>&1
diff memory/.baseline/log-new.txt memory/.baseline/log-new-p1.txt
diff memory/.baseline/learnings-after-log.jsonl memory/learnings.jsonl
```

PowerShell: same pattern with `Restore-Fixture` + `Assert-NoDiff`.

**Gate:** All diffs zero. `pytest tests/test_memory.py -v` green.

---

## Phase 2: Extract Validation → `engine/validation.py`

### Functions to extract

```python
validate_entry(entry, ctx)        # filter.py:423
jaccard_similarity(t1, t2)        # filter.py:549
actions_oppose(a1, a2)            # filter.py:562
find_best_match(entry, entries)   # filter.py:573
```

### Dependencies

`validate_entry` reads only from `ctx` — never module globals. Keys used: `max_step`, `valid_source_agents`, `valid_types`, `valid_domains`, `valid_severities`, `valid_scopes`, `valid_debt_levels`.

### Validation gate

Phase 1 diffs plus quarantine file diff:

```bash
restore; rm -f memory/quarantine.jsonl
python src/filter.py --log-file memory/.baseline/test-invalid.json > memory/.baseline/log-quarantine-p2.txt 2>&1
diff memory/.baseline/log-quarantine.txt memory/.baseline/log-quarantine-p2.txt
diff memory/.baseline/quarantine-after-log.jsonl memory/quarantine.jsonl
```

**Gate:** Diffs zero. Tests pass.

---

## Phase 3: Extract Retrieval → `engine/retrieval.py`

### Functions to extract

```python
score_entry(entry, current_step, task_components, task_files, task_domain, ctx)  # filter.py:976
is_in_retention(entry, current_step, ctx)  # filter.py:996
handle_retrieval(current_step, task_components, task_files, task_domain, ctx, paths)  # filter.py:1011
```

### Dependencies

- Reads from `ctx` (no module globals): `decay_rate`, `score_threshold`, scoring weights, retention values, `escalation_threshold`, `max_warnings`, `max_patterns`, `max_step`, `domain_mappings`.
- Imports `read_learnings`, `write_learnings` from `engine.io`.
- Imports `load_profile`, `get_profile_context` from `engine.profile`.

### `task_domain=None` edge case

When `--step` is used WITHOUT `--domain`, `task_domain` is `None`. `get_profile_context()` skips stack-specific preferences. Phase 0.1 baseline `retrieval-no-domain.txt` guards this.

### Validation gate

```bash
restore; python src/filter.py --step 5 --components Player,Enemy --domain physics > memory/.baseline/retrieval-component-p3.txt 2>&1
diff memory/.baseline/retrieval-component.txt memory/.baseline/retrieval-component-p3.txt

restore; python src/filter.py --step 10 --domain tooling > memory/.baseline/retrieval-domain-p3.txt 2>&1
diff memory/.baseline/retrieval-domain.txt memory/.baseline/retrieval-domain-p3.txt

restore; python src/filter.py --step 1 --components NonExistent --domain nonexistent > memory/.baseline/retrieval-empty-p3.txt 2>&1
diff memory/.baseline/retrieval-empty.txt memory/.baseline/retrieval-empty-p3.txt

restore; python src/filter.py --step 5 --components Player,Enemy > memory/.baseline/retrieval-no-domain-p3.txt 2>&1
diff memory/.baseline/retrieval-no-domain.txt memory/.baseline/retrieval-no-domain-p3.txt

# Step alone (no qualifiers) — help + exit 1
restore; python src/filter.py --step 5 > memory/.baseline/step-alone-p3.txt 2>&1
diff memory/.baseline/step-alone.txt memory/.baseline/step-alone-p3.txt

# Escalation
restore; python src/filter.py --step 30 --domain tooling > memory/.baseline/retrieval-escalation-p3.txt 2>&1
diff memory/.baseline/retrieval-escalation.txt memory/.baseline/retrieval-escalation-p3.txt

# File mutation (access_count bump)
restore; python src/filter.py --step 5 --components Player,Enemy --domain physics > /dev/null 2>&1
diff memory/.baseline/learnings-after-retrieval.jsonl memory/learnings.jsonl
```

**Gate:** Diffs zero. Tests pass.

---

## Phase 4: Extract Consolidation → `engine/consolidation.py`

### Functions to extract

```python
score_for_promotion(entry, current_step, ctx)    # filter.py:1194
is_promotion_candidate(entry, current_step, ctx)  # filter.py:1227
detect_contradictions(entries)                    # filter.py:1249
review_quarantine(quarantine_path)                # filter.py:1270
get_agents_md_suggestions(entries, agents_md_path) # filter.py:1356
infer_sprint_number(entries)                      # filter.py:1377
save_session(session_file, sprint_number)         # filter.py:1390
load_session(session_file)                        # filter.py:1403
clear_session(session_file)                       # filter.py:1429
handle_consolidate(sprint, force, paths, ctx)     # filter.py:1438
handle_confirm_reset(paths)                       # filter.py:1630
```

`check_staleness` is **NOT** extracted here — it moves to `engine/git_utils.py` in Phase 7 with `stamp_entry` (both call git). `handle_consolidate` imports it: `from engine.git_utils import check_staleness`.

### Dispatch and signature changes for `handle_consolidate`

**Current source** (filter.py:1438): `handle_consolidate(sprint_number=None, confirm_reset=False, force=False)`

**Target signature**: `handle_consolidate(sprint, force, paths, ctx)`

**Changes**:
- `sprint_number` → `sprint` (rename for consistency)
- `confirm_reset` parameter removed — `main()` now calls `handle_confirm_reset(paths)` directly when `args.confirm_reset` is true
- `force` parameter retained (position unchanged)
- `paths` and `ctx` parameters added (dependency injection)

**Why**: Separating `confirm_reset` into its own handler simplifies dispatch. The diff gate verifies byte-identical output.

### Parameter forwarding

```python
from engine.io import read_learnings, write_learnings
from engine.git_utils import check_staleness   # Phase 7

def handle_consolidate(sprint, force, paths, ctx):
    entries = read_learnings(paths["learnings"])
    repo_root = paths["repo_root"]
    for entry in entries:
        staleness = check_staleness(entry, repo_root)
    suggestions = get_agents_md_suggestions(entries, paths["agents_md"])
    quarantine_results = review_quarantine(paths["quarantine"])
    save_session(paths["session_file"], sprint_number)

def handle_confirm_reset(paths):
    learnings_path = paths["learnings"]
    session_file = paths["session_file"]
```

### Validation gate

```bash
restore; cp memory/.baseline/fixture-agents.md memory/AGENTS.md; rm -rf memory/archive
python src/filter.py --consolidate --sprint 1 > memory/.baseline/consolidate-p4.txt 2>&1
diff memory/.baseline/consolidate.txt memory/.baseline/consolidate-p4.txt
diff memory/.baseline/sprint-1.jsonl memory/archive/sprint-1.jsonl

# --force overwrite
restore; cp memory/.baseline/fixture-agents.md memory/AGENTS.md
python src/filter.py --consolidate --sprint 1 --force > memory/.baseline/consolidate-force-p4.txt 2>&1
diff memory/.baseline/consolidate-force.txt memory/.baseline/consolidate-force-p4.txt

# Confirm-reset
restore; python src/filter.py --consolidate --confirm-reset > memory/.baseline/confirm-reset-p4.txt 2>&1
diff memory/.baseline/confirm-reset.txt memory/.baseline/confirm-reset-p4.txt
diff memory/.baseline/learnings-after-reset.jsonl memory/learnings.jsonl
```

**Gate:** Diffs zero. Tests pass.

---

## Phase 5: Extract AGENTS.md Review → `engine/agents_review.py`

```python
parse_agents_sections(agents_md_path)      # filter.py:599
extract_section_keywords(heading, content)  # filter.py:634
tokenize_keywords(text)                     # filter.py:670
handle_review_agents(current_step, threshold, paths, ctx) # filter.py:688
check_agents_conflict(entry, agents_md_path) # filter.py:767
```

### Validation gate

```bash
restore; cp memory/.baseline/fixture-agents.md memory/AGENTS.md
python src/filter.py --review-agents --step 5 > memory/.baseline/review-agents-p5.txt 2>&1
diff memory/.baseline/review-agents.txt memory/.baseline/review-agents-p5.txt
```

**Gate:** Diff zero. Tests pass.

---

## Phase 6: Extract Handlers → `engine/handlers.py`

### Functions to extract

```python
handle_log(json_str, paths, ctx)        # filter.py:824
handle_update(ts, json_str, paths, ctx) # filter.py:887
handle_resolve(ts, paths)               # filter.py:942
handle_stats(paths)                     # filter.py:1108
```

### Imports — no callable injection

```python
from engine.io import read_learnings, write_learnings, append_learning, quarantine
from engine.validation import validate_entry, find_best_match
from engine.git_utils import stamp_entry           # Phase 7 — direct import
from engine.agents_review import check_agents_conflict
```

`handlers.py` never imports from `filter.py` — no circular imports.

### What `stamp_entry()` does

`stamp_entry(entry, repo_root)` (originally `filter.py:509`) injects git commit hash + timestamp. Calls `subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo_root)`. Lives in `engine/git_utils.py` (Phase 7).

### Inside `handle_log`

```python
def handle_log(json_str, paths, ctx):
    entry = json.loads(json_str)
    errors = validate_entry(entry, ctx)
    if errors:
        quarantine(paths["quarantine"], json_str, "; ".join(errors))
        return 1
    entry = stamp_entry(entry, paths["repo_root"])
    conflict = check_agents_conflict(entry, paths["agents_md"])
    entries = read_learnings(paths["learnings"])
    match = find_best_match(entry, entries)
    # dedup logic, append, write
    append_learning(paths["learnings"], entry)
```

### Validation gate

Re-run all of Phase 0.1 / 0.2 / 0.3 baselines and diff. Add quarantine file diff to the quarantine case. Add `learnings-after-log.jsonl` diff to the new-log case.

**Gate:** All diffs zero. Tests pass.

---

## Phase 7: Extract Constants + Git Utils → Slim `filter.py`

### `engine/constants.py`

All default values (`DECAY_RATE`, `SCORE_THRESHOLD`, scoring weights, retention values, `MAX_STEP`, `SESSION_EXPIRY_MINUTES`, `VALID_*` sets, `STOP_WORDS`, `DOMAIN_MAPPINGS = None`).

**Precedence chain for `DOMAIN_MAPPINGS`**:
1. `config.json` (if present) — highest priority, loaded via `load_config()`
2. `~/.agent-memory/developer-profile.json` — profile-specific mappings
3. `profile.py` hardcoded defaults (`DEFAULT_DOMAIN_MAPPINGS`)
4. `constants.py` value of `None` acts as "not configured" sentinel — triggers fallback to step 2/3

When `ctx["domain_mappings"]` is `None`, `get_profile_context()` uses profile.py's built-in defaults. This matches current behavior.

### `engine/git_utils.py`

All git subprocess calls. Two functions:

```python
import subprocess
from datetime import datetime, timezone

def stamp_entry(entry, repo_root):
    if not entry.get("commit"):
        try:
            entry["commit"] = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True, timeout=5
            ).strip()
        except Exception:
            entry["commit"] = "unknown"
    if not entry.get("ts"):
        entry["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return entry

def check_staleness(entry, repo_root):
    """Determine staleness via git log (originally filter.py:1311)."""
    # ...
```

### `load_config()` returns a dict — no globals mutation

```python
def main():
    overrides = load_config()       # returns dict
    ctx = build_default_ctx()       # seeded from engine.constants
    ctx.update(overrides)
```

The `globals().update(config)` call at `filter.py:1723` is **deleted**. Extracted modules read everything from `ctx` parameters; nothing reads module globals at call time.

### What remains in `filter.py`

1. `_read_engine_version()` — regex contract retired post-shim; safe to refactor.
2. `ENGINE_VERSION = _read_engine_version()`.
3. `setup_paths(memory_dir_arg)` + `build_paths()` — path resolution + dict.
4. `load_config()` — returns override dict (no `globals().update`).
5. `build_default_ctx()` — seeds `ctx` from `engine.constants`.
6. `main()` — argparse + ctx/paths construction + dispatch.
7. Module imports: `from engine import io, validation, retrieval, consolidation, agents_review, handlers, git_utils, profile`.

Target: `filter.py` < 400 lines.

### Validation gate

Re-run every Phase 0.x baseline against the refactored code; all diffs must be zero. `pytest tests/test_memory.py -v`. `python src/filter.py --version`. `python src/scaffold.py --help`. `python src/update.py --version`.

---

## Phase 8: Update Consumers (post-shim, slimmed)

### 8.0 — Removed (post-shim)

`scaffold.py` and `update.py` no longer copy engine code into projects. The shim in `memory/filter.py` (~12 lines) sets `AGENT_MEMORY_DIR` and execs `~/.agent-memory/engine/filter.py`. After Phase 1.3, the only files either tool touches in a project's `memory/` are config and data (`config.json`, `learnings.jsonl`, `quarantine.jsonl`, `archive/`, `AGENTS.md`, `SYSTEM_INVARIANTS.md`, `HANDOFF.md`).

The version-embed regex contract is **retired**. `_read_engine_version()` is no longer subject to shape constraints.

### 8.1: `tests/test_memory.py`

**BLOCKING.** Required changes:

- `test_scaffold_creates_memory` (`test_memory.py:602-606`):
  - **Delete** `assert (memory_dir / "profile.py").exists()` at line 603 — post-shim, projects do not carry `profile.py`.
  - Keep `assert (memory_dir / "filter.py").exists()`.
  - **Add** an assertion that `memory/filter.py` is a shim: file < 50 lines AND contains `AGENT_MEMORY_DIR`.
- Remove any other tests checking for top-level `profile.py` in `memory/`.
- All subprocess-based tests (17 tests) remain unchanged.
- Optional: add unit tests for `engine.*` modules.

**Verify:** `pytest tests/test_memory.py -v` passes.

### 8.2: `deploy.ps1` / `deploy.sh`

After refactor:

- Copy `src/filter.py` (unchanged) to `~/.agent-memory/engine/filter.py`.
- Copy `src/engine/` recursively to `~/.agent-memory/engine/engine/`.
- Drop all `src/profile.py` references — `profile.py` is now inside `engine/`.
- **Backup `engine/` before overwriting:**

  ```powershell
  if (Test-Path "$LIVE_ENGINE\engine") {
      Copy-Item -Recurse "$LIVE_ENGINE\engine" "$BACKUP_DIR\engine" -Force
  }
  ```

  ```bash
  if [ -d "$LIVE_ENGINE/engine" ]; then
      cp -r "$LIVE_ENGINE/engine" "$BACKUP_DIR/engine"
  fi
  ```

- Verification still runs `python "$LIVE_ENGINE/filter.py" --stats` against any registered project from `projects.txt`.

### 8.3: Post-deploy verification gate

```powershell
# 1. Deployed structure
Test-Path ~\.agent-memory\engine\filter.py
Test-Path ~\.agent-memory\engine\engine\__init__.py
Test-Path ~\.agent-memory\engine\engine\io.py
Test-Path ~\.agent-memory\engine\engine\git_utils.py
Test-Path ~\.agent-memory\engine\engine\profile.py

# 2. Test suite
pytest tests/test_memory.py -v

# 3. CLI through the live engine
& python ~\.agent-memory\engine\filter.py --version

# 4. Each registered project still works through its shim
foreach ($p in (Get-Content ~\.agent-memory\engine\projects.txt | Where-Object { $_ -and -not $_.StartsWith('#') })) {
    & python "$p\memory\filter.py" --stats | Select-String 'MEMORY STATS'
}

# 5. Circular import check
Select-String -Path ~\.agent-memory\engine\engine\*.py -Pattern '^from filter import|^import filter'
# Output must be empty.
```

**Gate:** All checks pass.

---

## Safety Rules

### Never skip

- **Diff gate after every phase.** Zero tolerance.
- **Fresh fixture copy per run.** Retrieval bumps `access_count`.
- **Test suite after every phase.** `pytest tests/test_memory.py -v` stays green.

### Rollback

```bash
git checkout -- src/filter.py
rm -rf src/engine/<phase-module>.py
```

### What to watch for

1. **No more `globals().update(config)`.** All config flows through the `ctx` dict built in `main()`. Extracted modules never read module-level constants at call time — they read `ctx[key]`. Verify by grepping every `engine/*.py` for direct constant reads (`DECAY_RATE`, `MAX_STEP`, etc.) — none should appear outside `engine/constants.py`.
2. **Import timing.** Extracted modules are imported at module load. Config overrides happen at runtime in `main()`. Modules must not read globals at import time.
3. **Stdout encoding.** `main()` reconfigures stdout to UTF-8 (after `--version` exit). Extracted modules don't change encoding.
4. **`sys.exit()` in helpers.** Stays in `main()` or returned as int that `main()` translates.
5. **`subprocess` calls.** All git work lives in `engine/git_utils.py` (`stamp_entry`, `check_staleness`). Both take `repo_root` explicitly; neither reads any module global.
6. **Version embed regex — RETIRED.** `_read_engine_version()` may be renamed/moved/refactored freely after Phase 1.3 shim cutover.
7. **`profile.py` import path.** After moving to `engine/profile.py`, use relative imports inside `engine/` and `from engine.profile import ...` from `filter.py`. No top-level `profile.py`.
8. **Circular imports.** `handlers.py` imports from `engine/*` only — never from `filter.py`. Verify with grep returning no matches.

---

## Module Target Layout (Post-Refactor)

```
src/
  filter.py              # CLI entry point (< 400 lines)
  scaffold.py            # No engine-copy logic post-shim
  update.py              # No engine-copy logic post-shim
  engine/
    __init__.py          # Empty
    constants.py         # Default constant values + schema sets
    io.py                # File I/O (read/write/quarantine)
    validation.py        # Schema validation + dedup
    retrieval.py         # Scoring + filtering
    consolidation.py     # Sleep Cycle + promotion
    agents_review.py     # AGENTS.md health
    git_utils.py         # stamp_entry + check_staleness (all git subprocess calls)
    handlers.py          # Orchestrators (handle_log, handle_update, handle_resolve, handle_stats)
    profile.py           # Developer profile loader (moved from src/)
```

**Deployed layout** (post-shim):

```
project/
  memory/
    filter.py            # ~12-line shim (sets AGENT_MEMORY_DIR + execs ~/.agent-memory/engine/filter.py)
    config.json
    learnings.jsonl
    quarantine.jsonl
    archive/
    AGENTS.md
    SYSTEM_INVARIANTS.md
    HANDOFF.md

~/.agent-memory/engine/
  filter.py              # CLI entry point
  engine/                # Full package
    __init__.py
    constants.py
    io.py
    validation.py
    retrieval.py
    consolidation.py
    agents_review.py
    git_utils.py
    handlers.py
    profile.py
  VERSION
  projects.txt
```

---

## Success Criteria

- [ ] Phase 1.3 shim cutover is verified shipped before this refactor begins.
- [ ] `filter.py` < 400 lines.
- [ ] Every extracted module < 400 lines.
- [ ] All Phase 0 baseline diffs zero (Phase 7 gate).
- [ ] Full test suite passes after every phase.
- [ ] `engine/git_utils.py` owns all git subprocess calls (`stamp_entry`, `check_staleness`).
- [ ] `load_config()` returns a dict; no `globals().update()` anywhere in post-refactor `filter.py`.
- [ ] `--force` flag remains correctly propagated through `handle_consolidate` (validated by `consolidate-force` baseline diff).
- [ ] `--version` handled before `setup_paths()` / `load_config()` in `main()`.
- [ ] `task_domain=None` retrieval baseline passes diff gate.
- [ ] Escalation baseline (`--step 30 --domain tooling`) populates the `## ⚡ ESCALATION` section and passes diff gate.
- [ ] Quarantine file (`quarantine.jsonl`) byte-identical to baseline after a quarantined log.
- [ ] `learnings.jsonl` byte-identical to `learnings-after-retrieval.jsonl` after a retrieval call (access_count bump preserved).
- [ ] `tests/test_memory.py:603` `profile.py` assertion deleted; shim assertion added.
- [ ] No circular imports: no `engine/` module imports from `filter.py`.
- [ ] No `engine/*.py` module reads top-level constants from `filter.py` at call time.
- [ ] Fixture data is reproducible from the schema (every field legal per `VALID_TYPES` / `VALID_SCOPES` / `VALID_DEBT_LEVELS`).
- [ ] No new dependencies added.
