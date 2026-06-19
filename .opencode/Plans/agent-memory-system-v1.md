# Agent Memory System V1 — Implementation Plan

> **Location:** `.opencode/memory-design/agent-memory-system-v1.md`
> **Status:** Ready for deployment after current sprint.
> **V2 enhancements:** See `agent-memory-system-v2.md` (deferred).

## Overview

Integrate an episodic/semantic memory system into Magpie Swoop's existing tiered rules architecture. The system captures non-obvious learnings during development, pre-filters them for relevance, and periodically consolidates them into durable invariants.

**Current state:** Static tiered rules (tier-1-core.md always loaded, tier-2/3 loaded on demand). No persistent learning capture between sessions.

**Target state:** Dynamic episodic log (learnings.jsonl) + consolidated semantic rules (SYSTEM_INVARIANTS.md) + pre-filtering script (filter.py) + swarm orchestration across GM and subagents + integration with opencode.json and AGENTS.md.

**Key design principles:**
- **Component-based retrieval:** Class and system names endure refactoring; file paths are brittle. Prefer `components` over `files_touched` as retrieval keys.
- **Positive mutation:** Capture not just failures (bug_fix) but also highly efficient patterns (optimization) and structural discoveries (architectural_pattern).
- **Swarm division of labor:** Memory management is distributed across the agent swarm — not the sole responsibility of the primary coding agent.
- **Semantic immutability:** SYSTEM_INVARIANTS.md is immutable during active tasks. Only updated during the Sleep Cycle (consolidation phase).

---

## Implementation Steps

### Step 1: Create Memory Directory Structure

Create the following structure at the project root:

```
memory/
  learnings.jsonl          # Active episodic log (starts empty)
  SYSTEM_INVARIANTS.md     # Consolidated semantic rules (starts with seed content)
  filter.py                # Pre-filtering script
  archive/                 # Empty initially, populated during consolidation
```

**Files to create:**
- `memory/learnings.jsonl` — empty file
- `memory/SYSTEM_INVARIANTS.md` — seed with 3-5 existing rules from tier-1-core.md and tier-2-gameplay.md that are truly invariant (e.g., entity lifecycle, delta-time rule, async safety)
- `memory/filter.py` — Python script for pre-filtering learnings
- `memory/archive/` — empty directory

---

### Step 2: Define the Evolutionary Learning Schema

Every entry in `learnings.jsonl` must adhere strictly to the following schema to ensure accurate retrieval and enable positive architectural mutation.

```json
{
  "step": 12,
  "source_agent": "code-reviewer",
  "type": "bug_fix",
  "domain": "physics",
  "components": ["PooledEntity", "PhysicsBody"],
  "files_touched": ["src/entities/PooledEntity.ts"],
  "trigger": "When despawning a PooledEntity",
  "action": "ALWAYS disable the physics body before calling setActive(false)",
  "reason": "Physics body keeps processing after object is deactivated, causing ghost collisions",
  "importance": 9,
  "severity": "critical",
  "access_count": 0,
  "resolved": false,
  "ts": "2024-01-15T10:30:00Z"
}
```

**Field reference:**

| Field | Type | Description |
|---|---|---|
| `step` | int | Task/step number this was discovered in (1-30 for current plan) |
| `source_agent` | string | Which agent discovered this: `gm`, `code-reviewer`, `test-writer`, `asset-scout`, `phaser-scout`, `plan-reviewer`, `basic-reviewer`, `pro-reviewer` |
| `type` | enum | `bug_fix` — something that broke or would break. `optimization` — a highly efficient code pattern worth preserving. `architectural_pattern` — a structural discovery about how the codebase is organized |
| `domain` | string | Coarse domain tag: `physics`, `ui`, `audio`, `data`, `tooling`, `entities`, `scenes`, `spawner`, `performance`, `mobile` |
| `components` | string[] | Class names, system names, or module identifiers. **Primary retrieval key.** File paths are brittle against refactoring; component names endure. |
| `files_touched` | string[] | Specific file paths involved. Secondary retrieval key, used when component match is ambiguous. |
| `trigger` | string | The "When X" condition that makes this learning relevant |
| `action` | string | The "Then Y" instruction the agent must follow. ALWAYS/NEVER imperative. |
| `reason` | string | *Why* this rule exists at a mechanical level. Not optional — enables accurate consolidation. |
| `importance` | int 1-10 | Agent's honest assessment. 1 = trivial, 10 = will break the build. Don't default to 9. |
| `severity` | enum | `minor` — trivial, `major` — annoying, `critical` — build-breaking or ghost bugs |
| `access_count` | int | Incremented by filter.py each time this entry is injected. High count = high retention value. |
| `resolved` | bool | Set to `true` when the underlying issue is fixed or superseded |
| `ts` | ISO 8601 | Timestamp of when the learning was written |

