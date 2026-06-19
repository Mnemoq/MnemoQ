# Phase 1.2 — Resolver Refactor (v1.16.1)

> **Goal:** Prepare `filter.py` for the shim cutover by replacing module-level path globals with a `Paths` dataclass.
> **Prerequisite:** Phase 1.1 (Hygiene baseline) complete.
> **Done when:** All 4 real projects pass `python memory/filter.py --stats` and the test suite is green.
> **Constraint:** No external behavior change. This is a refactor-only phase.

---

## Current State

`filter.py` uses 8 module-level path globals initialized by `setup_paths()`:

```python
MEMORY_DIR = None
CONFIG_PATH = None
REPO_ROOT = None
LEARNINGS_PATH = None
QUARANTINE_PATH = None
ARCHIVE_DIR = None
SESSION_FILE = None
AGENTS_MD_PATH = None
```

These are used in ~30 places throughout the file. The `setup_paths()` function uses `global` keyword to mutate them.

### Problems

1. **Global state** — makes testing harder, prevents multiple instances
2. **Implicit initialization** — functions assume `setup_paths()` was called first
3. **Shim blocker** — the shim needs to set `AGENT_MEMORY_DIR` and exec `filter.py`, but the current design doesn't cleanly separate path resolution from path usage

---

## Target State

### 1. `Paths` Dataclass

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Paths:
    """Immutable container for all path-dependent state."""
    memory_dir: str
    repo_root: str
    config_path: str
    learnings_path: str
    quarantine_path: str
    archive_dir: str
    session_file: str
    agents_md_path: str
```

**Why frozen?** Paths are computed once at startup and never change. Immutable = no accidental mutation.

### 2. `resolve_memory_dir()` Helper

Extract the resolution logic from `setup_paths()` into a pure function:

```python
def resolve_memory_dir(memory_dir_arg: str | None) -> str:
    """Resolve memory directory path.
    
    Resolution priority:
      1. --memory-dir CLI flag
      2. AGENT_MEMORY_DIR environment variable
      3. <cwd>/memory/ (if it exists)
      4. Exit with error
    
    All paths are normalized to absolute paths.
    Explicit paths (--memory-dir and AGENT_MEMORY_DIR) are validated to exist.
    """
    if memory_dir_arg:
        raw = memory_dir_arg.strip()
        if not os.path.isdir(raw):
            sys.exit(f"ERROR: --memory-dir path does not exist or is not a directory: {raw}")
    elif os.environ.get("AGENT_MEMORY_DIR"):
        raw = os.environ["AGENT_MEMORY_DIR"].strip()
        if not os.path.isdir(raw):
            sys.exit(f"ERROR: AGENT_MEMORY_DIR path does not exist or is not a directory: {raw}")
    elif os.path.isdir(os.path.join(os.getcwd(), "memory")):
        raw = os.path.join(os.getcwd(), "memory")
    else:
        sys.exit("ERROR: No memory directory found. Use --memory-dir or run from a project root.")
    
    return os.path.abspath(raw)
```

### 3. `setup_paths()` Returns `Paths`

```python
def setup_paths(memory_dir_arg: str | None) -> Paths:
    """Resolve and return all path-dependent state.
    
    Returns a Paths dataclass. Does not mutate module state.
    
    Note: repo_root is derived as os.path.dirname(memory_dir). If memory_dir
    is the repo root itself (edge case: --memory-dir .), AGENTS_MD_PATH will
    resolve to <parent>/AGENTS.md. This is pre-existing behavior.
    """
    memory_dir = resolve_memory_dir(memory_dir_arg)
    repo_root = os.path.dirname(memory_dir)
    
    return Paths(
        memory_dir=memory_dir,
        repo_root=repo_root,
        config_path=os.path.join(memory_dir, "config.json"),
        learnings_path=os.path.join(memory_dir, "learnings.jsonl"),
        quarantine_path=os.path.join(memory_dir, "quarantine.jsonl"),
        archive_dir=os.path.join(memory_dir, "archive"),
        session_file=os.path.join(memory_dir, ".consolidate_session.json"),
        agents_md_path=os.path.join(repo_root, "AGENTS.md"),
    )
