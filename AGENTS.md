# Agent Guidelines

## Architecture
filter.py is a thin dispatcher — all logic lives in src/engine/ modules.
Always pass ctx dict and Paths to engine functions; never read module globals directly.

## Data
learnings.jsonl is append-only. Use --update to amend, --resolve to mark resolved.
Never edit learnings.jsonl by hand — schema validation will fail on next load.

## Config
memory/config.json holds project-specific tuning. Copy from templates/config.json to initialize.
VALID_DOMAINS and VALID_SOURCE_AGENTS constrain what entries are accepted.

## Testing
Run `python -m pytest tests/` before committing. Tests live in tests/test_memory.py.
Engine modules are tested via filter.py CLI integration, not direct imports.

## Deployment
Bump VERSION file, then run scripts/deploy.ps1. Deploy copies to ~/.agent-memory/engine/.
