#!/usr/bin/env bash
set -euo pipefail

DEV_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIVE_ENGINE="$HOME/.agent-memory/engine"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

DRY_RUN=false
CANARY_PROJECT=""
FULL_TESTS=false
SKIP_TESTS=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run) DRY_RUN=true; shift ;;
        --canary) CANARY_PROJECT="$2"; shift 2 ;;
        --full) FULL_TESTS=true; shift ;;
        --skip-tests) SKIP_TESTS=true; shift ;;
        *) echo "Unknown flag: $1"; exit 1 ;;
    esac
done

echo "Validating dev project..."
for f in src/filter.py src/profile.py src/scaffold.py src/update.py src/engine_version.py src/shim.py VERSION; do
    if [[ ! -f "$DEV_ROOT/$f" ]]; then
        echo "ERROR: Missing required file: $f" >&2
        exit 1
    fi
done

cd "$DEV_ROOT"
if [[ "$SKIP_TESTS" == "true" ]]; then
    echo "Skipping tests (--skip-tests)"
elif [[ "$FULL_TESTS" != "true" ]]; then
    # Default: fast smoke subset only. The full suite is the responsibility of
    # GitHub CI on push/PR; use --full for a release-grade deploy gate.
    echo "Running smoke tests (fast subset; use --full for full+coverage)..."
    SMOKE_OUTPUT=$(python -m pytest -m smoke -q 2>&1)
    SMOKE_EXIT_CODE=$?
    if [[ $SMOKE_EXIT_CODE -ne 0 ]]; then
        echo "Smoke tests failed (exit code $SMOKE_EXIT_CODE). Aborting deploy." >&2
        echo "$SMOKE_OUTPUT"
        exit 1
    fi
    echo "  Smoke tests passed."
else

echo "Running full test suite with coverage (--full)..."
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
# Use awk for portability (works on both GNU and BSD/macOS)
CURRENT_COVERAGE=$(echo "$TEST_OUTPUT" | awk '/TOTAL.*%/ { for(i=1;i<=NF;i++) if($i ~ /%$/) { gsub(/%/,"",$i); print $i; exit } }')

if [[ -n "$CURRENT_COVERAGE" ]]; then
    echo "  Coverage: ${CURRENT_COVERAGE}%"
    
    BASELINE_FILE="$DEV_ROOT/coverage-baseline.txt"
    if [[ -f "$BASELINE_FILE" ]]; then
        BASELINE_COVERAGE=$(awk '/TOTAL.*%/ { for(i=1;i<=NF;i++) if($i ~ /%$/) { gsub(/%/,"",$i); print $i; exit } }' "$BASELINE_FILE" 2>/dev/null || echo "")
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

fi  # end test-mode selection (smoke / full / skip)

BACKUP_DIR="$LIVE_ENGINE/backups/$TIMESTAMP"
if [[ "$DRY_RUN" == "false" ]]; then
    echo "Backing up live engine to $BACKUP_DIR..."
    mkdir -p "$BACKUP_DIR"
    cp "$LIVE_ENGINE"/*.py "$BACKUP_DIR/" 2>/dev/null || true
    [[ -f "$LIVE_ENGINE/VERSION" ]] && cp "$LIVE_ENGINE/VERSION" "$BACKUP_DIR/"
    [[ -d "$LIVE_ENGINE/templates" ]] && cp -r "$LIVE_ENGINE/templates" "$BACKUP_DIR/templates"
fi

echo "Copying dev files to live engine..."
for pair in "src/filter.py:filter.py" "src/profile.py:profile.py" "src/scaffold.py:scaffold.py" "src/update.py:update.py" "src/engine_version.py:engine_version.py" "src/shim.py:shim.py" "VERSION:VERSION"; do
    src="${pair%%:*}"
    dst="${pair##*:}"
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would copy: $DEV_ROOT/$src -> $LIVE_ENGINE/$dst"
    else
        cp "$DEV_ROOT/$src" "$LIVE_ENGINE/$dst"
        echo "  Copied: $dst"
    fi
done

if [[ "$DRY_RUN" == "true" ]]; then
    echo "  [DRY-RUN] Would copy: templates/ -> $LIVE_ENGINE/templates/"
else
    cp -r "$DEV_ROOT/templates" "$LIVE_ENGINE/templates"
    echo "  Copied: templates/"
fi

echo "Verifying live engine..."
if [[ "$DRY_RUN" == "false" ]]; then
    if ! python "$LIVE_ENGINE/filter.py" --stats > /dev/null 2>&1; then
        echo "Verification failed. Restoring from backup..." >&2
        cp "$BACKUP_DIR"/*.py "$LIVE_ENGINE/"
        cp "$BACKUP_DIR/VERSION" "$LIVE_ENGINE/"
        cp -r "$BACKUP_DIR/templates" "$LIVE_ENGINE/templates"
        exit 1
    fi
    echo "  Verification passed."
fi

if [[ -n "$CANARY_PROJECT" ]]; then
    echo "Updating canary project: $CANARY_PROJECT"
    if [[ "$DRY_RUN" == "false" ]]; then
        python "$LIVE_ENGINE/update.py" --project "$CANARY_PROJECT" --yes
    fi
else
    echo "Updating all registered projects..."
    if [[ "$DRY_RUN" == "false" ]]; then
        python "$LIVE_ENGINE/update.py" --yes
    fi
fi

echo ""
echo "Deploy complete."
if [[ "$DRY_RUN" == "true" ]]; then
    echo "(Dry-run mode - no changes made)"
fi
