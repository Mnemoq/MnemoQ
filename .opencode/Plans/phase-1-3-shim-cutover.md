# Phase 1.3 — Shim Cutover (v1.17.0)

> **Goal:** Replace per-project engine copies with a thin shim that execs the central engine.
> **Prerequisite:** Phase 1.2 (Resolver refactor) complete.
> **Done when:** Editing `src/filter.py` is reflected in all 4 projects on next invocation; no project carries a copy of `filter.py`.

---

## Current State

- `scaffold.py` copies `filter.py` and `profile.py` to each project's `memory/` directory
- `update.py` uses regex to replace version constants in copied `filter.py`
- Projects have full ~1800-line copies of engine code
- Changes to engine require running `update.py` to propagate to all projects

## Target State

- Projects have a ~15-line shim that sets `AGENT_MEMORY_DIR` and execs `~/.agent-memory/engine/filter.py`
- Engine code lives only in `~/.agent-memory/engine/`
- Changes to engine are immediately visible to all projects (no update needed)
- `profile.py` is no longer copied (loaded from central location)

---

## Implementation Steps

### Step 1: Create Shared Shim Module

**Create `src/shim.py`** with the shim template and detection logic:

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

**Why shared module?** Single source of truth prevents divergence. Both `scaffold.py` and `update.py` import from here.

**Why sentinel?** Exact detection via sentinel comment is more reliable than heuristic string matching.

### Step 2: Update `scaffold.py`

**Import from shared shim module:**

```python
from shim import SHIM_TEMPLATE, is_shim
```

**Change `copy_engine_files()` to write shim instead of copying files:**

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

**Update `check_prerequisites()` to not require `profile.py`:**

```python
def check_prerequisites():
    """Verify engine files exist before scaffolding."""
    required_files = ["filter.py", "templates/config.json"]
    for f in required_files:
        path = ENGINE_DIR / f
        if not path.exists():
            sys.exit(f"ERROR: Engine file missing: {path}\nRun the deploy script first.")
```

### Step 3: Add `--migrate-to-shim` to `update.py`

**Import from shared shim module:**

```python
from shim import SHIM_TEMPLATE, is_shim
```

**New function to convert existing projects:**

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

**Add CLI flag:**

```python
parser.add_argument("--migrate-to-shim", action="store_true", 
                    help="Replace full engine copies with shims")
```

**Update `main()` to handle the flag:**

```python
if args.migrate_to_shim:
    for project in projects:
        success, msg = migrate_to_shim(project, args.dry_run)
        print(f"{project}: {msg}")
    return 0
```

### Step 4: Simplify `update_engine_files()`

**In `update.py`, remove regex version embedding:**

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

**Remove the regex replacement logic entirely.**

### Step 5: Update `verify_update()`

**No changes needed** — the shim is transparent to `verify_update()`. It runs `python memory/filter.py --stats` which works identically whether it's a shim or full copy.

### Step 6: Update `update_project()`

**In `update.py`, skip pycache clearing for shim projects:**

```python
def update_project(project_path, engine_version, engine_path, dry_run=False, force=False):
    # ... existing code ...
    
    # Update engine files (or migrate to shim)
    success = update_engine_files(project_path, engine_path, dry_run=dry_run)
    if not success:
        return False, "Failed to update engine files", None
    
    # Skip pycache clearing for shim projects (no compiled artifacts)
    if not dry_run:
        filter_path = project_path / "memory" / "filter.py"
        if not is_shim(filter_path):
            # Legacy copy, clear pycache
            clear_pycache(project_path)
    
    # ... rest of function ...
```

### Step 7: Add Tests

**Test shim delegation:**

