#!/usr/bin/env bash
set -euo pipefail

DEV_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIVE_ENGINE="$HOME/.agent-memory/engine"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

DRY_RUN=false
CANARY_PROJECT=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run) DRY_RUN=true; shift ;;
        --canary) CANARY_PROJECT="$2"; shift 2 ;;
        *) echo "Unknown flag: $1"; exit 1 ;;
    esac
done

echo "Validating dev project..."
for f in src/filter.py src/profile.py src/scaffold.py src/update.py VERSION; do
    if [[ ! -f "$DEV_ROOT/$f" ]]; then
        echo "ERROR: Missing required file: $f" >&2
        exit 1
    fi
done

echo "Running tests..."
cd "$DEV_ROOT"
python -m pytest tests/ --tb=short || { echo "Tests failed. Aborting deploy." >&2; exit 1; }

BACKUP_DIR="$LIVE_ENGINE/backups/$TIMESTAMP"
if [[ "$DRY_RUN" == "false" ]]; then
    echo "Backing up live engine to $BACKUP_DIR..."
    mkdir -p "$BACKUP_DIR"
    cp "$LIVE_ENGINE"/*.py "$BACKUP_DIR/" 2>/dev/null || true
    [[ -f "$LIVE_ENGINE/VERSION" ]] && cp "$LIVE_ENGINE/VERSION" "$BACKUP_DIR/"
    [[ -d "$LIVE_ENGINE/templates" ]] && cp -r "$LIVE_ENGINE/templates" "$BACKUP_DIR/templates"
fi

echo "Copying dev files to live engine..."
for pair in "src/filter.py:filter.py" "src/profile.py:profile.py" "src/scaffold.py:scaffold.py" "src/update.py:update.py" "VERSION:VERSION"; do
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
