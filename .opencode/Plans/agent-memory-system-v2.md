# Agent Memory System V2 — Future Enhancements

> **Location:** `.opencode/memory-design/agent-memory-system-v2.md`
> **Status:** Deferred. Implement only after V1 Phase 1 exit criteria are met.
> **Prerequisite:** `agent-memory-system-v1.md` must be fully deployed and operational.

---

> **Status:** Deferred. These enhancements build on the V1 foundation and should only be implemented after completing a full 30-step sprint with the base memory system. Each enhancement is designed to be additive — no V1 step needs to be rewritten, only extended.

The V1 system captures knowledge about the **codebase**. V2 teaches the system to capture knowledge about **itself** — its own workflows, retrieval math, context delivery, and memory quality. Four interlocking upgrades transform the memory layer from a passive log into a self-tuning feedback loop.

---

### V2.1 — Meta-Learning (Protocol Mutation)

**The problem:** V1 captures codebase knowledge but cannot evolve its own protocol. If `filter.py` constantly injects physics invariants during UI tasks, the agents have no way to flag that the retrieval logic itself is flawed.

**The concept:** Extend the `type` enum with `meta_learning` — a learning about the memory system's own behaviour. Agents log frustrations or optimizations targeting `filter.py`, `AGENT_MEMORY_GUIDE.md`, or `memory-config.json` (see V2.2).

**Schema addition:**

```json
{
  "step": 22,
  "source_agent": "gm",
  "type": "meta_learning",
  "domain": "tooling",
  "components": ["filter.py", "retrieval"],
  "files_touched": ["memory/filter.py"],
  "trigger": "When running filter.py for UI-only tasks",
  "action": "Ignore domain tags and strictly match UI components — physics invariants pollute the context window",
  "reason": "filter.py injected 4 physics warnings into a HUD layout task, wasting ~320 tokens and distracting from the actual task",
  "importance": 6,
  "severity": "major",
  "access_count": 0,
  "resolved": false,
  "ts": "2024-03-10T14:00:00Z"
}
```

**New field for meta_learning entries only:**

| Field | Type | Description |
|---|---|---|
| `target_file` | string | Which protocol file this learning targets: `filter.py`, `AGENT_MEMORY_GUIDE.md`, or `memory-config.json` |
| `proposed_patch` | string | A human-readable description of the proposed change (e.g., "Add --target_agent flag" or "Reduce domain_weight from 0.4 to 0.2") |

**Sleep Cycle upgrade:** During consolidation (Step 12), the system evaluates `meta_learning` entries separately. Instead of promoting them to `SYSTEM_INVARIANTS.md`, it outputs a **patch proposal** — a `sed` command, a `git diff`, or a structured edit instruction — to update the targeted protocol file. The developer reviews and applies the patch.

**Cross-cutting impacts on V1 plan:**
| V1 Step | Impact |
|---|---|
| Step 2 (Schema) | Add `meta_learning` to `type` enum. Add optional `target_file` and `proposed_patch` fields. |
| Step 3 (filter.py) | filter.py must pass through `meta_learning` entries without scoring them (they target the system, not the codebase). |
| Step 10 (AGENT_MEMORY_GUIDE.md) | Document the meta_learning type and the patch proposal workflow. |
| Step 12 (Sleep Cycle) | Add a "Protocol Mutation" phase: evaluate meta_learning entries, output patch proposals. |

---

### V2.2 — Self-Tuning Decay Algorithms (Algorithmic Evolution)

**The problem:** V1 hardcodes the decay rate (`0.995`) and relevance weights (`1.0 / 0.7 / 0.4`) inside `filter.py`. These values were educated guesses. In a self-improving system, the agents should tune retrieval math based on empirical outcomes, not static assumptions.

**The concept:** Extract all tuning parameters from `filter.py` into a lightweight `memory/memory-config.json` file. Agents can propose mutations to this file based on observed task success/failure patterns.