```

### 4. Module-Level `PATHS` Variable

To minimize diff, store the `Paths` instance in a module-level variable:

```python
# ponytail: module-level singleton, parameterize if multi-instance needed
PATHS: Paths | None = None
```

All functions access paths via `PATHS.memory_dir`, `PATHS.learnings_path`, etc.

**Why module-level?** Minimal diff. We could pass `Paths` as a parameter to every function, but that's a huge refactor with no behavioral benefit. The module-level approach is the lazy/correct choice for now.

### 5. Defensive Guard for `PATHS`

Add a helper to access `PATHS` safely:

```python
def _get_paths() -> Paths:
    """Get PATHS or raise if not initialized."""
    if PATHS is None:
        raise RuntimeError("PATHS not initialized. Call setup_paths() first.")
    return PATHS
```

This prevents `AttributeError` if a function is called before `main()` (e.g., during testing or if `filter.py` is imported as a module).

---

## Implementation Steps

### Step 1: Define `Paths` Dataclass

Add at the top of `filter.py` (after imports, before constants):

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Paths:
    """Immutable container for all path-dependent state."""
    memory_dir: str
    repo_root: str
    config_path: str
    learnings_path: str
    quarantine_path: str
    archive_dir: str
    session_file: str
    agents_md_path: str
```

### Step 2: Add `resolve_memory_dir()`

Extract the resolution logic from `setup_paths()` into a pure function (see above).

### Step 3: Refactor `setup_paths()`

Replace the current `setup_paths()` with the version that returns `Paths` (see above).

### Step 4: Replace Globals with `PATHS`

**Remove:**
```python
MEMORY_DIR = None
CONFIG_PATH = None
REPO_ROOT = None
LEARNINGS_PATH = None
QUARANTINE_PATH = None
ARCHIVE_DIR = None
SESSION_FILE = None
AGENTS_MD_PATH = None
```

**Add:**
```python
# ponytail: module-level singleton, parameterize if multi-instance needed
PATHS: Paths | None = None


def _get_paths() -> Paths:
    """Get PATHS or raise if not initialized."""
    if PATHS is None:
        raise RuntimeError("PATHS not initialized. Call setup_paths() first.")
    return PATHS
```

### Step 5: Update `main()` to Store `PATHS`

In `main()`, after parsing args:

```python
global PATHS
PATHS = setup_paths(args.memory_dir)
```

### Step 6: Update All Usages

Replace all ~30 usages of the old globals with `_get_paths().<field>`:

| Old | New |
|-----|-----|
| `MEMORY_DIR` | `_get_paths().memory_dir` |
| `CONFIG_PATH` | `_get_paths().config_path` |
| `REPO_ROOT` | `_get_paths().repo_root` |
| `LEARNINGS_PATH` | `_get_paths().learnings_path` |
| `QUARANTINE_PATH` | `_get_paths().quarantine_path` |
| `ARCHIVE_DIR` | `_get_paths().archive_dir` |
| `SESSION_FILE` | `_get_paths().session_file` |
| `AGENTS_MD_PATH` | `_get_paths().agents_md_path` |

**Example changes:**

```python
# Before
if not os.path.exists(LEARNINGS_PATH):
    return entries
with open(LEARNINGS_PATH, "r", encoding="utf-8") as f:

# After
if not os.path.exists(_get_paths().learnings_path):
    return entries
with open(_get_paths().learnings_path, "r", encoding="utf-8") as f:
```

```python
# Before
config_path = Path(CONFIG_PATH)

# After
config_path = Path(_get_paths().config_path)
```

### Step 7: Update `load_config()` to Use `_get_paths()`

```python
def load_config():
    """Load project-specific configuration from config.json."""
    config_path = Path(_get_paths().config_path)
    # ... rest unchanged
```

### Step 8: Add Unit Tests for Resolver

Add tests to verify the priority chain and the `_get_paths()` guard:

