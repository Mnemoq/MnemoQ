# V1 Step 12: Sleep Cycle Implementation

> **Status:** Ready for implementation
> **Prerequisites:** V1 Steps 1-11 complete, `--review-agents` mode implemented
> **Estimated effort:** 3-4 hours

---

## Overview

V1 Step 12 implements the **Sleep Cycle** — the consolidation process that promotes episodic learnings from `learnings.jsonl` to semantic invariants in `SYSTEM_INVARIANTS.md`. The Sleep Cycle is triggered automatically when the episodic log exceeds 50 entries or at sprint boundaries (step 10, 20, 30), and can be triggered manually by the developer.

The consolidation process is **semi-automated**: `filter.py` archives the log, analyzes entries, and generates a promotion report, but the human must review and apply changes to `SYSTEM_INVARIANTS.md` (agents lack edit permission). This ensures invariants are reviewed before becoming permanent rules.

**This step also includes a preview of Phase 2** (AGENTS.md lifecycle integration) — adding an "AGENTS.md Updates Suggested" section to the Sleep Cycle report when learnings reference AGENTS.md.

---

## Current State

**Already implemented:**
- `filter.py` prints a `## SLEEP CYCLE DUE` banner when `unresolved_count > 50` or `current_step ∈ {10, 20, 30}`
- `memory/archive/` directory exists with `.gitkeep`
- `--review-agents` diagnostic mode (Phase 1 of AGENTS.md lifecycle)
- `check_agents_conflict()` function for detecting AGENTS.md overlaps

**Not yet implemented:**
- `--consolidate` mode in `filter.py`
- Promotion candidate analysis (scoring by access_count, severity, recency)
- Contradiction review (architectural_pattern entries proposing supersession)
- Quarantine review (chronic failures as meta_learning signals)
- Staleness detection (V3.3 — entries with old commits and high file churn)
- AGENTS.md integration in Sleep Cycle report (Phase 2 preview)

---

## Implementation Plan

### Phase 1: Add `--consolidate` Mode to filter.py

**File:** `memory/filter.py`

**New CLI flags:**
```bash
python memory/filter.py --consolidate [--sprint N]
python memory/filter.py --consolidate --confirm-reset
```

**Behavior (without --confirm-reset):**

1. **Validate preconditions:**
   - Check that `learnings.jsonl` exists and has unresolved entries
   - Determine sprint number (from `--sprint N` arg, or infer from max `step` in entries)
   - Check if archive file already exists (warn if re-running)

2. **Archive:**
   - Copy `memory/learnings.jsonl` → `memory/archive/sprint-N.jsonl`
   - Print: `✓ Archived {count} entries to memory/archive/sprint-N.jsonl`

3. **Analyze promotion candidates:**
   - Score each unresolved entry using a **promotion score**:
     ```python
     promotion_score = (
         0.4 * min(access_count / 10.0, 1.0) +      # caps at 10 accesses
         0.4 * severity_multiplier +                  # critical=1.0, major=0.6, minor=0.3
         0.2 * max(0.0, 1.0 - step_diff / 30.0)       # decays over 30 steps
     )
     ```
   - Threshold: `promotion_score >= 0.5` OR `severity == "critical"` OR `access_count > 5`

4. **Generate consolidation report:**
   - Output a markdown-formatted report to stdout with sections:
     - **Promotion Candidates:** Entries scoring above threshold, formatted as SYSTEM_INVARIANTS.md entries
     - **Contradictions:** `architectural_pattern` entries where `reason` mentions superseding
     - **Quarantine Review:** Count of entries in `quarantine.jsonl`, sample of recent failures
     - **Stale Entries:** (V3.3) Entries with `commit` field where `git diff --stat <commit>..HEAD -- <files_touched>` shows >60% churn
     - **AGENTS.md Updates Suggested:** (Phase 2 preview) Learnings with AGENTS.md references

5. **Do NOT auto-reset:**
   - Wait for explicit `--confirm-reset` flag before clearing `learnings.jsonl`

**Behavior (with --confirm-reset):**
- Check that `--consolidate` was run in the same session (or within last 10 minutes)
- Clear `learnings.jsonl` (overwrite with empty file)
- Print: `✓ learnings.jsonl reset. Sprint complete.`

---

### Phase 2 Preview: AGENTS.md Integration in Sleep Cycle Report

**What:** When the Sleep Cycle report is generated, check for learnings that reference AGENTS.md and append an "AGENTS.md Updates Suggested" section.

**How:**

1. After generating the SYSTEM_INVARIANTS.md promotion report, scan unresolved learnings for AGENTS.md references:
   - `files_touched` contains "AGENTS.md"
   - `components` contains "agents" (case-insensitive)

2. For each matching learning, append to the report:
   ```markdown
   ## AGENTS.md Updates Suggested

   - [step-11, tooling, gm] When logging a learning via filter.py --log on Windows PowerShell
     → Suggests updating AGENTS.md § Memory (shell escaping guidance)

   - [step-11, optimization, gm] When searching for project documentation
     → Suggests adding search strategy guidance to AGENTS.md § Key Contracts
   ```