**Schema design notes:**
- **Routing keys:** Prefer `components` over `files_touched`. File paths are brittle against refactoring; class and system names endure.
- **Positive mutation:** Use the `type` field to capture highly efficient code patterns (`optimization`), not just failures (`bug_fix`). This enables the system to preserve and propagate good patterns, not just avoid bad ones.
- **Condition-Action:** The `trigger` and `action` fields are mandatory. Passive observations are ignored; explicit behavioral directives are required.

---

### Step 3: Write filter.py

Create `memory/filter.py` with the following logic:

**Inputs (CLI args):**
- `--step N` — current step number
- `--components CompA,CompB,CompC` — comma-separated list of component names (classes, systems) the current task touches
- `--files file1,file2` — comma-separated list of file paths (optional, secondary match)
- `--domain domain` — coarse domain tag (optional, fallback)

**Outputs (stdout):**
- `## ⚠ WARNINGS` section (critical severity, unresolved)
- `## RELEVANT PATTERNS` section (non-critical, scored by recency × importance × relevance)

**Scoring formula:**
```python
score = recency * importance * relevance

where:
  recency = 0.995 ^ (current_step - entry.step)
  importance = entry.importance / 10.0
  relevance = hierarchical:
    1.0 if exact component match (any entry in components matches)
    0.7 if file_touched match (any entry in files_touched matches)
    0.4 if domain match
    0.1 otherwise
```

**Score threshold (dynamic injection):**
Instead of injecting a fixed top-N, filter.py applies a minimum score threshold. Only entries above the threshold are injected (up to the max counts). Critical-severity entries bypass the threshold entirely.

```python
SCORE_THRESHOLD = 0.15

if entry.severity == "critical" or score >= SCORE_THRESHOLD:
    inject(entry)
```

What the threshold excludes:
- All entries with no structural match (max score for no-match = `1.0 × 1.0 × 0.1 = 0.10`, always below 0.15)
- Low-importance entries with only domain-level match
- Novel tasks get 3-5 highly relevant entries instead of 20 diluted ones

Score ranges by relevance tier:
```
Component match (1.0):  0.099 – 1.000   ← always injected
File match (0.7):       0.069 – 0.700   ← injected if importance >= 3
Domain match (0.4):     0.040 – 0.400   ← injected if importance >= 4
No match (0.1):         0.010 – 0.100   ← NEVER injected (always below 0.15)
```

**Retention windows:**
- `minor` — drops out after 5 steps (unless access_count > 3)
- `major` — stays for 20 steps
- `critical` — stays until `resolved: true`

**Escalation rule:** If a `critical` learning remains unresolved after 30 steps, flag it for immediate consolidation into `SYSTEM_INVARIANTS.md`.

**Script behavior:**
1. Read all entries from `memory/learnings.jsonl`
2. Filter out `resolved: true` entries
3. Score each entry against current task context (components > files > domain)
4. Sort by score descending
5. Split into warnings (critical) and patterns (non-critical)
6. Increment `access_count` for injected entries (write back to learnings.jsonl)
7. Print formatted output

**Example output:**
```
## ⚠ WARNINGS — Read before starting
- [step-12, physics, code-reviewer] When despawning a PooledEntity: ALWAYS disable the physics
  body before calling setActive(false). Reason: ghost collisions on next frame.

## RELEVANT PATTERNS
- [step-8, ui, gm] When positioning overlays: Z-index on modal overlay must exceed 1000.
- [step-23, audio, gm] When calling AudioManager.play(): always check isMuted() first or
  you'll get a silent promise rejection.
- [step-15, performance, code-reviewer] When iterating pool children: use group.getChildren()
  cached in create(), not per-frame. Reason: avoids allocation in update() loop.
```

---

### Step 4: Swarm Orchestration & Division of Labor

