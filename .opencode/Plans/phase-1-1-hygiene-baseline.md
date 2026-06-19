# Phase 1.1 — Hygiene Baseline (v1.16.0)

> **Goal:** Cheap, must-do-first cleanup. Remove stale project entries, centralize version constant, add coverage baseline.
> **Prerequisite:** None. This is the foundation.
> **Done when:** `projects.txt` pruned and guarded against temp dirs, version constant is in one place, `pytest --cov` produces baseline, deploy fails on coverage regression > 5 points, version bumped to 1.16.0.

---

## Task 1: Centralize Version Constant

**Problem:** `"1.15.0"` is hardcoded as fallback in 3 files:
- `src/filter.py:49`
- `src/scaffold.py:35`
- `src/update.py:38`

**Solution:** Create `src/engine_version.py` with single source of truth.

### Files to create/modify:

| File | Action |
|------|--------|
| `src/engine_version.py` | **Create** — single helper with `get_engine_version()` |
| `src/filter.py` | Replace `_read_engine_version()` with import from `engine_version` (add import at top with other imports, remove function definition) |
| `src/scaffold.py` | Replace `_read_engine_version()` with import; **update `copy_engine_files()` regex** |
| `src/update.py` | Replace `_read_engine_version()` with import; **update `update_engine_files()` regex** |
| `scripts/deploy.ps1` | Add `engine_version.py` to validation and copy lists |
| `scripts/deploy.sh` | Add `engine_version.py` to validation and copy lists |

**Note:** In `filter.py`, add `from engine_version import get_engine_version` at the top with other imports (after line 37). Remove the `_read_engine_version()` function (lines 41-49) and change `ENGINE_VERSION = _read_engine_version()` to `ENGINE_VERSION = get_engine_version()`. The `# --- Constants ---` comment remains in place.

### `src/engine_version.py` design:

```python
"""
Engine Version — Single source of truth for version constant.

Reads from VERSION file, falls back to hardcoded value if file missing.

Note: During development, the repo VERSION file is the source of truth.
get_engine_version() reads from the deployed engine location, which only
exists after deploy.ps1/deploy.sh has run. Before deployment, all tools
fall back to FALLBACK_VERSION.
"""
from pathlib import Path

# Hardcoded fallback — update this when bumping version
FALLBACK_VERSION = "1.16.0"

def get_engine_version() -> str:
    """Read engine version from VERSION file, with fallback."""
    version_file = Path.home() / ".agent-memory" / "engine" / "VERSION"
    if version_file.exists():
        try:
            version = version_file.read_text().strip()
            if version:
                return version
        except Exception:
            pass
    return FALLBACK_VERSION
```

### Update version embedding regex in `scaffold.py` and `update.py`:

**Problem:** After removing `_read_engine_version()` from `filter.py`, the old regex won't match. The new pattern must find the import and replace the version.

**Old pattern (no longer matches):**
```python
pattern = r'def _read_engine_version\(\):.*?ENGINE_VERSION = _read_engine_version\(\)'
```

**New pattern:**
```python
# Match the import line and the ENGINE_VERSION assignment
# Use .*? with re.DOTALL to handle intervening comments (e.g., # --- Constants ---)
pattern = r'from engine_version import get_engine_version.*?ENGINE_VERSION = get_engine_version\(\)'
replacement = f'ENGINE_VERSION = "{ENGINE_VERSION}"'
```

**Verification in scaffold.py:**
```python
# After substitution, verify:
if 'from engine_version import get_engine_version' in new_content:
    raise RuntimeError("Version embedding failed: import still present")
if f'ENGINE_VERSION = "{ENGINE_VERSION}"' not in new_content:
    raise RuntimeError("Version embedding failed: static version not embedded")
```

**Verification in update.py (`update_engine_files()`):**
```python
# Same checks as scaffold.py, but return False instead of raising:
if 'from engine_version import get_engine_version' in new_content:
    return False
if f'ENGINE_VERSION = "{ENGINE_VERSION}"' not in new_content:
    return False
```