**New file: `memory/memory-config.json`**

```json
{
  "decay_rate": 0.995,
  "component_weight": 1.0,
  "file_weight": 0.7,
  "domain_weight": 0.4,
  "score_threshold": 0.15,
  "max_warnings": 5,
  "max_patterns": 15,
  "minor_retention_steps": 5,
  "major_retention_steps": 20,
  "escalation_threshold_steps": 30
}
```

**How tuning works in practice:**

```
┌────────────────────────────────────────────────────────────┐
│ OBSERVATION: Code-reviewer rejects GM's code 3 times in    │
│ sprint 2 because GM ignored warnings that had decayed      │
│ below the injection threshold.                             │
└────────────────────────────────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────┐
│ CODE-REVIEWER logs a meta_learning:                        │
│ {                                                          │
│   "type": "meta_learning",                                 │
│   "target_file": "memory-config.json",                     │
│   "proposed_patch": "Increase decay_rate from 0.995 to     │
│                      0.998 — memories decay too fast,      │
│                      causing repeated violations of        │
│                      critical invariants",                 │
│   "trigger": "When critical warnings decay below threshold │
│               before the relevant task is reached",        │
│   "action": "Increase decay_rate to 0.998 to extend the    │
│              half-life of critical memories"                │
│ }                                                          │
└────────────────────────────────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────┐
│ SLEEP CYCLE: Evaluates the meta_learning. If validated     │
│ (e.g., 3+ rejections traced to decay), outputs a patch:   │
│   sed -i 's/"decay_rate": 0.995/"decay_rate": 0.998/'     │
│       memory/memory-config.json                            │
│                                                            │
│ Developer reviews and applies.                             │
└────────────────────────────────────────────────────────────┘
```

**Guardrails:**
- Only `meta_learning` entries with `target_file: "memory-config.json"` can trigger config mutations.
- The Sleep Cycle requires developer approval before applying config patches (these change system behaviour).
- Config changes are logged in `SYSTEM_INVARIANTS.md` under a `## Config Mutations` sub-header for auditability.

**Cross-cutting impacts on V1 plan:**
| V1 Step | Impact |
|---|---|
| Step 1 (Directory) | Add `memory/memory-config.json` to the file creation list. |
| Step 3 (filter.py) | Replace all hardcoded constants (including `SCORE_THRESHOLD`) with reads from `memory-config.json`. |
| Step 4 (Swarm) | Code-reviewer gains authority to propose config mutations via meta_learning. |
| Step 12 (Sleep Cycle) | Add config mutation phase (evaluate, propose patch, await developer approval). |

---

### V2.3 — Swarm-Specific Context Delivery

**The problem:** V1 feeds the same filtered output to every agent. But the task-planner doesn't need physics bug syntax — it needs system boundaries. The code-reviewer doesn't need optimization patterns — it needs strict compliance checklists. Feeding identical context to all agents wastes tokens and dilutes focus.

**The concept:** Add a `--target_agent` flag to `filter.py` that returns specialised context variants tuned for each agent's role.

**New CLI flag:**
```bash
python filter.py --step 6 --components CoinSpawner,Pool --target_agent gm
```

**Context profiles per agent:**

| `--target_agent` | Types returned | Severity filter | Focus |
|---|---|---|---|
| `gm` | All types (`bug_fix`, `optimization`, `architectural_pattern`) | All severities | Full spectrum, weighted toward `action` fields (execution directives) |
| `code-reviewer` | `bug_fix` only | `critical` and `major` only | Strict compliance checklist — "did the GM violate any active invariants?" |
| `task-planner` | `architectural_pattern` and `meta_learning` only | All severities | System boundaries and protocol health — "what are the known constraints on this task?" |
| `test-writer` | `bug_fix` and `optimization` | `major` and `critical` | "What edge cases and performance traps has the codebase encountered?" |
| `phaser-scout` | `architectural_pattern` only | All severities | "What Phaser API constraints have been discovered?" |