Memory management is distributed across the agent swarm. Responsibilities are strictly divided to maintain data integrity.

#### 4a. Task-Planner Subagent (The Architect)

*Note: This role is currently fulfilled by the GM agent during its "Plan & Orient" phase. If a dedicated task-planner subagent is added later, these responsibilities transfer to it.*

**Cold Start Fix:** Before task assignment, perform static analysis on the requested feature. Generate the anticipated `--components` and `--files` lists to feed the retrieval script.

**Pre-emption:** Read the filtered memory output before drafting the task sequence. Do not assign sub-tasks that contradict known critical invariants.

#### 4b. GameMaker (GM) Agent (The Executor)

**Compliance:** Treat injected critical warnings as immutable constraints for the current code generation step.

**Discovery Logging:** If a non-obvious workaround, race condition, or novel optimization is discovered during execution, append it to `learnings.jsonl` using the schema above. Include `source_agent: "gm"`. Do not log trivial syntax errors.

**Write-time deduplication:** Before appending a new learning, check for an existing unresolved entry with matching `trigger` (exact string match) + sorted `components` tuple. If found, increment the existing entry's `access_count` instead of creating a new one. This prevents duplicate injections and keeps the JSONL lean.

**Challenge Protocol:** If an injected rule actively blocks a necessary architectural change, log a contradiction learning with:
- `type: "architectural_pattern"`
- `trigger` describing the blocking rule and its `source_agent`
- `action` describing the proposed supersede
- `reason` explaining why the old rule no longer applies

This creates an auditable trail. The consolidation phase will review contradictions and update `SYSTEM_INVARIANTS.md` accordingly.

#### 4c. Code-Review Subagent (The Enforcer & Sweeper)

**Rule Verification:** Independently run `python memory/filter.py --step <N> --components <...> --domain <...>` against the submitted code diff. Reject the code if the GM agent violated the mandated `action` field of an active invariant.

**Garbage Collection:** Upon approving a PR, evaluate the active episodic memory. If the GM's code permanently resolves a known `bug_fix`, toggle the corresponding entry to `"resolved": true` in `learnings.jsonl`.

#### 4d. Other Subagents

- **test-writer:** May append `optimization` or `bug_fix` learnings when test discovery reveals non-obvious behavior.
- **phaser-scout:** May append `architectural_pattern` learnings when Phaser API constraints are discovered.
- **asset-scout:** May append learnings related to asset pipeline constraints.
- **basic-reviewer:** Read-only for memory. Does not write learnings.
- **pro-reviewer:** Read-only for memory. Does not write learnings.

---

### Step 5: Seed SYSTEM_INVARIANTS.md

Extract 5-10 truly invariant rules from the existing tiered rules. These are rules that:
- Are structural constraints of the codebase (not style preferences)
- Have been validated across multiple steps
- Would break the build or cause ghost bugs if violated

**Semantic immutability:** This file is immutable during active tasks. Only updated during the Sleep Cycle (consolidation phase) or when a Challenge Protocol contradiction is resolved.

**Seed content (extract from tier-1-core.md and tier-2-gameplay.md):**

```markdown
# Magpie Swoop — System Invariants

Consolidated structural rules. Versioned by sprint/step range.
IMMUTABLE during active tasks. Only updated during Sleep Cycle.

## Steps 1-30 (Initial Development)

### Entity Lifecycle
**Rule:** `spawn()` fully reinitialises. `despawn()` kills tweens, zeros velocity, disables physics body, clears refs, then calls `setActive(false)`.
**Reason:** Physics body keeps processing if disabled after `setActive(false)`, causing ghost collisions. Running tweens on inactive objects cause errors on next activation.
**First discovered:** Step 5 (entity pool implementation)
**Source:** gm

### Delta-Time
**Rule:** `const dt = delta / 1000` at top of `update()`. ALL manual position/velocity updates MUST use `dt`. Tweens exempt.
**Reason:** Phaser's `delta` is in milliseconds. Without conversion, movement speed varies with frame rate, breaking the 60fps target on mobile.
**First discovered:** Step 3 (Magpie movement)
**Source:** gm

### Async Safety
**Rule:** Never `await` in `update()`. Fire-and-forget + EventBus: emit success OR failure event. Game must never hang.
**Reason:** `update()` runs every frame. An unresolved promise blocks the game loop, freezing the entire game.
**First discovered:** Step 18 (AdMob integration)
**Source:** gm

### Scene Communication
**Rule:** EventBus ONLY for cross-scene communication. Never call scene methods directly. Never use `scene.get(key) as SpecificScene`.
**Reason:** Direct scene references create tight coupling and break when scenes are restarted or stopped. EventBus decouples lifecycle.
**First discovered:** Step 7 (HUD scene)
**Source:** gm

### Pool Pre-Warming
**Rule:** After creating a group, call `group.createMultiple({ quantity: N, active: false, visible: false })` with N from `GameConfig.ts`.
**Reason:** Creating objects mid-game causes frame spikes. Pre-warming ensures smooth 60fps on 2018 mid-range Android.
**First discovered:** Step 6 (coin spawner)
**Source:** gm
```