### Deploy script copy lists:

**`deploy.ps1`** — add to `$requiredFiles` array (line 13) and `$filesToCopy` array (line 40-46):
```powershell
$requiredFiles = @("src\filter.py", "src\profile.py", "src\scaffold.py", "src\update.py", "src\engine_version.py", "VERSION")

$filesToCopy = @(
    @{ Src = "src\filter.py"; Dst = "filter.py" },
    @{ Src = "src\profile.py"; Dst = "profile.py" },
    @{ Src = "src\scaffold.py"; Dst = "scaffold.py" },
    @{ Src = "src\update.py"; Dst = "update.py" },
    @{ Src = "src\engine_version.py"; Dst = "engine_version.py" },  # NEW
    @{ Src = "VERSION"; Dst = "VERSION" }
)
```

**`deploy.sh`** — add to validation loop (line 20) and copy loop (line 41):
```bash
for f in src/filter.py src/profile.py src/scaffold.py src/update.py src/engine_version.py VERSION; do

for pair in "src/filter.py:filter.py" "src/profile.py:profile.py" "src/scaffold.py:scaffold.py" "src/update.py:update.py" "src/engine_version.py:engine_version.py" "VERSION:VERSION"; do
```

### Verification:
- [ ] `python -c "from src.engine_version import get_engine_version; print(get_engine_version())"` outputs version
- [ ] `python src/filter.py --version` works
- [ ] `python src/scaffold.py --version` works
- [ ] `python src/update.py --version` works
- [ ] `pytest tests/` passes
- [ ] `deploy.ps1 -DryRun` copies `engine_version.py`
- [ ] `deploy.sh --dry-run` copies `engine_version.py`

---

## Task 2: Prune projects.txt + Add Temp Dir Guard

**Problem:** `projects.txt` may contain stale entries like `Temp\tmp*` from Windows temp directories.

**Solution:** 
1. Add `is_temp_path()` guard in `update.py`
2. Auto-prune stale entries on each run (with backup) — **skip during `--dry-run`**
3. Add unit tests for `is_temp_path()` and `load_projects()` auto-prune

### Files to modify:

| File | Action |
|------|--------|
| `src/update.py` | Add `is_temp_path()`, modify `load_projects()` to auto-prune (with dry-run guard) |
| `tests/test_memory.py` | Add tests for `is_temp_path()` and `load_projects()` |

### Guard design:

```python
# Add to imports at top of update.py
import tempfile

def is_temp_path(path: Path) -> bool:
    """Check if path is under system temp directory.
    
    Returns True if the resolved path is relative to the system temp dir.
    Returns False on any resolution error (treat as non-temp for safety).
    """
    try:
        temp_dir = Path(tempfile.gettempdir()).resolve()
        return path.resolve().is_relative_to(temp_dir)
    except (ValueError, OSError):
        return False

def load_projects(dry_run=False):
    """Load list of project paths from projects.txt, auto-pruning temp dirs.
    
    Stale temp entries are removed from projects.txt with a backup created.
    If dry_run=True, skip the prune step (read-only mode).
    """
    projects_file = ENGINE_DIR / "projects.txt"
    if not projects_file.exists():
        return []
    
    projects = []
    stale_entries = []
    all_lines = []
    
    with open(projects_file, 'r', encoding='utf-8') as f:
        all_lines = f.readlines()
    
    for line in all_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            projects.append(stripped)  # Keep comments/empty as-is (will be filtered later)
            continue
        p = Path(stripped)
        if is_temp_path(p):
            stale_entries.append(stripped)
        else:
            projects.append(p)
    
    # Auto-prune stale entries (skip during dry-run)
    if stale_entries and not dry_run:
        # Create backup
        backup_path = ENGINE_DIR / f"projects.txt.backup-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(projects_file, backup_path)
        print(f"WARNING: Pruned {len(stale_entries)} temp directory entries:", file=sys.stderr)
        for s in stale_entries:
            print(f"  - {s}", file=sys.stderr)
        print(f"Backup saved to: {backup_path}", file=sys.stderr)
        
        # Rewrite projects.txt without stale entries
        with open(projects_file, 'w', encoding='utf-8') as f:
            for item in projects:
                if isinstance(item, str):
                    f.write(item + '\n')
                else:
                    f.write(str(item) + '\n')
    elif stale_entries and dry_run:
        # Just warn during dry-run, don't prune
        print(f"WARNING: Found {len(stale_entries)} temp directory entries (would prune on real run):", file=sys.stderr)
        for s in stale_entries:
            print(f"  - {s}", file=sys.stderr)
    
    # Return only Path objects
    return [p for p in projects if isinstance(p, Path)]
```

