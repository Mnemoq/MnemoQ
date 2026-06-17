# Agent Memory Engine

Episodic memory system for AI agents. Provides retrieval, validation, and consolidation of learnings across sessions.

## Development

```powershell
# Set dev environment
$env:AGENT_MEMORY_DIR = "C:\AgentMemoryEngine\src"

# Run tests
python -m pytest tests/

# Smoke test
python src/filter.py --stats
```

## Deploy

```powershell
# Preview
.\scripts\deploy.ps1 -DryRun

# Canary (one project)
.\scripts\deploy.ps1 -CanaryProject "C:\PixelPurge"

# All projects
.\scripts\deploy.ps1
```

## Structure

- `src/` — Engine source (filter, profile, scaffold, update)
- `tests/` — Test suite
- `templates/` — Config templates, prompts, snippets
- `scripts/` — Deploy scripts

## Versioning

Bump `VERSION` file, then deploy. The deploy script copies to `~/.agent-memory/engine/` and runs `update.py` on all registered projects.