---

### Step 6: Update AGENTS.md

Add a new `## Memory` section to `AGENTS.md` with the following content:

```markdown
## Memory

**Before starting any task:**
1. Run `python memory/filter.py --step <N> --components <CompA,CompB> --files <file1,file2> --domain <domain>`
2. Read the output. Treat `## ⚠ WARNINGS` as immutable constraints for the current task.
3. `memory/SYSTEM_INVARIANTS.md` is always loaded (via opencode.json instructions). It contains permanent structural rules.
4. If a WARNING applies to the current task, re-read the relevant learning before writing code.

**After completing any task:**
- If you discovered something non-obvious (bug, race condition, ordering constraint, optimization, architectural pattern), append a learning to `memory/learnings.jsonl`.
- Use the full schema: include `source_agent`, `type`, `components`, `trigger`, `action`, `reason`.
- Condition-Action format: `trigger` (When X) + `action` (ALWAYS/NEVER Y) + `reason` (mechanical explanation).
- **Write-time dedup:** Before appending, check for an existing unresolved entry with matching `trigger` + sorted `components`. If found, increment its `access_count` instead of creating a duplicate.
- If your task fixes or supersedes a prior learning, set its `resolved: true` in `learnings.jsonl`.

**Challenge Protocol:**
- If an injected rule actively blocks a necessary architectural change, log a contradiction learning.
- Set `type: "architectural_pattern"`, note the `source_agent` of the original rule in the `reason`.
- Propose the supersede in the `action` field. Do not silently overwrite.

**When to write a learning:**
- You hit a bug that wouldn't be obvious from reading the code (`type: "bug_fix"`)
- You discovered a highly efficient code pattern worth preserving (`type: "optimization"`)
- You discovered a structural pattern about how the codebase is organized (`type: "architectural_pattern"`)
- You had to undo an approach because of a downstream effect
- You found that an existing SYSTEM_INVARIANT was wrong or incomplete

**When NOT to write a learning:**
- Things that are obvious from the code
- Things already captured in `SYSTEM_INVARIANTS.md` or tiered rules
- Trivial style preferences (e.g., naming conventions)

**Consolidation (Sleep Cycle — run at end of each sprint or when learnings.jsonl exceeds 50 entries):**
1. Archive: `cp memory/learnings.jsonl memory/archive/sprint-N.jsonl`
2. Distill: Review the raw log. Extract the most highly accessed (via `access_count`) or critical unresolvable rules.
3. Promote: Append distilled rules to `memory/SYSTEM_INVARIANTS.md` under a new sprint header.
4. Reset: `echo "" > memory/learnings.jsonl`
5. Review any Challenge Protocol contradictions and resolve them (update or supersede old invariants).
```

---

### Step 7: Update opencode.json

Add `memory/SYSTEM_INVARIANTS.md` to the `instructions` array so it's always loaded alongside `tier-1-core.md`:

```json
{
  "instructions": [
    ".opencode/rules/tier-1-core.md",
    "memory/SYSTEM_INVARIANTS.md"
  ]
}
```

**Rationale:** SYSTEM_INVARIANTS.md contains durable structural rules that should always be in context, just like tier-1-core.md. The episodic learnings (learnings.jsonl) are injected dynamically by filter.py, so they don't need to be in instructions.

---

### Step 8: Update GM Agent Prompt

Add a memory-awareness block to `.opencode/agents/prompts/gm.md`:

```markdown
### 6. Memory Protocol
* Before starting any task, run `python memory/filter.py --step <N> --components <CompA,CompB> --files <file1,file2> --domain <domain>` and read the output.
* Treat `## ⚠ WARNINGS` as immutable constraints. If a WARNING applies to the current task, re-read the relevant learning before writing code.
* After completing a task, if you discovered something non-obvious, append a learning to `memory/learnings.jsonl` using the full schema (include `source_agent: "gm"`, `type`, `components`, `trigger`, `action`, `reason`).
* **Write-time dedup:** Before appending, check for an existing unresolved entry with matching `trigger` + sorted `components`. If found, increment its `access_count` instead of creating a duplicate.
* If your task resolves a prior learning, set its `resolved: true`.
* **Challenge Protocol:** If an injected rule actively blocks a necessary architectural change, log a contradiction learning with `type: "architectural_pattern"`, note the `source_agent` of the original rule, and propose a supersede. Do not silently overwrite.
```

---

### Step 9: Update Code-Reviewer Agent Prompt

Add memory enforcement responsibilities to `.opencode/agents/prompts/code-reviewer.md`:

```markdown
### Memory Enforcement