```python
def test_resolve_memory_dir_priority(monkeypatch, tmp_path):
    """Test resolve_memory_dir() honors priority: --memory-dir > env > cwd/memory.
    
    Note: Error exit paths (sys.exit()) are tested by integration tests, not unit tests.
    """
    # Test 1: --memory-dir takes priority
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    result = resolve_memory_dir(str(memory_dir))
    assert result == str(memory_dir.resolve())
    
    # Test 2: AGENT_MEMORY_DIR env var (when --memory-dir is None)
    env_dir = tmp_path / "env_memory"
    env_dir.mkdir()
    monkeypatch.setenv("AGENT_MEMORY_DIR", str(env_dir))
    result = resolve_memory_dir(None)
    assert result == str(env_dir.resolve())
    
    # Test 3: cwd/memory fallback (when both are None)
    monkeypatch.delenv("AGENT_MEMORY_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    result = resolve_memory_dir(None)
    assert result == str(memory_dir.resolve())


def test_get_paths_raises_if_uninitialized():
    """Test _get_paths() raises RuntimeError if PATHS is None."""
    import filter
    old_paths = filter.PATHS
    try:
        filter.PATHS = None
        with pytest.raises(RuntimeError, match="PATHS not initialized"):
            filter._get_paths()
    finally:
        filter.PATHS = old_paths
```

### Step 9: Run Tests

```powershell
python -m pytest tests/ -v
```

All tests should pass. No behavioral changes expected.

### Step 10: Verify with Real Projects

For each of the 4 real projects:

```powershell
cd <project-dir>
python memory/filter.py --stats > before.txt
# Apply refactor
python memory/filter.py --stats > after.txt
# Compare
fc before.txt after.txt
```

Output must be **byte-identical** to pre-refactor.

---

## Files to Modify

| File | Changes |
|------|---------|
| `src/filter.py` | Add `Paths` dataclass, `resolve_memory_dir()`, `_get_paths()`, refactor `setup_paths()`, replace globals with `_get_paths().<field>`, update ~30 usages |
| `tests/test_memory.py` | Add `test_resolve_memory_dir_priority()` |

**No changes to:**
- `src/scaffold.py`
- `src/update.py`
- `src/profile.py`
- `scripts/deploy.ps1`
- `scripts/deploy.sh`

---

## Risk Assessment

### Low Risk

- **Refactor-only** — no new features, no behavioral changes
- **Type hints** — `Paths` dataclass is typed, catches errors at development time
- **Immutable** — `frozen=True` prevents accidental mutation
- **Tests** — 22 existing tests will catch any regressions

### Mitigations

- **Backup** — deploy script creates backup before copying
- **Rollback** — revert `src/filter.py` to pre-refactor version if issues arise
- **Dry-run** — test with `deploy.ps1 -DryRun` first

---

## Success Criteria

- [ ] `Paths` dataclass defined with all 8 path fields
- [ ] `resolve_memory_dir()` extracts resolution logic
- [ ] `setup_paths()` returns `Paths` instance
- [ ] All 8 globals replaced with `_get_paths().<field>`
- [ ] `_get_paths()` guard prevents `AttributeError` if called before `main()`
- [ ] All existing tests pass
- [ ] New test for `resolve_memory_dir()` priority chain passes
- [ ] All 4 real projects pass `python memory/filter.py --stats`
- [ ] `--stats` output is **byte-identical** to pre-refactor (verified with `fc` or `diff`)

---

## Rollback Plan

If issues arise:

1. Revert `src/filter.py` to pre-refactor version
2. Run `deploy.ps1` to restore live engine
3. Verify tests pass

---

## Future Work (Post-1.2)

After 1.2 is stable, consider:

- **Pass `Paths` as parameter** — more explicit, enables testing with multiple paths
- **Remove module-level `PATHS`** — requires updating all function signatures
- **Add type hints** — annotate all functions with `Paths` parameter

These are optional refactorings. The current design (module-level `PATHS`) is sufficient for the shim cutover in 1.3.