```python
def test_shim_delegates_to_engine(temp_project, engine_dir):
    """Test that shim correctly delegates to central engine."""
    from shim import SHIM_TEMPLATE
    
    # Write shim to project
    memory_dir = temp_project / "memory"
    memory_dir.mkdir()
    shim_path = memory_dir / "filter.py"
    shim_path.write_text(SHIM_TEMPLATE)
    
    # Run shim with --version
    result = subprocess.run(
        [sys.executable, str(shim_path), "--version"],
        capture_output=True,
        text=True,
        cwd=temp_project
    )
    
    # Should match engine version
    assert result.returncode == 0
    assert "agent-memory-engine" in result.stderr
```

**Test --memory-dir override:**

```python
def test_shim_memory_dir_override(temp_project, engine_dir, tmp_path):
    """Test that --memory-dir CLI flag overrides AGENT_MEMORY_DIR set by shim."""
    from shim import SHIM_TEMPLATE
    
    # Write shim to project
    memory_dir = temp_project / "memory"
    memory_dir.mkdir()
    shim_path = memory_dir / "filter.py"
    shim_path.write_text(SHIM_TEMPLATE)
    
    # Create alternate memory directory
    alt_memory = tmp_path / "alt_memory"
    alt_memory.mkdir()
    (alt_memory / "learnings.jsonl").touch()
    (alt_memory / "quarantine.jsonl").touch()
    (alt_memory / "archive").mkdir()
    
    # Run shim with --memory-dir override
    result = subprocess.run(
        [sys.executable, str(shim_path), "--memory-dir", str(alt_memory), "--stats"],
        capture_output=True,
        text=True,
        cwd=temp_project
    )
    
    # Should use alt_memory, not the shim's directory
    assert result.returncode == 0
    assert "MEMORY STATS" in result.stdout
```

**Test migration:**

```python
def test_migrate_to_shim(temp_project, engine_dir):
    """Test --migrate-to-shim converts full copy to shim."""
    # Copy full engine to project
    memory_dir = temp_project / "memory"
    memory_dir.mkdir()
    shutil.copy2(engine_dir / "filter.py", memory_dir / "filter.py")
    shutil.copy2(engine_dir / "profile.py", memory_dir / "profile.py")
    
    # Migrate
    from update import migrate_to_shim
    success, msg = migrate_to_shim(temp_project)
    
    assert success
    assert "Migrated" in msg
    
    # Verify shim
    from shim import is_shim
    assert is_shim(memory_dir / "filter.py")
    
    # Verify profile.py removed
    assert not (memory_dir / "profile.py").exists()
    
    # Verify backup created
    backups = list((memory_dir / "backups").glob("*"))
    assert len(backups) == 1
```

**Test idempotent migration:**

```python
def test_migrate_to_shim_idempotent(temp_project, engine_dir):
    """Test running --migrate-to-shim twice is safe."""
    # Copy full engine to project
    memory_dir = temp_project / "memory"
    memory_dir.mkdir()
    shutil.copy2(engine_dir / "filter.py", memory_dir / "filter.py")
    
    # Migrate once
    from update import migrate_to_shim
    success1, msg1 = migrate_to_shim(temp_project)
    assert success1
    assert "Migrated" in msg1
    
    # Count backups after first migration
    backups_after_first = list((memory_dir / "backups").glob("*"))
    
    # Migrate again
    success2, msg2 = migrate_to_shim(temp_project)
    assert success2
    assert "Already a shim" in msg2
    
    # Verify no additional backup created
    backups_after_second = list((memory_dir / "backups").glob("*"))
    assert len(backups_after_first) == len(backups_after_second)
```

**Test missing engine error:**