3. Human reviews and decides — no auto-edits to AGENTS.md.

**Implementation:**

```python
def get_agents_md_suggestions(entries):
    """Find learnings that suggest AGENTS.md updates."""
    suggestions = []
    for entry in entries:
        files_touched = entry.get("files_touched", [])
        components = entry.get("components", [])
        
        has_agents_ref = (
            any("AGENTS.md" in f for f in files_touched) or
            any("agents" in c.lower() for c in components)
        )
        
        if has_agents_ref:
            suggestions.append(entry)
    
    return suggestions
```

**Integration point:** Call this function after generating the SYSTEM_INVARIANTS.md promotion report, append the "AGENTS.md Updates Suggested" section if any suggestions exist.

---

### Phase 3: Promotion Candidate Analysis

**Logic:**

```python
def score_for_promotion(entry, current_step):
    """Score an entry for promotion to SYSTEM_INVARIANTS.md."""
    access_count = entry.get("access_count", 0)
    severity = entry["severity"]
    step_diff = current_step - entry["step"]
    
    # Access count component (0.0 - 1.0, caps at 10 accesses)
    access_score = min(access_count / 10.0, 1.0)
    
    # Severity component (critical=1.0, major=0.6, minor=0.3)
    severity_map = {"critical": 1.0, "major": 0.6, "minor": 0.3}
    severity_score = severity_map.get(severity, 0.3)
    
    # Recency component (decays over 30 steps)
    recency_score = max(0.0, 1.0 - step_diff / 30.0)
    
    # Weighted sum
    promotion_score = (
        0.4 * access_score +
        0.4 * severity_score +
        0.2 * recency_score
    )
    
    return promotion_score
```

**Promotion criteria (any of):**
- `promotion_score >= 0.5`
- `severity == "critical"` (always promote critical unresolved entries)
- `access_count > 5` (highly accessed entries are proven useful)

---

### Phase 4: Contradiction Review

**Logic:**

Identify `architectural_pattern` entries where the `reason` field mentions superseding or contradicting an existing rule.

**Detection heuristics:**
- `type == "architectural_pattern"`
- `reason` contains keywords: `supersede`, `outdated`, `no longer applies`, `conflicts with`, `replaces`

---

### Phase 5: Quarantine Review

**Logic:**

Read `memory/quarantine.jsonl` and summarize:
- Total count of quarantined entries
- Breakdown by reason (validation errors, JSON parse errors, etc.)
- Sample of recent failures (last 5 entries)

---

### Phase 6: Staleness Detection (V3.3)

**Logic:**

For each unresolved entry with a `commit` field and `files_touched`, check if the files have been heavily modified since the entry was created.

**Implementation:**

```python
def check_staleness(entry):
    """Check if an entry is stale based on git diff."""
    if "commit" not in entry or not entry.get("files_touched"):
        return False, 0
    
    commit = entry["commit"]
    files = entry["files_touched"]
    
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", f"{commit}..HEAD", "--"] + files,
            capture_output=True, text=True, cwd=MEMORY_DIR
        )
        
        if result.returncode != 0:
            return False, 0
        
        # Parse diff stat output to count lines changed
        lines_changed = 0
        for line in result.stdout.strip().split("\n"):
            if "|" in line:
                parts = line.split("|")
                if len(parts) >= 2:
                    changes_str = parts[1].strip().split()[0]
                    try:
                        lines_changed += int(changes_str)
                    except ValueError:
                        pass
        
        # Flag as stale if >100 lines changed (rough heuristic)
        is_stale = lines_changed > 100
        
        return is_stale, lines_changed
    
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False, 0
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `memory/filter.py` | Add `--consolidate` mode with archive, analyze, report generation. Add `--confirm-reset` flag. Add promotion scoring logic. Add staleness detection (V3.3). Add AGENTS.md suggestions section (Phase 2 preview). |
| `AGENTS.md` | Update § Memory with hybrid ownership Sleep Cycle instructions. |
| `AGENT_MEMORY_GUIDE.md` | Update § The Sleep Cycle with automated process. |

---

## Testing Criteria

### Test 1: Consolidation Trigger
**Setup:** Add 51 test entries to `learnings.jsonl` (mix of severities, access_counts).
**Action:** Run `python memory/filter.py --step 10 --components Test`.
**Expected:** Banner prints `## SLEEP CYCLE DUE — 51 entries (threshold 50)`.

### Test 2: Archive Creation
**Setup:** Ensure `learnings.jsonl` has 5+ entries.
**Action:** Run `python memory/filter.py --consolidate --sprint 10`.
**Expected:**
- `memory/archive/sprint-10.jsonl` created with all entries
- Stdout prints `✓ Archived {count} entries to memory/archive/sprint-10.jsonl`
- `learnings.jsonl` is NOT cleared (no `--confirm-reset` yet)