**Token budget impact:**

| Agent | Approximate tokens per injection |
|---|---|
| `gm` | ~1,600 (full spectrum) |
| `code-reviewer` | ~600 (narrow, high-signal) |
| `task-planner` | ~400 (architectural only) |
| `test-writer` | ~800 (bugs + optimizations) |
| `phaser-scout` | ~300 (API patterns only) |

**Net effect:** Total swarm token cost per task drops from ~2K × N agents to ~3.7K total (vs ~2K × 3 = 6K if all agents received the full stream). Each agent gets higher-signal context.

**Cross-cutting impacts on V1 plan:**
| V1 Step | Impact |
|---|---|
| Step 3 (filter.py) | Add `--target_agent` flag. Implement type/severity filtering per profile. Score threshold (0.15) applies per-profile — each agent's output is filtered independently. |
| Step 4 (Swarm) | Each agent prompt specifies its `--target_agent` value when calling filter.py. |
| Step 8 (GM prompt) | Update filter.py invocation to include `--target_agent gm`. |
| Step 9 (Code-reviewer prompt) | Update filter.py invocation to include `--target_agent code-reviewer`. |
| Token Budget section | Replace flat ~2K estimate with per-agent breakdown. |

---

### V2.4 — The Immune System (Memory Health Metrics)

**The problem:** V1 uses `access_count` as the primary signal for memory value during consolidation. But a memory might be injected 10 times and ignored 10 times — high access, zero utility. Promoting such memories to `SYSTEM_INVARIANTS.md` creates "bad laws" that pollute permanent context.

**The concept:** Add a `utility_score` field that tracks whether injected memories actually influenced agent behaviour. The Sleep Cycle uses `utility_score`, not just `access_count`, to decide promotion.

**Schema addition:**

```json
{
  "utility_score": 0,
  "utility_history": []
}
```

| Field | Type | Description |
|---|---|---|
| `utility_score` | float | Starts at 0.0. Ranged: -10.0 to +10.0. Positive = memory was useful. Negative = memory was noise. |
| `utility_history` | object[] | Audit trail: `[{ "step": 12, "delta": +1.0, "reason": "GM applied pattern, code-reviewer confirmed" }]` |

**Scoring mechanics:**

```
┌────────────────────────────────────────────────────────────┐
│ AFTER TASK COMPLETION:                                     │
│                                                            │
│ Code-reviewer evaluates each memory that was injected      │
│ into the GM's context during the task:                     │
│                                                            │
│ ┌──────────────────────────────────────────────────────┐   │
│ │ GM applied the memory's action AND it prevented a    │   │
│ │ bug or improved the code?                            │   │
│ │   → utility_score += 1.0                             │   │
│ │   → Log: "GM applied pattern, reviewer confirmed"    │   │
│ └──────────────────────────────────────────────────────┘   │
│                                                            │
│ ┌──────────────────────────────────────────────────────┐   │
│ │ GM's code violated the memory's action AND the       │   │
│ │ reviewer caught it?                                  │   │
│ │   → utility_score -= 0.5                             │   │
│ │   → Log: "GM ignored warning, reviewer rejected"     │   │
│ └──────────────────────────────────────────────────────┘   │
│                                                            │
│ ┌──────────────────────────────────────────────────────┐   │
│ │ Memory was injected but GM didn't encounter the      │   │
│ │ trigger condition at all (irrelevant to task)?       │   │
│ │   → utility_score -= 0.2                             │   │
│ │   → Log: "Injected but not applicable to task"       │   │
│ └──────────────────────────────────────────────────────┘   │
│                                                            │
│ ┌──────────────────────────────────────────────────────┐   │
│ │ Memory was injected 5+ times across tasks and        │   │
│ │ never applied or relevant?                           │   │
│ │   → Flag for pruning (see Sleep Cycle below)         │   │
│ └──────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────┘
```

