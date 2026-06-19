# Phase 1.3 Shim Cutover - Implementation Handoff

## Overview

Phase 1.3 replaces full engine copies in each project with a thin shim that delegates to the central engine. This enables instant propagation of engine changes without running `update.py`.

## Implementation Summary

### Files Created

#### `src/shim.py` (NEW)
Shared shim module with template and detection logic:

```python
"""Shim template and utilities for delegating to central engine."""

# Sentinel comment for exact shim detection
SHIM_SENTINEL = "# AGENT-MEMORY-SHIM v1"

SHIM_TEMPLATE = f'''#!/usr/bin/env python3
{SHIM_SENTINEL}
"""Thin shim that delegates to the central engine."""
import os
import sys

# Set AGENT_MEMORY_DIR so the engine knows where to find data
os.environ["AGENT_MEMORY_DIR"] = os.path.dirname(os.path.abspath(__file__))

# Exec the central engine
engine_path = os.path.expanduser("~/.agent-memory/engine/filter.py")
if not os.path.exists(engine_path):
    print(f"ERROR: Engine not found at {{engine_path}}", file=sys.stderr)
    print("Run the deploy script to install the engine.", file=sys.stderr)
    sys.exit(1)

# Replace this process with the engine
# Note: On Windows, os.execv spawns a new process (PID changes).
# On Unix, it replaces the current process in-place.
os.execv(sys.executable, [sys.executable, engine_path] + sys.argv[1:])
'''


def is_shim(file_path):
    """Check if a file is a shim (vs full engine copy)."""
    if not file_path.exists():
        return False
    try:
        first_line = file_path.read_text(encoding='utf-8').split('\n', 2)[1]
        return first_line.strip() == SHIM_SENTINEL
    except (IOError, IndexError):
        return False
```

**Key design decisions:**
- Sentinel comment enables exact detection (vs heuristic string matching)
- `os.execv` preserves exit codes and I/O streams
- Windows PID change documented in comments

---

### Files Modified

#### `src/scaffold.py`

**Changes:**
1. Added import: `from shim import SHIM_TEMPLATE, is_shim`
2. Removed `profile.py` from `check_prerequisites()` (no longer needed per-project)
3. Replaced `copy_engine_files()` to write shim instead of copying files
4. Updated message from "Copied filter.py and profile.py" to "Wrote shim to filter.py"

**Before:**
```python
def copy_engine_files(target_memory, force):
    """Copy filter.py and profile.py from engine to target."""
    # ... 25 lines of regex-based version embedding ...
```

**After:**
```python
def copy_engine_files(target_memory, force):
    """Write shim to target project's memory directory."""
    target_memory.mkdir(parents=True, exist_ok=True)
    
    # Write shim
    shim_path = target_memory / "filter.py"
    if shim_path.exists() and not force:
        if is_shim(shim_path):
            return  # Already a shim, nothing to do
        # It's an old full copy, overwrite
    
    shim_path.write_text(SHIM_TEMPLATE, encoding='utf-8')
    
    # Remove profile.py if it exists (no longer needed)
    profile_path = target_memory / "profile.py"
    if profile_path.exists():
        profile_path.unlink()
```

**Impact:** New scaffolded projects get a shim instead of full engine copy.

---

#### `src/update.py`

**Changes:**
1. Added import: `from shim import SHIM_TEMPLATE, is_shim`
2. Added `migrate_to_shim()` function (57 lines)
3. Simplified `update_engine_files()` from 40 lines to 11 lines
4. Added `--migrate-to-shim` CLI flag
5. Updated `update_project()` to pass `dry_run` to `update_engine_files()`
6. Updated pycache clearing logic to skip shim projects

**New function: `migrate_to_shim()`**
```python
def migrate_to_shim(project_path, dry_run=False):
    """Replace full engine copy with shim.
    
    Creates backup of existing files, then writes shim.
    Cleans stale __pycache__/ after migration.
    """
    memory_dir = project_path / "memory"
    if not memory_dir.exists():
        return False, "No memory/ directory found"
    
    # Check if already a shim
    filter_path = memory_dir / "filter.py"
    if is_shim(filter_path):
        return True, "Already a shim"
    
    # Create backup
    if not dry_run:
        backup_dir = memory_dir / "backups" / datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        # Backup existing files
        for filename in ["filter.py", "profile.py"]:
            src = memory_dir / filename
            if src.exists():
                shutil.copy2(src, backup_dir / filename)
    
    # Write shim
    if not dry_run:
        filter_path.write_text(SHIM_TEMPLATE, encoding='utf-8')
        
        # Remove profile.py (no longer needed)
        profile_path = memory_dir / "profile.py"
        if profile_path.exists():
            profile_path.unlink()
        
        # Clean stale __pycache__/
        pycache_dir = memory_dir / "__pycache__"
        if pycache_dir.exists():
            shutil.rmtree(pycache_dir)
    
    return True, "Migrated to shim"
```

