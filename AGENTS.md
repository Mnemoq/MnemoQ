# Agent Guidelines

## Architecture
cli.py is a thin dispatcher — all logic lives in src/agent_memory/engine/ modules.
Always pass ctx dict and Paths to engine functions; never read module globals directly.

## Data
learnings.jsonl is append-only. Use --update to amend, --resolve to mark resolved.
Never edit learnings.jsonl by hand — schema validation will fail on next load.

## Config
memory/config.json holds project-specific tuning. Copy from templates/config.json to initialize.
VALID_DOMAINS and VALID_SOURCE_AGENTS constrain what entries are accepted.

## Testing
Run `python -m pytest tests/` before committing. Tests live in tests/test_memory.py.
Engine modules are tested via cli.py CLI integration, not direct imports.
Exception: `tests/test_server.py` may import `agent_memory.engine.server.create_app` directly to exercise the HTTP API surface.
Exception: `tests/test_triggers.py` may import `agent_memory.engine.triggers` directly to exercise sleep cycle trigger logic.
Exception: `tests/test_pure_functions.py` may import pure functions directly from engine modules (validation, retrieval, consolidation, auto_learn) for unit testing without side effects.

## Deployment
Bump VERSION file, then run scripts/deploy.ps1. Deploy copies to ~/.agent-memory/engine/.

## Intentional Design Decisions
These are deliberate tradeoffs — do not flag as issues in review:
- **Single validation path**: `validate_entry()` is the source of truth for schema enforcement. API models use `dict[str, Any]` to avoid duplicate validation drift.
- **`*_core` functions return dicts**: Not Pydantic models. Keeps engine decoupled from API layer.
- **No premature abstractions**: If a pattern isn't in the codebase, it was considered and rejected. Suggest only what fits existing conventions.
- **ctx dict is read-only in core functions**: No defensive copy needed. If a core function mutates ctx, that's a bug to flag — not a reason to add copying overhead.
- **Git session lock**: A file-based advisory lock (`.git/.windsurf-git-lock`) prevents concurrent Windsurf sessions from running git-mutating workflows simultaneously. Leaf workflows (`/commit`, `/push`, `/fast-ship`, `/rebase`, `/pr`, `/publish`) acquire and release the lock; `/ship` does not (its delegates handle it). 10-minute stale threshold auto-recovers from crashed sessions. See `.windsurf/workflows/_git-lock.md` for the lock snippet.

## Plan Deviations
When implementing from a plan file, surface any deviation from the plan as an explicit decision point before coding it.
See `.windsurf/workflows/plan-deviation.md` for the full procedure.