**Sleep Cycle upgrade — Immune Phase:**

Before the existing Distill phase, add an **Immune Phase**:

```
┌────────────────────────────────────────────────────────────┐
│ IMMUNE PHASE (runs before Distill):                        │
│                                                            │
│ 1. PRUNE: Delete entries where:                            │
│    - access_count >= 5 AND utility_score <= -1.0           │
│    → These are "toxic memories" — frequently injected      │
│      but consistently useless or misleading.               │
│                                                            │
│ 2. QUARANTINE: Flag entries where:                         │
│    - access_count >= 3 AND utility_score == 0              │
│    → These are "unproven memories" — injected but never    │
│      confirmed useful. Do NOT promote to invariants.       │
│      Keep in episodic log for one more sprint cycle.       │
│                                                            │
│ 3. FAST-TRACK: Entries where:                              │
│    - utility_score >= 3.0                                  │
│    → These are "proven memories" — consistently useful.    │
│      Prioritize for promotion regardless of access_count.  │
│                                                            │
│ 4. Log all pruning/quarantine/fast-track decisions to      │
│    memory/archive/immune-log-sprint-N.jsonl                │
└────────────────────────────────────────────────────────────┘
```

**Why this matters:**

| Metric | V1 (access_count only) | V2 (utility_score) |
|---|---|---|
| "Bad law" promoted to invariants | Possible — high-access noise gets promoted | Prevented — low-utility entries are pruned |
| "Good law" missed | Possible — low-access but high-utility entries overlooked | Fast-tracked — proven memories promoted early |
| Memory quality over time | Degrades — noise accumulates | Self-cleans — toxic memories are pruned each cycle |

**Cross-cutting impacts on V1 plan:**
| V1 Step | Impact |
|---|---|
| Step 2 (Schema) | Add `utility_score` (float, default 0.0) and `utility_history` (array, default []) fields. |
| Step 3 (filter.py) | filter.py outputs `utility_score` alongside each injected entry so agents can see it. |
| Step 4c (Code-reviewer) | Code-reviewer gains post-task utility evaluation responsibility (the scoring mechanics above). |
| Step 12 (Sleep Cycle) | Add Immune Phase before Distill. Add pruning/quarantine/fast-track logic. |
| Step 11 (Testing) | Add immune system test: verify toxic memories are pruned, proven memories are fast-tracked. |

---

### V2 Phased Rollout

These enhancements are sequenced to avoid destabilising the V1 foundation. Each phase builds on the previous one.