**Rule Verification:** Before approving a review, run `python memory/filter.py --step <N> --components <...> --domain <...>` against the submitted code diff. If the GM agent violated the mandated `action` field of an active `## ⚠ WARNINGS` invariant, reject the code with a specific citation.

**Garbage Collection:** Upon approving a PR, evaluate the active episodic memory. If the GM's code permanently resolves a known `bug_fix` learning, toggle the corresponding entry to `"resolved": true` in `memory/learnings.jsonl`.
```

---

### Step 10: Create AGENT_MEMORY_GUIDE.md

Create a top-level `AGENT_MEMORY_GUIDE.md` file that documents:
- The evolutionary schema (with field reference table)
- The three learning types: `bug_fix`, `optimization`, `architectural_pattern`
- Component-based retrieval philosophy (why components > files)
- Swarm division of labor (who writes, who enforces, who resolves)
- How to use filter.py (CLI args, output format)
- The Challenge Protocol (how to propose superseding an invariant)
- The Sleep Cycle (consolidation process with distill/promote/reset)
- How to resolve learnings
- How to supersede SYSTEM_INVARIANTS

This file is the "source of truth" for the memory system. AGENTS.md references it for consolidation instructions.

---

### Step 11: Test the System

**Dry run:**
1. Manually add 3-5 test learnings to `memory/learnings.jsonl` (simulate past discoveries, include at least one of each `type`)
2. Run `python memory/filter.py --step 10 --components PooledEntity,PhysicsBody --files src/entities/PooledEntity.ts --domain physics`
3. Verify output format is correct (`## ⚠ WARNINGS` and `## RELEVANT PATTERNS`)
4. Verify `access_count` is incremented for injected entries
5. Verify resolved entries are excluded
6. Verify component match scores higher than file-only match
7. Verify score threshold excludes entries below 0.15 (no-match entries should not appear)
8. Verify critical-severity entries bypass the threshold (injected regardless of score)

**Integration test:**
1. Start a new task (e.g., step 11 of the plan)
2. Run filter.py with the task's component list
3. Confirm warnings appear in context
4. Complete the task
5. Append a learning if something non-obvious was discovered (include `source_agent`, `type`, `components`)
6. Verify the learning appears in the next filter.py run

**Dedup test:**
1. Add a learning to `learnings.jsonl` with a specific `trigger` + `components`
2. Attempt to add a second learning with the same `trigger` + sorted `components`
3. Verify the first entry's `access_count` is incremented (not a new entry created)
4. Verify the JSONL does not contain duplicate entries

**Swarm test:**
1. Simulate a code-reviewer run: verify it can independently run filter.py and check for invariant violations
2. Simulate garbage collection: verify code-reviewer can toggle `resolved: true` on a learning that the GM's code fixed

---

### Step 12: Define Consolidation Triggers

**Automatic triggers:**
- When `learnings.jsonl` exceeds 50 entries
- At the end of each sprint (e.g., after step 10, 20, 30 of the plan)

**Manual trigger:**
- Developer can run consolidation at any time by following the sleep cycle instructions in AGENT_MEMORY_GUIDE.md

