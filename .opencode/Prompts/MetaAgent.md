---
description: Meta-agent that analyzes failure patterns and evolves other agents' prompts to eliminate recurring failures.
mode: subagent
model: opencode-go/deepseek-v4-pro
temperature: 0.2
permission:
  edit: allow
  bash: allow
---
# The Meta-agent (Self-Evolving Prompt Engineer)

**This prompt has been migrated to Windsurf.** See `.windsurf/workflows/meta-agent.md` for the active version.

The Windsurf-native Meta-agent uses these data sources instead of `agent_activity.log`:
- `memory/learnings.jsonl` — structured learnings (failure patterns, bug fixes, architectural patterns)
- `memory/metrics.jsonl` — retrieval effectiveness and quarantine events
- `memory/HANDOFF.md` — session progress and stuck-state detection
- `memory/SYSTEM_INVARIANTS.md` — immutable constraints (read-only)
- Recent Cascade conversations via `trajectory_search`
- Git history for revert/fixup patterns

Invoke via `/meta-agent` in Windsurf, or read `.windsurf/workflows/meta-agent.md` directly.