### Update `main()` in `update.py` to pass dry_run:

```python
# In main(), around line 438:
dry_run = args.dry_run
projects = load_projects(dry_run=dry_run)
```

### Unit tests:

```python
# In tests/test_memory.py
import tempfile
from pathlib import Path
import sys
import shutil
import os
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from update import is_temp_path, load_projects

def test_is_temp_path_detects_temp():
    """is_temp_path returns True for paths under system temp dir."""
    temp_dir = Path(tempfile.gettempdir())
    assert is_temp_path(temp_dir) is True
    assert is_temp_path(temp_dir / "some" / "subdir") is True
    # Windows-specific temp paths
    if sys.platform == "win32":
        assert is_temp_path(Path("C:/Users/Admin/AppData/Local/Temp") / "tmp1234") is True

def test_is_temp_path_rejects_non_temp():
    """is_temp_path returns False for paths not under system temp dir."""
    assert is_temp_path(Path("C:/Projects/magpie-swoop")) is False
    assert is_temp_path(Path("/home/user/projects/foo")) is False
    assert is_temp_path(Path(tempfile.gettempdir()).parent / "other_dir") is False

def test_is_temp_path_handles_nonexistent():
    """is_temp_path returns False for non-existent paths (doesn't crash)."""
    assert is_temp_path(Path("Z:/nonexistent/path")) is False

def test_load_projects_prunes_temp_entries(tmp_path, monkeypatch):
    """load_projects auto-prunes temp entries with backup."""
    # Setup: create a fake ENGINE_DIR with projects.txt
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    projects_file = engine_dir / "projects.txt"
    
    # Write projects.txt with one valid and one temp entry
    temp_entry = str(Path(tempfile.gettempdir()) / "tmp1234")
    projects_file.write_text(f"# Comment\nC:/Projects/valid\n{temp_entry}\n")
    
    # Monkeypatch ENGINE_DIR in update module
    import update
    monkeypatch.setattr(update, "ENGINE_DIR", engine_dir)
    
    # Run load_projects
    result = load_projects(dry_run=False)
    
    # Verify: only valid project returned
    assert len(result) == 1
    assert str(result[0]) == "C:/Projects/valid"
    
    # Verify: backup created
    backups = list(engine_dir.glob("projects.txt.backup-*"))
    assert len(backups) == 1
    
    # Verify: projects.txt rewritten without temp entry
    content = projects_file.read_text()
    assert temp_entry not in content
    assert "C:/Projects/valid" in content

def test_load_projects_dry_run_no_prune(tmp_path, monkeypatch):
    """load_projects with dry_run=True does not modify projects.txt."""
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    projects_file = engine_dir / "projects.txt"
    
    temp_entry = str(Path(tempfile.gettempdir()) / "tmp1234")
    original_content = f"# Comment\nC:/Projects/valid\n{temp_entry}\n"
    projects_file.write_text(original_content)
    
    import update
    monkeypatch.setattr(update, "ENGINE_DIR", engine_dir)
    
    # Run load_projects with dry_run=True
    result = load_projects(dry_run=True)
    
    # Verify: only valid project returned
    assert len(result) == 1
    
    # Verify: projects.txt NOT modified
    assert projects_file.read_text() == original_content
    
    # Verify: no backup created
    backups = list(engine_dir.glob("projects.txt.backup-*"))
    assert len(backups) == 0
```