```
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 1: STABILIZE (Current — V1 deployment)                    │
│                                                                 │
│ Goal: Deploy the base memory system and generate raw data.      │
│                                                                 │
│ Actions:                                                        │
│ - Implement Steps 1-12 from the V1 plan as-is                   │
│ - Seed initial SYSTEM_INVARIANTS                                │
│ - Run a full 30-step sprint to populate learnings.jsonl         │
│ - Do NOT add V2 features yet — let the foundation mature        │
│                                                                 │
│ Exit criteria:                                                  │
│ - 30+ learnings captured in learnings.jsonl                     │
│ - At least 1 Sleep Cycle completed successfully                 │
│ - SYSTEM_INVARIANTS.md has grown beyond seed content            │
│ - All agents are comfortable with the V1 workflow               │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 2: IMMUNE SYSTEM (V2.4)                                   │
│                                                                 │
│ Goal: Prevent bad memories from entering permanent rules.       │
│                                                                 │
│ Why first: utility_score is the highest-impact addition.        │
│ Without it, the first few Sleep Cycles risk promoting noise.    │
│                                                                 │
│ Actions:                                                        │
│ - Add utility_score and utility_history to schema               │
│ - Update code-reviewer prompt with post-task evaluation duty    │
│ - Add Immune Phase to Sleep Cycle                               │
│ - Backfill utility_score: 0 on all existing learnings           │
│                                                                 │
│ Exit criteria:                                                  │
│ - Code-reviewer consistently scores utility after each task     │
│ - At least 1 Sleep Cycle runs with Immune Phase                 │
│ - No "bad laws" promoted (manually verified)                    │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 3: CONTEXT SEGMENTATION (V2.3)                            │
│                                                                 │
│ Goal: Reduce token waste by delivering specialised context.     │
│                                                                 │
│ Actions:                                                        │
│ - Add --target_agent flag to filter.py                          │
│ - Define context profiles per agent (table above)               │
│ - Update each agent prompt to pass its --target_agent value     │
│ - Measure token savings vs V1 baseline                          │
│                                                                 │
│ Exit criteria:                                                  │
│ - Each agent receives only its profile's context types          │
│ - Total swarm token cost per task < 4K (vs ~6K in V1)          │
│ - No agent reports missing critical context                     │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 4: META-LEARNING (V2.1 + V2.2)                           │
│                                                                 │
│ Goal: Enable the system to evolve its own protocol.             │
│                                                                 │
│ Why last: Requires mature V1 data to identify real              │
│ inefficiencies. Premature meta-learning produces noise.         │
│                                                                 │
│ Actions:                                                        │
│ - Add meta_learning to type enum                                │
│ - Create memory-config.json, extract filter.py constants        │
│ - Update filter.py to read from memory-config.json              │
│ - Add Protocol Mutation phase to Sleep Cycle                    │
│ - Instruct agents to begin observing protocol inefficiencies    │
│                                                                 │
│ Exit criteria:                                                  │
│ - At least 1 meta_learning logged by any agent                  │
│ - At least 1 config mutation proposed and developer-approved    │
│ - Filter.py reads all tuning params from memory-config.json     │
└─────────────────────────────────────────────────────────────────┘
```

**Phase dependency graph:**

```
Phase 1 (Stabilize)
    │
    ├──→ Phase 2 (Immune System)  ← highest priority, prevents bad promotions
    │        │
    │        └──→ Phase 3 (Context Segmentation)  ← token savings
    │                 │
    │                 └──→ Phase 4 (Meta-Learning)  ← self-evolution
    │
    └──→ Phase 3 can also run in parallel with Phase 2
         (they are independent — Immune scores utility,
          Segmentation filters by agent role)
```

---

### V2 Schema Summary (All Additions)

For reference, here is the complete V2 schema with all new fields highlighted:

```json
{
  "step": 12,
  "source_agent": "code-reviewer",
  "type": "bug_fix | optimization | architectural_pattern | meta_learning",
  "domain": "physics",
  "components": ["PooledEntity", "PhysicsBody"],
  "files_touched": ["src/entities/PooledEntity.ts"],
  "trigger": "When despawning a PooledEntity",
  "action": "ALWAYS disable the physics body before calling setActive(false)",
  "reason": "Physics body keeps processing after object is deactivated",
  "importance": 9,
  "severity": "critical | major | minor",
  "access_count": 0,
  "utility_score": 0.0,
  "utility_history": [],
  "resolved": false,
  "target_file": "filter.py",
  "proposed_patch": "Increase decay_rate to 0.998",
  "ts": "2024-01-15T10:30:00Z"
}
```

| New in V2 | Type | Default | Used by type |
|---|---|---|---|
| `meta_learning` (type enum value) | enum | — | meta_learning only |
| `utility_score` | float | 0.0 | All types |
| `utility_history` | object[] | [] | All types |
| `target_file` | string | null | meta_learning only |
| `proposed_patch` | string | null | meta_learning only |

---

### V2 New Files

| File | Created in phase | Purpose |
|---|---|---|
| `memory/memory-config.json` | Phase 4 | Externalised tuning parameters for filter.py |
| `memory/archive/immune-log-sprint-N.jsonl` | Phase 2 | Audit trail for Immune Phase pruning/quarantine/fast-track decisions |