**Simplified `update_engine_files()`:**
```python
def update_engine_files(project_path, engine_path, dry_run=False):
    """For shim projects, no file updates needed. For legacy copies, migrate to shim."""
    memory_dir = project_path / "memory"
    filter_path = memory_dir / "filter.py"
    
    # Check if it's a shim
    if is_shim(filter_path):
        return True  # Shim, nothing to update
    
    # Legacy copy — migrate to shim
    return migrate_to_shim(project_path, dry_run=dry_run)[0]
```

**CLI flag added:**
```python
parser.add_argument("--migrate-to-shim", action="store_true", 
                    help="Replace full engine copies with shims")
```

**Impact:** Existing projects can be migrated to shim with backup. Legacy `update.py` runs auto-migrate.

---

#### `scripts/deploy.ps1`

**Changes:**
1. Added `src\shim.py` to `$requiredFiles` array (line 13)
2. Added `@{ Src = "src\shim.py"; Dst = "shim.py" }` to `$filesToCopy` array (line 40-46)

**Before:**
```powershell
$requiredFiles = @("src\filter.py", "src\profile.py", "src\scaffold.py", "src\update.py", "src\engine_version.py", "VERSION")
```

**After:**
```powershell
$requiredFiles = @("src\filter.py", "src\profile.py", "src\scaffold.py", "src\update.py", "src\engine_version.py", "src\shim.py", "VERSION")
```

**Impact:** Deploy script validates and copies `shim.py` to live engine.

---

#### `scripts/deploy.sh`

**Changes:**
1. Added `src/shim.py` to validation loop (line 20)
2. Added `"src/shim.py:shim.py"` to copy loop (line 41)

**Before:**
```bash
for f in src/filter.py src/profile.py src/scaffold.py src/update.py src/engine_version.py VERSION; do
```

**After:**
```bash
for f in src/filter.py src/profile.py src/scaffold.py src/update.py src/engine_version.py src/shim.py VERSION; do
```

**Impact:** Deploy script validates and copies `shim.py` to live engine.

---

#### `tests/test_memory.py`

**Changes:**
1. Added `import shutil` to imports
2. Added `TestShim` class with 8 tests (217 lines)

**New tests:**
1. `test_shim_delegates_to_engine` — Verifies shim execs central engine
2. `test_shim_memory_dir_override` — Verifies `--memory-dir` CLI flag overrides shim's env var
3. `test_migrate_to_shim` — Verifies migration creates backup, removes profile.py
4. `test_migrate_to_shim_idempotent` — Verifies running migration twice is safe
5. `test_shim_missing_engine` — Verifies graceful error when engine not found
6. `test_profile_loads_post_migration` — Verifies profile.py loads from central location
7. `test_scaffold_force_overwrites_old_copy` — Verifies `--force` overwrites legacy copy
8. `test_scaffold_idempotent` — Verifies scaffold returns early if already shim

**Impact:** Comprehensive test coverage for shim functionality.

---

## Test Results

```
33 passed in 2.09s
Coverage: 14% (baseline: 11%, gain: +3 points)
```

All tests pass, including:
- 25 existing tests (no regressions)
- 8 new shim tests

---

## Verification Results

### Deploy Script (Dry-Run)
```
Validating dev project...
Running tests with coverage...
  Coverage: 14%
  Coverage: 14% (baseline: 11%, gain: +3 points)
Copying dev files to live engine...
  [DRY-RUN] Would copy: src\shim.py -> shim.py
  ...
Deploy complete.
```

### Shim Behavior
- Shim correctly delegates to central engine
- `--memory-dir` CLI flag overrides shim's `AGENT_MEMORY_DIR`
- Missing engine produces clear error message
- Migration creates backup and removes profile.py
- Idempotent migration (second run returns "Already a shim")

---

## Key Design Decisions

