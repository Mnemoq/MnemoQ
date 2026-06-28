## Memory

**Session start:** `memory/HANDOFF.md` and `memory/SYSTEM_INVARIANTS.md` are auto-loaded by your IDE/agent platform. Act on HANDOFF's "next action" line if present.

**Before any task — retrieval:**
1. Run `python memory/filter.py --step <N> --components <A,B> --files <f1,f2> --domain <d>` (or `mnemoq --step <N> ...` if installed as a console command)
2. **Component heuristic:** Use exported class/system names from files the task touches. Example: task on `src/models/User.ts` caching → `User,UserCache,Cache`. Never use file paths as components — they endure refactoring.
3. Treat `## ⚠ WARNINGS` as immutable constraints for the current task.
4. If `python` command not found, try `py -3 memory/filter.py ...` (Windows launcher). If still failing, proceed with task but note the failure in `memory/HANDOFF.md`.
5. **Optional:** Run `python memory/filter.py --stats` to inspect memory system health.
6. **Developer Profile:** The engine automatically loads `~/.agent-memory/developer-profile.json` (if it exists) and displays `## 🎯 DEVELOPER PREFERENCES` in retrieval output. These are advisory guidelines from your global profile. Create the file with JSON keys matching your preferences (e.g., `{"prefer_functional_style": true}`).

### Priority Hierarchy

When multiple rules conflict, follow this priority order:

1. **SYSTEM_INVARIANTS.md** (highest priority) — always loaded, immutable, project-specific
2. **`## ⚠ WARNINGS`** — immutable constraints for current task, project-specific
3. **`## 🎯 DEVELOPER PREFERENCES`** — advisory guidelines, developer-wide
4. **`## RELEVANT PATTERNS`** — contextual information, project-specific

**Conflict resolution:**
- If a WARNING conflicts with a profile preference, the WARNING wins
- If a profile preference conflicts with a PATTERN, the profile preference wins
- Profile preferences are advisory — they can be overridden if project context requires

**Logging overrides:**
- If you override a profile preference, log a learning explaining why

**Writing a learning — `--log-file` only (never edit `learnings.jsonl` directly):**

**PowerShell note:** PowerShell strips double quotes from arguments passed to native executables. ALWAYS use `--log-file <path>` instead of `--log '<json>'`. Write the JSON to a temp file first, then pass the file path.

Required fields (11) + Optional fields (4):

| Field | Type | Constraint |
|-------|------|------------|
| `step` | int | See `max_step` in `memory/config.json`. If `max_step` is `null`, no upper bound. |
| `source_agent` | string | See `valid_source_agents` in `memory/config.json` |
| `type` | string | `bug_fix`, `optimization`, `architectural_pattern`, `meta_learning` |
| `domain` | string | See `valid_domains` in `memory/config.json` |
| `components` | string[] | Non-empty array of class/system names |
| `files_touched` | string[] | Non-empty array of file paths |
| `trigger` | string | Must start with "When" (case-insensitive) |
| `action` | string | Must contain "ALWAYS" or "NEVER" (case-insensitive) |
| `reason` | string | Mechanical explanation (non-empty) |
| `importance` | int | 1-10 |
| `severity` | string | `minor`, `major`, `critical` |
| `verified` | bool | Default: `false`. Whether tested/confirmed |
| `scope` | string | Default: `"file"`. `file`, `module`, `system` |
| `symptoms` | string | Default: `""`. Error messages or observable behavior |
| `debt_level` | string | Default: `"proper"`. `proper`, `workaround`, `temporary` |

**Auto-stamped fields (omit these):** `ts`, `commit`, `access_count`, `reinforcement_count`, `resolved` — set automatically by the engine.

**Example (PowerShell-safe, use --log-file):**
```
# Write JSON to temp file, then pass to --log-file
python memory/filter.py --log-file "$env:TEMP\learning.json"
```

**Outcomes:** `ADDED` / `DUPLICATE` (access_count and reinforcement_count bumped) / `CONFLICT` (follow Challenge Protocol) / `QUARANTINED` (fix and re-submit).