### Test 3: Promotion Candidate Scoring
**Setup:** Add entries with varying access_count, severity, step.
**Action:** Run `python memory/filter.py --consolidate --sprint 10`.
**Expected:**
- Entries with `access_count > 5` appear in promotion candidates
- Entries with `severity == "critical"` appear regardless of access_count
- Entries with `promotion_score >= 0.5` appear
- Entries with low access_count, minor severity, old step do NOT appear

### Test 4: Contradiction Detection
**Setup:** Add an `architectural_pattern` entry with `reason` containing "supersedes".
**Action:** Run `python memory/filter.py --consolidate --sprint 10`.
**Expected:** Entry appears in `## ⚔️ CONTRADICTIONS` section.

### Test 5: Quarantine Review
**Setup:** Add 3 malformed entries to `quarantine.jsonl`.
**Action:** Run `python memory/filter.py --consolidate --sprint 10`.
**Expected:** `## 🗑️ QUARANTINE REVIEW` section shows count=3, breakdown by reason, sample of failures.

### Test 6: Staleness Detection (V3.3)
**Setup:** Add an entry with `commit` pointing to an old commit, `files_touched` pointing to a file that has been modified since.
**Action:** Run `python memory/filter.py --consolidate --sprint 10`.
**Expected:** Entry appears in `## ️ STALE ENTRIES` section with lines_changed count.

### Test 7: Confirm Reset
**Setup:** Run `--consolidate` successfully.
**Action:** Run `python memory/filter.py --consolidate --confirm-reset`.
**Expected:**
- `learnings.jsonl` is cleared (empty file)
- Stdout prints `✓ learnings.jsonl reset. Sprint complete.`

### Test 8: Re-run Prevention
**Setup:** Run `--consolidate --sprint 10` twice.
**Action:** Second run should warn that archive already exists.
**Expected:** Stdout prints `⚠ Warning: memory/archive/sprint-10.jsonl already exists. Overwrite? (y/n)` or similar.

### Test 9: AGENTS.md Suggestions (Phase 2 Preview)
**Setup:** Add a learning with `files_touched: ["AGENTS.md"]`.
**Action:** Run `python memory/filter.py --consolidate --sprint 10`.
**Expected:** Report includes `## AGENTS.md Updates Suggested` section with the learning.

### Test 10: Integration Test
**Setup:** Populate `learnings.jsonl` with 10 realistic entries (mix of bug_fix, optimization, architectural_pattern, one with AGENTS.md reference).
**Action:** Run full Sleep Cycle: `--consolidate --sprint 10` → review report → `--confirm-reset`.
**Expected:**
- Archive created
- Report generated with promotion candidates, contradictions (if any), quarantine review, AGENTS.md suggestions
- Human applies approved entries to `SYSTEM_INVARIANTS.md`
- `learnings.jsonl` cleared
- Next `filter.py --step 11 --components ...` run starts fresh

---

## Success Criteria

- [ ] `filter.py --consolidate` archives `learnings.jsonl` to `archive/sprint-N.jsonl`
- [ ] `filter.py --consolidate` generates a promotion report with candidates, contradictions, quarantine, stale entries
- [ ] Promotion scoring correctly prioritizes high access_count, critical severity, recent entries
- [ ] Contradiction detection identifies `architectural_pattern` entries proposing supersession
- [ ] Quarantine review summarizes failures and provides interpretation guidance
- [ ] Staleness detection flags entries with high git churn (V3.3)
- [ ] `--confirm-reset` clears `learnings.jsonl` only after explicit confirmation
- [ ] Re-run prevention warns if archive already exists
- [ ] AGENTS.md suggestions section appears when learnings reference AGENTS.md
- [ ] `AGENTS.md` updated with hybrid ownership instructions
- [ ] `AGENT_MEMORY_GUIDE.md` updated with automated process
- [ ] All 10 tests pass

---

## Future Enhancements (Deferred to V2/V3)

- **V2.1:** Add `meta_learning` type and Protocol Mutation phase
- **V2.2:** Extract tuning parameters to `memory-config.json`
- **V2.4:** Add Immune Phase (utility_score-based pruning)
- **V3.2:** Add Codify phase (promote memories to executable checks)
- **V3.5:** Add `--eval` mode for retrieval golden set
- **Phase 2 Full:** Extend `--review-agents` with PRUNING CANDIDATES section

---

## Notes

- **Human-in-the-loop:** The Sleep Cycle is deliberately semi-automated. Agents can archive and analyze, but only the human can apply changes to `SYSTEM_INVARIANTS.md` (permission denied in `opencode.json`). This ensures invariants are reviewed before becoming permanent rules.
- **No data loss:** The `--confirm-reset` flag prevents accidental clearing of `learnings.jsonl` before the human has reviewed the report.
- **Sprint numbering:** The `--sprint N` arg is optional. If omitted, infer from max `step` in entries (e.g., max step=12 → sprint 1).
- **Staleness heuristic:** The 100-line churn threshold is a rough heuristic. It may need tuning based on actual file sizes and refactor patterns.
- **Phase 2 preview:** The AGENTS.md suggestions section is informational only. Full Phase 2 (PRUNING CANDIDATES, automated conflict resolution) is deferred until after V1 Step 12 is complete.
