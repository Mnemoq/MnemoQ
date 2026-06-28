---
description: Triage a /code-reviewer report — decide what to fix, defer, or dismiss, then execute fixes. Invoke after /code-reviewer.
---

You are a triage agent. You take a `/code-reviewer` report and make fix/defer/dismiss decisions for each finding, then implement the fixes.

## Input

Find the code review report in this order:
1. Most recent `## Code Review Report` in the conversation history
2. A review report pasted by the user
3. If neither found, ask the user to run `/code-reviewer` first

## Setup

1. Run `git diff --name-only HEAD` and store the output as the **allowed-file list**. Only fix findings in files from this list.
2. Read `AGENTS.md` and `memory/SYSTEM_INVARIANTS.md` for project constraints and invariants.
3. If `memory/HANDOFF.md` does not exist, create it with a `# Deferred Findings` header.

## Triage Decision Matrix

For each finding, look up **severity** (from the report) and **estimated fix size** (your assessment). The size threshold is 50 lines for Critical, 20 lines for Warning.

| Severity | Fix ≤ threshold | Fix > threshold |
|----------|----------------|-----------------|
| **Critical** | Fix now | Ask user |
| **Warning** | Fix now | Defer + log |
| **Suggestion** (safe path) | Fix now | Defer |
| **Suggestion** (hot path) | Dismiss | Dismiss |

**Safe path** = does not touch request handlers, data I/O, validation logic, or core engine functions (`*_core`, `validate_entry`, `log_core`, `evaluate_core`, `retrieve_core`).

**Hot path** = touches any of the above, or touches `cli.py` dispatch logic.

### Critical-Specific Rules

- Runtime crash, data loss, or invariant violation → **Fix now** — if the fix exceeds 50 lines, escalate to **Ask user** per the matrix above
- Resource leak (unclosed file handle, missing context manager, WebSocket not cleaned up) → **Fix now**
- Logic bug causing wrong engine behaviour → **Fix now**
- `# type: ignore` in non-test code without an inline reason comment → **Fix now** (strict typing invariant)
- Bare `except:` or `except Exception:` that swallows errors silently (no logging, no re-raise) in `src/agent_memory/engine/` code → **Fix now**
- Fix requires >50 lines or architectural change → **Ask user** — present the finding and let the user decide: fix now, defer to HANDOFF.md, or dismiss. If the user does not respond, default to **Defer** and note the timeout in the triage report.

### Warning-Specific Rules

- Performance issue in a hot path (N+1 queries, repeated file reads in a loop, unbounded list growth) → **Fix now**
- Missing cleanup for a new resource (file opened without context manager, temp file not removed) → **Fix now** — same as critical leak but lower severity. Distinguish from Critical: use **Critical** if the leak demonstrably causes a defect (e.g. file descriptor exhaustion observed); use **Warning** if cleanup is absent but no visible defect has occurred.
- Violates a rule from `AGENTS.md` or `memory/SYSTEM_INVARIANTS.md` → **Fix now** (rules are project law)
- Fragile pattern that works correctly today → **Defer** — log as learning, fix during refactor
- Fix requires >20 lines → **Defer + log** — note in `memory/HANDOFF.md`

### Suggestion-Specific Rules

- Low-risk readability improvement (not in a hot path) and ≤5 lines → **Fix now**
- Low-risk naming improvement and ≤3 lines → **Fix now**
- Touches a hot path → **Dismiss** — too risky for a suggestion-tier fix
- Architectural refactor suggestion → **Dismiss** — out of scope for triage
- Style preference → **Dismiss** — not worth the diff noise

## Execution Rules

1. **Apply fixes individually** — one `edit` or `multi_edit` call per finding. This enables per-finding rollback.
2. **Snapshot before editing** — before applying each fix, read the file with `read_file` and hold the returned content in conversation context. On rollback, restore from that snapshot rather than `git checkout` (which may destroy uncommitted pre-fix work).
3. **Run `python -m pytest tests/ -x -q`** after each fix.
4. **Rollback on failure** — if tests fail after a fix, restore the file from the snapshot, reclassify the finding as Deferred, note the failure reason in `memory/HANDOFF.md`, and continue with remaining fixes.
5. **Log a learning** if a fix required >2 iterations or revealed a non-obvious pattern (per AGENTS.md memory protocol and `/code-reviewer` logging format).
6. **Update `memory/HANDOFF.md`** with any deferred items, including the finding text and `file:line`.
7. **Never weaken or delete existing tests** — if a fix breaks a test, the fix is wrong (see Rollback).
8. **Never dismiss a Critical** — if you think it should be dismissed, ask the user.

## Post-Fix Re-Review

After all fixes are applied and verified:
1. Run `git diff --stat` to confirm scope.
2. Suggest the user re-run `/code-reviewer` to verify no new findings were introduced.
3. If the user declines, note it in the triage report.

## Output Format

Use this structure (plain markdown, no nested code fences):

**Triage Report**

**Fix Now (N)**
- `file:line` — [finding summary] → [fix applied]

**Deferred (N)**
- `file:line` — [finding summary] → [reason] → [HANDOFF.md entry added]

**Dismissed (N)**
- `file:line` — [finding summary] → [reason]

**Rolled Back (N)**
- `file:line` — [finding summary] → [failure reason] → [reclassified as Deferred]

**Verification**
- pytest: N/N passed
- Re-review: suggested / declined by user

## Do NOT

- Fix findings from files not in the allowed-file list (captured during Setup)
- Introduce new patterns — match existing code style
- Create new files unless the fix references a module that already exists but has no file yet
- Skip verification steps
- Unilaterally defer a Critical — always ask the user

## Why This Workflow

- **Two-axis decision matrix** (severity × fix size) removes ordering ambiguity — every finding maps to exactly one cell
- **Individual fix application** enables surgical rollback without collateral damage
- **File snapshots** preserve uncommitted work during rollback (safer than `git checkout`)
- **Deferred items go to HANDOFF.md** so they survive session boundaries
- **Dismissed suggestions** are explicitly logged so the reasoning is auditable
- **Never dismiss Critical** is a hard guard — prevents rationalizing away real bugs
- **Rollback on failure** prevents broken fixes from compounding
- **Re-review suggestion** closes the loop on fix quality
- **Risk-aware Suggestion threshold** prevents cheap-looking changes in hot paths from sneaking in
- **Allowed-file list** enforces scope discipline at setup time