### 1. Sentinel-Based Detection
**Decision:** Use `# AGENT-MEMORY-SHIM v1` comment for exact detection.

**Rationale:** Heuristic string matching (`"AGENT_MEMORY_DIR" in content and "exec" in content`) is fragile. Sentinel is exact and versioned.

### 2. `os.execv` for Process Replacement
**Decision:** Use `os.execv` to replace shim process with engine.

**Rationale:** Preserves exit codes, stdin/stdout/stderr, and signal handling. Transparent to callers.

**Trade-off:** On Windows, PID changes (spawns new process). Documented in shim template.

### 3. Shared Shim Module
**Decision:** Create `src/shim.py` as single source of truth.

**Rationale:** Prevents divergence between `scaffold.py` and `update.py`. Both import from same module.

### 4. Backup Before Migration
**Decision:** Create timestamped backup in `memory/backups/TIMESTAMP/` before overwriting.

**Rationale:** Enables rollback if migration causes issues. Follows existing backup pattern.

### 5. Clean `__pycache__/` During Migration
**Decision:** Remove stale `__pycache__/` when migrating to shim.

**Rationale:** Shim projects don't compile `filter.py`, so old `.pyc` files are clutter. One-time cleanup during migration.

---

## Migration Path

### For New Projects
```powershell
python scaffold.py <project-path>
```
Creates shim automatically.

### For Existing Projects
```powershell
# Migrate all projects
python update.py --migrate-to-shim

# Migrate specific project
python update.py --project <project-path> --migrate-to-shim

# Preview migration
python update.py --migrate-to-shim --dry-run
```

### Auto-Migration
Running `update.py` on a legacy project auto-migrates to shim:
```powershell
python update.py  # Legacy projects auto-migrate
```

---

## Rollback Plan

If issues arise:

1. **Quick rollback** — Restore from backup:
   ```powershell
   cp memory/backups/*/filter.py memory/filter.py
   ```

2. **Full rollback** — Re-run scaffold with `--force`:
   ```powershell
   python scaffold.py <project-path> --force
   ```
   Note: This creates a new shim, not a full copy. To restore full copy, manually copy from `~/.agent-memory/engine/filter.py`.

---

## Files Changed Summary

| File | Lines Added | Lines Removed | Net Change |
|------|-------------|---------------|------------|
| `src/shim.py` | 57 | 0 | +57 |
| `src/scaffold.py` | 15 | 25 | -10 |
| `src/update.py` | 70 | 40 | +30 |
| `scripts/deploy.ps1` | 2 | 0 | +2 |
| `scripts/deploy.sh` | 2 | 0 | +2 |
| `tests/test_memory.py` | 217 | 0 | +217 |
| **Total** | **363** | **65** | **+298** |

---

## Code Review Checklist

- [ ] `src/shim.py` — Sentinel detection, `os.execv` usage, error handling
- [ ] `src/scaffold.py` — Shim writing, profile.py removal, idempotency
- [ ] `src/update.py` — Migration logic, backup creation, dry-run support, pycache cleanup
- [ ] `scripts/deploy.ps1` — Validation and copy lists
- [ ] `scripts/deploy.sh` — Validation and copy lists
- [ ] `tests/test_memory.py` — Test coverage for all shim scenarios

---

## Next Steps

1. **Deploy** — Run `scripts/deploy.ps1` to deploy updated engine
2. **Migrate** — Run `python update.py --migrate-to-shim` to migrate all projects
3. **Validate** — Test one project end-to-end before migrating all
4. **Monitor** — Watch for any issues with shim behavior

---

## Known Limitations

1. **Windows PID change** — `os.execv` on Windows spawns a new process. Any tooling that monitors PID may break. Validate during canary.

2. **`sys.argv[0]` transparency** — After `os.execv`, `sys.argv[0]` in the engine will be `~/.agent-memory/engine/filter.py`, not `memory/filter.py`. Currently no logic depends on this.

3. **Error messages reference "deploy script"** — Generic message works for both PowerShell and bash, but doesn't specify which script to run.

---

## Questions for Code Review

1. Is the sentinel approach better than heuristic detection?
2. Should we add a `--check-shim` flag to verify all projects are shimmable?
3. Is the backup naming convention (`memory/backups/TIMESTAMP/`) consistent with existing patterns?
4. Should `migrate_to_shim()` return more detailed status (e.g., backup path)?
5. Are there any edge cases in the migration logic we haven't covered?