### Verification:
- [ ] `update.py` auto-prunes temp paths with backup
- [ ] `update.py --dry-run` warns but does NOT prune
- [ ] `tests/test_memory.py::test_is_temp_path_*` all pass
- [ ] `tests/test_memory.py::test_load_projects_*` all pass
- [ ] Existing tests still pass

---

## Task 3: Add pytest-cov + Coverage Baseline

**Problem:** No coverage measurement. Future targets need real numbers.

**Solution:** Add pytest-cov, run once, commit baseline.

### Files to modify:

| File | Action |
|------|--------|
| `pyproject.toml` | Add pytest-cov to dev dependencies |

### `pyproject.toml` changes:

```toml
[project.optional-dependencies]
dev = ["pytest-cov>=4.0"]

[tool.coverage.run]
source = ["src"]
omit = ["tests/*"]

[tool.coverage.report]
show_missing = true
fail_under = 0  # Will be raised after baseline
```

### Commands to run:

```powershell
# Install pytest-cov
pip install pytest-cov

# Run coverage
python -m pytest tests/ --cov=src --cov-report=term-missing

# Save baseline
python -m pytest tests/ --cov=src --cov-report=term-missing > coverage-baseline.txt
```

### Verification:
- [ ] `pytest --cov=src` runs without error
- [ ] `coverage-baseline.txt` committed to repo
- [ ] Baseline shows current coverage percentage

---

## Task 4: Bump Version to 1.16.0

**Problem:** Phase 1.1 delivers v1.16.0 but version is still 1.15.0.

**Solution:** Update VERSION file and pyproject.toml.

### Files to modify:

| File | Action |
|------|--------|
| `VERSION` | Change from `1.15.0` to `1.16.0` |
| `pyproject.toml` | Change `version = "1.15.0"` to `version = "1.16.0"` |
| `src/engine_version.py` | `FALLBACK_VERSION` already set to `"1.16.0"` in Task 1 |

### Verification:
- [ ] `cat VERSION` shows `1.16.0`
- [ ] `grep version pyproject.toml` shows `1.16.0`
- [ ] `python src/filter.py --version` shows `v1.16.0` (after deploy)

---

## Task 5: Deploy Script Coverage Gate

**Problem:** Deploy should fail if coverage regresses > 5 points.

**Solution:** Extend `deploy.ps1` and `deploy.sh` to check coverage.

**Key fixes from review:**
- Regex captures coverage **percentage** (not statement count)
- Combined test + coverage run (saves ~50% CI time)
- Exit code check after pytest
- PowerShell 5.1 compatibility (`*>&1` instead of `2>&1`)
- Handle malformed baseline file
- **Note:** `grep -P` (Perl regex) is GNU-specific. For macOS compatibility, use `sed` or `awk` fallback.

### Files to modify:

| File | Action |
|------|--------|
| `scripts/deploy.ps1` | Replace test run with combined test+coverage, add gate |
| `scripts/deploy.sh` | Replace test run with combined test+coverage, add gate |

### `deploy.ps1` changes:

```powershell
# Replace existing test run (lines 21-28) with combined test+coverage:
Write-Host "Running tests with coverage..."
Push-Location $DevRoot
$testOutput = python -m pytest tests/ --tb=short --cov=src --cov-report=term-missing *>&1
$testExitCode = $LASTEXITCODE
Pop-Location

# Check test exit code
if ($testExitCode -ne 0) {
    Write-Error "Tests failed (exit code $testExitCode). Aborting deploy."
    Write-Host $testOutput
    exit 1
}

# Extract coverage percentage from TOTAL line
# Format: "TOTAL                      499   1197    29%"
$coverageMatch = ($testOutput -join "`n") | Select-String "TOTAL.*?(\d+)\s*%"
if ($coverageMatch) {
    $currentCoverage = [int]$coverageMatch.Matches[0].Groups[1].Value
    Write-Host "  Coverage: $currentCoverage%"
    
    # Read baseline
    $baselinePath = "$DevRoot\coverage-baseline.txt"
    if (Test-Path $baselinePath) {
        try {
            $baselineContent = Get-Content $baselinePath -Raw
            $baselineMatch = $baselineContent | Select-String "TOTAL.*?(\d+)\s*%"
            if ($baselineMatch) {
                $baselineCoverage = [int]$baselineMatch.Matches[0].Groups[1].Value
                $regression = $baselineCoverage - $currentCoverage
                
                if ($regression -gt 5) {
                    Write-Error "Coverage regression: $baselineCoverage% -> $currentCoverage% ($regression point drop)"
                    Write-Error "Deploy aborted. Improve tests before deploying."
                    exit 1
                } elseif ($regression -gt 0) {
                    Write-Host "  Coverage: $currentCoverage% (baseline: $baselineCoverage%, regression: $regression points — within tolerance)"
                } else {
                    $gain = -$regression
                    Write-Host "  Coverage: $currentCoverage% (baseline: $baselineCoverage%, gain: +$gain points)"
                }
            } else {
                Write-Warning "Baseline file exists but TOTAL line not found. Skipping coverage gate."
            }
        } catch {
            Write-Warning "Could not read baseline file: $_. Skipping coverage gate."
        }
    } else {
        Write-Host "  No baseline found. Run: pytest --cov=src > coverage-baseline.txt"
    }
} else {
    Write-Warning "Could not extract coverage from test output. Skipping coverage gate."
}
```

### `deploy.sh` changes:

```bash
# Replace existing test run (lines 27-29) with combined test+coverage:
echo "Running tests with coverage..."
cd "$DEV_ROOT"
TEST_OUTPUT=$(python -m pytest tests/ --tb=short --cov=src --cov-report=term-missing 2>&1)
TEST_EXIT_CODE=$?

# Check test exit code
if [[ $TEST_EXIT_CODE -ne 0 ]]; then
    echo "Tests failed (exit code $TEST_EXIT_CODE). Aborting deploy." >&2
    echo "$TEST_OUTPUT"
    exit 1
fi

# Extract coverage percentage from TOTAL line
# Format: "TOTAL                      499   1197    29%"
# Note: grep -P is GNU-specific. For macOS, use: sed -n 's/.*TOTAL.*\([0-9]\+\)%/\1/p'
if command -v grep >/dev/null 2>&1 && grep --version 2>&1 | grep -q GNU; then
    CURRENT_COVERAGE=$(echo "$TEST_OUTPUT" | grep -oP "TOTAL.*?\K\d+(?=\s*%)")
else
    # macOS/BSD fallback using sed
    CURRENT_COVERAGE=$(echo "$TEST_OUTPUT" | sed -n 's/.*TOTAL.*\([0-9][0-9]*\)%.*/\1/p')
fi