**Sleep Cycle (consolidation process):**
1. **Archive:** `cp memory/learnings.jsonl memory/archive/sprint-N.jsonl`
2. **Distill:** Review the raw log. Extract the most highly accessed (via `access_count`) or critical unresolvable rules. Prioritize entries with `access_count > 5` or `severity: "critical"`.
3. **Promote:** Append distilled rules to `memory/SYSTEM_INVARIANTS.md` under a new sprint header. Include the rule, reason, step first discovered, and source_agent.
4. **Resolve contradictions:** Review any Challenge Protocol contradictions (type: `architectural_pattern` where reason mentions superseding). Update or supersede old invariants accordingly.
5. **Reset:** `echo "" > memory/learnings.jsonl`

---

## Token Budget

| Operation | Token cost |
|---|---|
| Reading filtered warnings (up to 5 entries) | ~200–400 tokens |
| Reading filtered pattern learnings (up to 15 entries) | ~200–1,200 tokens |
| Reading SYSTEM_INVARIANTS.md (mature project) | ~500–800 tokens |
| **Total per task** | **~900–2,400 tokens** |
| Consolidation pass (every ~50 tasks) | ~3,000 tokens, one-time |

The score threshold (0.15) dynamically scales injection count to task relevance. Tasks touching well-trodden components get the full 20 entries (~2K tokens). Tasks on novel components get 3-5 highly relevant entries (~900 tokens). The pre-filtering script runs locally (no LLM cost) and uses component-based matching, so cost stays flat as the project grows.

---

## Migration Path

**No breaking changes.** The existing tiered rules system remains intact:
- `tier-1-core.md` — always loaded (core contracts, state machine, step gate)
- `tier-2-gameplay.md` — loaded on demand (gameplay rules, pool lifecycle, physics)
- `tier-3-refactor.md` — loaded for refactoring (smell catalog, TypeScript rules)
- `SYSTEM_INVARIANTS.md` — always loaded (consolidated structural rules, immutable during tasks)
- `learnings.jsonl` — injected dynamically by filter.py (episodic learnings)

The memory system **augments** the tiered rules, not replaces them. Tiered rules are manually curated and stable. SYSTEM_INVARIANTS.md is automatically consolidated from episodic learnings. learnings.jsonl is the raw capture layer.

---

## Success Criteria

- [ ] `memory/` directory exists with all required files
- [ ] `filter.py` runs without errors and produces correct output format (`## ⚠ WARNINGS` and `## RELEVANT PATTERNS`)
- [ ] `filter.py` correctly prioritizes component match > file match > domain match
- [ ] `filter.py` applies score threshold (0.15) with critical-severity bypass
- [ ] `filter.py` excludes entries with no structural match (score always below 0.15)
- [ ] Write-time dedup prevents duplicate entries (matching `trigger` + sorted `components`)
- [ ] `SYSTEM_INVARIANTS.md` is seeded with 5-10 invariant rules (with source_agent annotations)
- [ ] `AGENTS.md` includes memory protocol instructions (including Challenge Protocol and dedup)
- [ ] `opencode.json` includes `SYSTEM_INVARIANTS.md` in instructions
- [ ] GM agent prompt includes memory-awareness block (including Challenge Protocol and dedup)
- [ ] Code-reviewer agent prompt includes Rule Verification and Garbage Collection responsibilities
- [ ] Dry run test passes (filter.py correctly filters, scores, and thresholds learnings)
- [ ] Dedup test passes (duplicate entries are merged, not appended)
- [ ] Integration test passes (learnings are captured and retrieved correctly)
- [ ] Swarm test passes (code-reviewer can enforce invariants and resolve learnings)

---

## Future Enhancements (V1 Backlog)

- **Automated consolidation:** Write a script that calls the LLM API to run the consolidation prompt
- **Learning quality scoring:** Rate learnings by how often they're accessed and whether they lead to successful task completion
- **Memory visualization:** Generate a dashboard showing learning density by domain, resolution rate, and consolidation history
- **Cross-project memory:** Share component-level learnings across related projects
- **Dedicated task-planner subagent:** Extract the "Architect" role into its own subagent with memory pre-emption responsibilities

> **V2 Enhancements (deferred):** See `agent-memory-system-v2.md` for meta-learning, self-tuning decay, swarm-specific context delivery, and the immune system.