**Amending and resolving:**
- `--update <ts> --log-file <path>` — full entry required (all 11 required fields). Not a partial delta.
- `--resolve <ts>` — partial update, sets `resolved: true` only.
- **Timestamp discovery:** `ts` is printed in `DUPLICATE` output.

**Challenge Protocol:**
- If an injected rule actively blocks a necessary architectural change, log a contradiction learning.
- Set `type: "architectural_pattern"`, note the `source_agent` of the original rule in the `reason`.
- Propose the supersede in the `action` field. Do not silently overwrite.

**When to write a learning:**
- You hit a bug that wouldn't be obvious from reading the code
- You discovered a highly efficient code pattern worth preserving
- You discovered a structural pattern about how the codebase is organized
- You had to undo an approach because of a downstream effect
- You found that an existing SYSTEM_INVARIANT was wrong or incomplete

**Automatic Learning Trigger (mandatory):**
- After 2+ failed iterations on the same problem, you MUST log a learning before marking the task complete.
- After any bug fix that required user correction (not self-discovered), you MUST log a learning.
- After discovering a tooling/workflow issue that cost multiple attempts, you MUST log a learning.

**When NOT to write a learning:**
- Things that are obvious from the code
- Things already captured in `SYSTEM_INVARIANTS.md` or tiered rules
- Trivial style preferences
- Anything that doesn't follow the condition-action format

**Consolidation (Sleep Cycle):**

Run when unresolved entries exceed threshold (default 20), 1 day passes since last consolidation, or quarantine exceeds threshold (default 20). All three thresholds are configurable under `tuning` in `memory/config.json` (`sleep_cycle_unresolved_threshold`, `sleep_cycle_days`, `sleep_cycle_quarantine_threshold`; set any to `0` to disable). The engine prints `## SLEEP CYCLE DUE` banner when a trigger fires.

1. **Trigger (automated):** The engine prints banner when unresolved entry count exceeds `sleep_cycle_unresolved_threshold` (default 20), `sleep_cycle_days` since last consolidation (default 1), or quarantine entries exceed `sleep_cycle_quarantine_threshold` (default 20).
2. **GM:** Archive → distill → draft a proposed diff to `SYSTEM_INVARIANTS.md` (output in chat, **NOT applied**).
3. **Human:** Review the proposed diff, apply to `SYSTEM_INVARIANTS.md`.
4. **GM:** Reset `learnings.jsonl` only after human confirms.

**Session End — Evaluate & Auto-Learn (close the loop):**

The memory is only as good as what gets written back. At the end of a session, run the evaluator so learnable moments become entries without hand-building each one. This path is IDE- and agent-agnostic — any tool that can run a shell command can drive it.

- **CLI (works everywhere):** emit a structured summary and run
  `mnemoq --evaluate-file <summary>.json` (or `python memory/filter.py --evaluate-file <path>`).
  - Summary fields: `step`, `prompt_type` (`human`|`agent`), `outcome` (`correction`|`preference`|`bug_fixed`|`decision`|`workaround`|`none`), `text`, `corrected_action`, `rejected_action`, `components`, `files_touched`.
  - Heuristic detectors score the summary: human correction `0.95`, explicit remember `0.85`, bug fixed `0.70`, decision `0.60`, workaround `0.55`.
  - Signals at or above `evaluate_auto_log_threshold` (default `0.5`) are **auto-logged**; the rest are returned as suggestions. Raise the threshold in `memory/config.json` if that is too noisy.
- **MCP-wired agents:** may instead call the `evaluate_prompt` tool each turn with the same ~100-byte summary; auto-logging happens server-side with zero engine tokens.

**Post-commit auto-learn (install once per clone):**
- Run `mnemoq --install-hooks` to install a git `post-commit` hook that runs `mnemoq --auto-learn` in the background after each commit (it never blocks or slows the commit). Auto-learn mines git history and corpus/retrieval metrics for repeated fixes, reverts, under-retrieved/over-injected entries, conflicts, and retrieval failures.

See the [Integration Guide](../docs/integration-guide.md) for the full retrieve → log → evaluate → auto-learn loop and per-IDE wiring tips.