```python
def test_shim_missing_engine(temp_project, tmp_path):
    """Test shim handles missing engine gracefully."""
    from shim import SHIM_TEMPLATE
    
    # Write shim to project
    memory_dir = temp_project / "memory"
    memory_dir.mkdir()
    shim_path = memory_dir / "filter.py"
    shim_path.write_text(SHIM_TEMPLATE)
    
    # Temporarily rename engine directory
    engine_dir = Path.home() / ".agent-memory" / "engine"
    backup_dir = tmp_path / "engine_backup"
    if engine_dir.exists():
        shutil.move(str(engine_dir), str(backup_dir))
    
    try:
        # Run shim
        result = subprocess.run(
            [sys.executable, str(shim_path), "--stats"],
            capture_output=True,
            text=True,
            cwd=temp_project
        )
        
        # Should fail with error message
        assert result.returncode == 1
        assert "Engine not found" in result.stderr
        assert "deploy script" in result.stderr
    finally:
        # Restore engine directory
        if backup_dir.exists():
            shutil.move(str(backup_dir), str(engine_dir))
```

**Test profile.py loads post-migration:**

```python
def test_profile_loads_post_migration(temp_project, engine_dir):
    """Test that profile.py loads from central location after migration."""
    # Copy full engine to project
    memory_dir = temp_project / "memory"
    memory_dir.mkdir()
    shutil.copy2(engine_dir / "filter.py", memory_dir / "filter.py")
    shutil.copy2(engine_dir / "profile.py", memory_dir / "profile.py")
    
    # Migrate
    from update import migrate_to_shim
    migrate_to_shim(temp_project)
    
    # Run shim with retrieval
    result = subprocess.run(
        [sys.executable, str(memory_dir / "filter.py"), "--step", "1", "--components", "Tooling", "--domain", "tooling"],
        capture_output=True,
        text=True,
        cwd=temp_project
    )
    
    # Should succeed (profile.py loaded from central location)
    assert result.returncode == 0
    # Profile context appears in output if profile exists
    # (may be "(none)" if no profile, but should not error)
```

**Test scaffold --force overwrites old copy:**

```python
def test_scaffold_force_overwrites_old_copy(temp_project, engine_dir):
    """Test that scaffold.py --force overwrites old full copy with shim."""
    # Copy full engine to project (simulating legacy state)
    memory_dir = temp_project / "memory"
    memory_dir.mkdir()
    shutil.copy2(engine_dir / "filter.py", memory_dir / "filter.py")
    
    # Verify it's not a shim yet
    from shim import is_shim
    assert not is_shim(memory_dir / "filter.py")
    
    # Run scaffold with --force
    result = subprocess.run(
        [sys.executable, str(engine_dir / "scaffold.py"), str(temp_project), "--defaults", "--force"],
        capture_output=True,
        text=True
    )
    
    assert result.returncode == 0
    
    # Verify it's now a shim
    assert is_shim(memory_dir / "filter.py")
```

**Test scaffold idempotency:**

```python
def test_scaffold_idempotent(temp_project, engine_dir):
    """Test that scaffold.py returns early if already a shim."""
    from shim import SHIM_TEMPLATE, is_shim
    
    # Write shim to project
    memory_dir = temp_project / "memory"
    memory_dir.mkdir()
    shim_path = memory_dir / "filter.py"
    shim_path.write_text(SHIM_TEMPLATE)
    
    # Get modification time before scaffold
    mtime_before = shim_path.stat().st_mtime_ns
    
    # Run scaffold (without --force)
    result = subprocess.run(
        [sys.executable, str(engine_dir / "scaffold.py"), str(temp_project), "--defaults"],
        capture_output=True,
        text=True
    )
    
    assert result.returncode == 0
    
    # Verify file was not modified (idempotent)
    mtime_after = shim_path.stat().st_mtime_ns
    assert mtime_before == mtime_after
    
    # Verify still a shim
    assert is_shim(shim_path)
```

### Step 8: Update Deploy Scripts

**Add `src/shim.py` to deploy scripts** — it's a new module that must be deployed alongside `filter.py`, `profile.py`, etc.

**In `scripts/deploy.ps1`:**
- Add `"src\shim.py"` to `$requiredFiles` array (line 13)
- Add `@{ Src = "src\shim.py"; Dst = "shim.py" }` to `$filesToCopy` array (line 40-46)

**In `scripts/deploy.sh`:**
- Add `src/shim.py` to validation loop (line 20)
- Add `"src/shim.py:shim.py"` to copy loop (line 41)