if [[ -n "$CURRENT_COVERAGE" ]]; then
    echo "  Coverage: ${CURRENT_COVERAGE}%"
    
    BASELINE_FILE="$DEV_ROOT/coverage-baseline.txt"
    if [[ -f "$BASELINE_FILE" ]]; then
        if command -v grep >/dev/null 2>&1 && grep --version 2>&1 | grep -q GNU; then
            BASELINE_COVERAGE=$(grep -oP "TOTAL.*?\K\d+(?=\s*%)" "$BASELINE_FILE" 2>/dev/null || echo "")
        else
            BASELINE_COVERAGE=$(sed -n 's/.*TOTAL.*\([0-9][0-9]*\)%.*/\1/p' "$BASELINE_FILE" 2>/dev/null || echo "")
        fi
        if [[ -n "$BASELINE_COVERAGE" ]]; then
            REGRESSION=$((BASELINE_COVERAGE - CURRENT_COVERAGE))
            if [[ $REGRESSION -gt 5 ]]; then
                echo "ERROR: Coverage regression: ${BASELINE_COVERAGE}% -> ${CURRENT_COVERAGE}% (${REGRESSION} point drop)" >&2
                echo "Deploy aborted. Improve tests before deploying." >&2
                exit 1
            elif [[ $REGRESSION -gt 0 ]]; then
                echo "  Coverage: ${CURRENT_COVERAGE}% (baseline: ${BASELINE_COVERAGE}%, regression: ${REGRESSION} points — within tolerance)"
            else
                GAIN=$((-REGRESSION))
                echo "  Coverage: ${CURRENT_COVERAGE}% (baseline: ${BASELINE_COVERAGE}%, gain: +${GAIN} points)"
            fi
        else
            echo "WARNING: Baseline file exists but TOTAL line not found. Skipping coverage gate." >&2
        fi
    else
        echo "  No baseline found. Run: pytest --cov=src > coverage-baseline.txt"
    fi
else
    echo "WARNING: Could not extract coverage from test output. Skipping coverage gate." >&2
fi
```

### Verification:
- [ ] `deploy.ps1 -DryRun` runs combined test+coverage
- [ ] `deploy.sh --dry-run` runs combined test+coverage
- [ ] Deploy fails when coverage drops > 5 points (test by temporarily lowering `fail_under`)
- [ ] Deploy succeeds when coverage regression <= 5 points
- [ ] Deploy handles missing baseline gracefully (first run)
- [ ] Deploy handles malformed baseline gracefully (warning, not crash)

---

## Implementation Order

1. **Task 1** (Version constant) — foundation, no deps
2. **Task 2** (Temp dir guard + tests) — independent
3. **Task 3** (pytest-cov) — independent of Task 1, but run after to get accurate coverage
4. **Task 4** (Version bump) — needs Task 1 done first (FALLBACK_VERSION)
5. **Task 5** (Deploy gate) — needs Task 3 done first (baseline exists)

---

## Success Criteria

- [ ] `src/engine_version.py` exists with `get_engine_version()`
- [ ] `filter.py`, `scaffold.py`, `update.py` import from `engine_version`
- [ ] `scaffold.py` and `update.py` version embedding regex updated for new import pattern
- [ ] `deploy.ps1` and `deploy.sh` copy `engine_version.py` to live engine
- [ ] `update.py` auto-prunes temp paths with backup
- [ ] `update.py --dry-run` warns but does NOT prune
- [ ] `tests/test_memory.py` has `is_temp_path()` tests
- [ ] `tests/test_memory.py` has `load_projects()` auto-prune tests
- [ ] `pytest --cov=src` runs without error
- [ ] `coverage-baseline.txt` committed
- [ ] `deploy.ps1` and `deploy.sh` fail on >5 point regression
- [ ] `deploy.sh` works on both GNU and BSD grep (macOS compatible)
- [ ] `VERSION` and `pyproject.toml` show `1.16.0`
- [ ] All existing tests pass

---

## Rollback Plan

If issues arise:
1. Revert `src/filter.py`, `src/scaffold.py`, `src/update.py` to use inline `_read_engine_version()`
2. Revert version embedding regex in `scaffold.py` and `update.py`
3. Remove `engine_version.py` from deploy copy lists
4. Remove temp dir guard from `update.py`
5. Remove coverage check from deploy scripts
6. Delete `src/engine_version.py` and `coverage-baseline.txt`
7. Revert `VERSION` and `pyproject.toml` to `1.15.0`
8. Restore `projects.txt` from backup if auto-prune caused issues