### Step 9: Rollout Plan

**Canary on one project:**

1. Pick one project (e.g., `PixelPurge`)
2. Run `python update.py --project PixelPurge --migrate-to-shim`
3. Verify `python memory/filter.py --stats` works
4. Verify `python memory/filter.py --memory-dir /tmp/alt --stats` honors CLI override
5. Make a change to `src/filter.py` in the engine
6. Run `deploy.ps1`
7. Verify the change is visible in `PixelPurge` without running `update.py`
8. Use the project for a work session, verify no issues

**Migrate remaining projects:**

```powershell
python update.py --migrate-to-shim
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `src/shim.py` | **New file** — `SHIM_TEMPLATE`, `SHIM_SENTINEL`, `is_shim()` |
| `src/scaffold.py` | Import from `shim.py`, update `copy_engine_files()`, update `check_prerequisites()` |
| `src/update.py` | Import from `shim.py`, add `migrate_to_shim()`, simplify `update_engine_files()`, add `--migrate-to-shim` flag, skip pycache for shims |
| `scripts/deploy.ps1` | Add `src/shim.py` to validation and copy lists |
| `scripts/deploy.sh` | Add `src/shim.py` to validation and copy lists |
| `tests/test_memory.py` | Add tests for shim delegation, migration, idempotency, missing engine, --memory-dir override, profile loading, scaffold --force, scaffold idempotency |

---

## Risk Assessment

### Medium Risk

- **Breaking change** — existing projects with full copies need migration
- **Rollback complexity** — can't easily revert a shimmed project to full copy
- **Debugging** — shim adds indirection, stack traces show engine path not project path
- **Windows PID change** — `os.execv` on Windows spawns a new process (PID changes), unlike Unix which replaces in-place. Any tooling that monitors PID may break.

### Mitigations

- **Backup** — `migrate_to_shim()` creates timestamped backup in `memory/backups/TIMESTAMP/` before overwriting
- **Dry-run** — `--dry-run` flag shows what would be migrated without changes
- **Canary** — migrate one project first, validate for a work session
- **Rollback** — restore from backup or re-run `scaffold.py --force` to restore full copies
- **PID monitoring** — document Windows behavior; validate during canary that no tooling depends on PID

---

## Success Criteria

- [ ] `src/shim.py` created with `SHIM_TEMPLATE`, `SHIM_SENTINEL`, `is_shim()`
- [ ] `scaffold.py` imports from `shim.py`, writes shim instead of copying files
- [ ] `update.py` imports from `shim.py`, adds `migrate_to_shim()`, simplifies `update_engine_files()`
- [ ] Regex version embedding removed from `update.py`
- [ ] `clear_pycache()` skipped for shim projects
- [ ] `verify_update()` works with shim
- [ ] Tests for shim delegation, migration, idempotency, missing engine, --memory-dir override, profile loading, scaffold --force, scaffold idempotency pass
- [ ] Deploy scripts copy `src/shim.py` to live engine
- [ ] Canary project migrated and validated (including --memory-dir override test)
- [ ] All 4 projects migrated
- [ ] Editing `src/filter.py` is reflected in all projects on next invocation

---

## Rollback Plan

If issues arise:

1. **Quick rollback** — restore from backup: `cp memory/backups/*/filter.py memory/filter.py`
2. **Full rollback** — re-run `python scaffold.py <project> --force` to restore full engine copies
3. Verify tests pass

---

## Future Work (Post-1.3)

After 1.3 is stable:

- **Simplify `update.py`** — no need to copy engine files, just verify shim exists
- **Add `--check-shim` flag** — verify all projects are shimmable
- **Document `sys.argv[0]` behavior** — after `os.execv`, `sys.argv[0]` in the engine will be `~/.agent-memory/engine/filter.py`, not `memory/filter.py`. Currently no logic depends on this, but worth documenting.
