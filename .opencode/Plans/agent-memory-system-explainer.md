# Agent Memory System — How It Works (Plain English)

## The Big Picture

Your AI agents forget everything between tasks. This system gives them persistent memory so they stop repeating the same mistakes — and eventually teaches the system to improve itself.

**Three tiers of memory:**

```
┌─────────────────────────────────────────────────────────────┐
│  SYSTEM_INVARIANTS.md (Semantic Memory)                     │
│  "The Law"                                                  │
│  Always loaded. Permanent rules. Updated during Sleep Cycle │
│  only. Strongest rules get codified as regression tests.    │
└─────────────────────────────────────────────────────────────┘
                   ▲
                   │ Consolidated during Sleep Cycle
                   │ (every ~50 tasks). Immune phase prunes
                   │ bad memories. Stale check catches rot.
                   │
┌─────────────────────────────────────────────────────────────┐
│  learnings.jsonl (Episodic Memory)                          │
│  "Recent Experiences"                                       │
│  Pre-filtered per task. Raw discoveries from each task.     │
│  Write-path gate prevents duplicates and malformed entries. │
└─────────────────────────────────────────────────────────────┘
                   ▲
                   │ Appended after each task via filter.py
                   │ --log (validated, deduped, classified)
                   │
┌─────────────────────────────────────────────────────────────┐
│  filter.py (Retrieval Engine)                               │
│  "The Librarian"                                            │
│  Runs before each task. Finds relevant learnings.           │
│  Per-agent context profiles. BM25 + component matching.    │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  HANDOFF.md (Working Memory)                                │
│  "Current State"                                            │
│  Disposable. Overwritten each session. Never consolidated.  │
│  Captures in-flight context so sessions don't cold-start.   │
└─────────────────────────────────────────────────────────────┘
```

---

## The Daily Workflow

### Before a Task Starts

```
┌────────────────────────────────────────────────────────────┐
│ 1. Developer assigns task (e.g., "Implement coin spawner") │
└────────────────────────────────────────────────────────────┘
                             ↓
┌────────────────────────────────────────────────────────────┐
│ 2. GM agent reads HANDOFF.md (if exists)                   │
│    Picks up where the last session left off.               │
└────────────────────────────────────────────────────────────┘
                             ↓
┌────────────────────────────────────────────────────────────┐
│ 3. GM agent runs filter.py                                 │
│    python filter.py --step 6 --components CoinSpawner,Pool │
│      --target_agent gm                                     │
└────────────────────────────────────────────────────────────┘
                             ↓
┌────────────────────────────────────────────────────────────┐
│ 4. filter.py searches learnings.jsonl                      │
│    - Finds entries matching "CoinSpawner" or "Pool"        │
│    - BM25 ranks paraphrases the tags miss                  │
│    - Scores them by recency × importance × relevance       │
│    - Applies score threshold (0.15): only relevant         │
│      entries are injected (critical bypass threshold)      │
│    - Returns only the gm context profile (full spectrum)   │
│    - Splits into WARNINGS (critical) and PATTERNS (useful) │
└────────────────────────────────────────────────────────────┘
                             ↓
┌────────────────────────────────────────────────────────────┐
│ 5. GM agent reads the output                               │
│    ⚠ WARNINGS:                                             │
│    - [step-5, physics] When despawning PooledEntity:       │
│      ALWAYS disable physics body before setActive(false).  │
│                                                            │
│    RELEVANT PATTERNS:                                      │
│    - [step-3, performance] When iterating pool children:   │
│      Use cached getChildren(), not per-frame allocation.   │
└────────────────────────────────────────────────────────────┘
                             ↓
┌────────────────────────────────────────────────────────────┐
│ 6. GM agent writes code while respecting WARNINGS          │
│    (Treats them as immutable constraints)                  │
└────────────────────────────────────────────────────────────┘
```

---

### After a Task Completes

```
┌────────────────────────────────────────────────────────────┐
│ 7. GM agent asks: "Did I discover something non-obvious?"  │
└────────────────────────────────────────────────────────────┘
                             ↓
               ┌─────────────┴─────────────┐
               ↓                           ↓
         ┌──────────┐                ┌──────────┐
         │   YES    │                │    NO    │
         └──────────┘                └──────────┘
               ↓                           ↓
┌─────────────────────────┐      ┌─────────────────┐
│ Log via filter.py:      │      │ Do nothing.     │
│                          │      │ Task complete.  │
│ python filter.py --log   │      └─────────────────┘
│   '{"step": 6,           │
│     "type": "bug_fix",   │
│     ...}'                │
│                          │
│ filter.py validates,     │
│ checks for duplicates    │
│ (exact trigger +         │
│  components match),      │
│ classifies, then appends │
│ (or rejects/amends).     │
└─────────────────────────┘
```

---

## The Swarm (Who Does What)

```
┌─────────────────────────────────────────────────────────────┐
│                    DEVELOPER                                 │
│              (Assigns tasks, reviews PRs)                    │
└─────────────────────────────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────┐
│              GM AGENT (The Executor)                         │
│  - Reads HANDOFF.md, then filtered memory before coding     │
│  - Writes code while respecting WARNINGS                    │
│  - Logs learnings via filter.py --log (not raw append)      │
│  - Overwrites HANDOFF.md at session end                     │
│  - Can challenge invariants via Challenge Protocol          │
└─────────────────────────────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────┐
│           CODE-REVIEWER AGENT (The Enforcer)                 │
│  - Runs filter.py --target_agent code-reviewer (bugs only)  │
│  - Rejects code if GM violated a WARNING                    │
│  - Toggles resolved: true on learnings that GM's code fixed │
│  - Scores utility_score on each injected memory             │
└─────────────────────────────────────────────────────────────┘
```

**Other agents can also contribute:**
- **test-writer** — Appends learnings when tests reveal non-obvious behaviour. Receives `--target_agent test-writer` context (bugs + optimizations).
- **phaser-scout** — Appends learnings when Phaser API constraints are discovered. Receives architectural patterns only.
- **asset-scout** — Appends learnings about asset pipeline constraints.

Each agent receives a specialised context profile (see V2.3 below) — the GM gets the full spectrum, the code-reviewer gets a narrow compliance checklist, and the scouts get only what's relevant to their domain.

---

## The Sleep Cycle (Memory Consolidation)

**When does it happen?**
- Every ~50 tasks (when learnings.jsonl gets too big)
- At the end of each sprint (e.g., after step 10, 20, 30)

**What happens?**

```
┌────────────────────────────────────────────────────────────┐
│ 1. ARCHIVE                                                  │
│    cp learnings.jsonl → archive/sprint-1.jsonl             │
│    (Raw experiences are preserved forever)                 │
└────────────────────────────────────────────────────────────┘
                             ↓
┌────────────────────────────────────────────────────────────┐
│ 2. IMMUNE PHASE (V2.4)                                      │
│    Score memories by actual utility, not just access:      │
│    - PRUNE: toxic memories (high access, negative utility) │
│    - QUARANTINE: unproven memories (injected, never used)  │
│    - FAST-TRACK: proven memories (utility_score >= 3.0)    │
│    - Log all decisions to immune-log-sprint-N.jsonl        │
└────────────────────────────────────────────────────────────┘
                             ↓
┌────────────────────────────────────────────────────────────┐
│ 3. STALE CHECK (V3.3)                                       │
│    For each unresolved entry with files_touched:           │
│    - git diff --stat <entry.commit>..HEAD -- <files>       │
│    - Heavy churn (>60% lines changed) → flag STALE,        │
│      re-verify against current code before promoting       │
│    - Files deleted → mark resolved: true                   │
└────────────────────────────────────────────────────────────┘
                             ↓
┌────────────────────────────────────────────────────────────┐
│ 4. CODIFY (V3.2)                                            │
│    For each promotion candidate:                           │
│    - Can it be a Vitest test or grep check?                │
│      YES → write the test, set codified_as + resolved.     │
│             Do NOT add to SYSTEM_INVARIANTS.md.            │
│      NO  → promote to prose invariant as usual.           │
└────────────────────────────────────────────────────────────┘
                             ↓
┌────────────────────────────────────────────────────────────┐
│ 5. DISTILL                                                  │
│    Review remaining raw log. Extract rules that are:       │
│    - Highly accessed (access_count > 5)                    │
│    - Critical and unresolved                               │
│    - Structural (not workarounds or task-specific)         │
│    - Not stale, not quarantined                            │
└────────────────────────────────────────────────────────────┘
                             ↓
┌────────────────────────────────────────────────────────────┐
│ 6. PROMOTE                                                  │
│    Append distilled rules to SYSTEM_INVARIANTS.md          │
│    under a new sprint header.                              │
│                                                            │
│    Example:                                                │
│    ## Sprint 1 (Steps 1-10)                                │
│    ### Pool Pre-Warming                                    │
│    **Rule:** After creating a group, call                  │
│    group.createMultiple({ quantity: N, ... })              │
│    **Reason:** Creating objects mid-game causes frame      │
│    spikes. Pre-warming ensures smooth 60fps.               │
│    **First discovered:** Step 6                            │
└────────────────────────────────────────────────────────────┘
                             ↓
┌────────────────────────────────────────────────────────────┐
│ 7. RESOLVE CONTRADICTIONS                                   │
│    Review any Challenge Protocol entries. If GM proposed   │
│    superseding an old invariant, update or replace it.     │
│                                                            │
│    Review meta_learning entries (V2.1). Output patch       │
│    proposals for protocol files (filter.py, config, etc.)  │
└────────────────────────────────────────────────────────────┘
                             ↓
┌────────────────────────────────────────────────────────────┐
│ 8. RESET                                                    │
│    echo "" > learnings.jsonl                               │
│    (Start the next cycle with a clean slate)               │
└────────────────────────────────────────────────────────────┘
```

---

## The Challenge Protocol (How Rules Evolve)

**Scenario:** GM agent needs to refactor the entity pool system, but an old invariant says "Never call setActive() directly."

```
┌────────────────────────────────────────────────────────────┐
│ 1. GM agent encounters the blocking invariant               │
│    ⚠ WARNING: [step-5] NEVER call setActive() directly.    │
└────────────────────────────────────────────────────────────┘
                             ↓
┌────────────────────────────────────────────────────────────┐
│ 2. GM agent decides the refactor is necessary               │
│    (The old rule no longer applies after the refactor)     │
└────────────────────────────────────────────────────────────┘
                             ↓
┌────────────────────────────────────────────────────────────┐
│ 3. GM agent logs a contradiction learning                   │
│    {                                                       │
│      "type": "architectural_pattern",                      │
│      "trigger": "When refactoring entity pool system",     │
│      "action": "ALLOW direct setActive() in despawn() if   │
│                 physics body is disabled first",           │
│      "reason": "Old rule from step-5 (source_agent: gm)    │
│                 no longer applies after pool refactor.      │
│                 New architecture guarantees body is         │
│                 disabled before setActive().",             │
│      "severity": "major"                                   │
│    }                                                       │
└────────────────────────────────────────────────────────────┘
                             ↓
┌────────────────────────────────────────────────────────────┐
│ 4. During next Sleep Cycle, the contradiction is reviewed   │
│    - Old invariant is marked [SUPERSEDED at step-15]       │
│    - New rule is promoted to SYSTEM_INVARIANTS.md          │
│    - Auditable trail is preserved                          │
└────────────────────────────────────────────────────────────┘
```

---

## Token Budget (Why This Is Cheap)

| Operation | Token cost |
|---|---|
| Reading warnings (up to 5, gm profile) | ~200–400 tokens |
| Reading patterns (up to 15, gm profile) | ~200–1,200 tokens |
| Reading SYSTEM_INVARIANTS.md | ~500-800 tokens |
| **Total per task (gm)** | **~900–2,400 tokens** |

The score threshold (0.15) dynamically scales injection count to task relevance. Tasks touching well-trodden components get the full 20 entries (~2K tokens). Tasks on novel components get 3-5 highly relevant entries (~900 tokens). Critical-severity entries bypass the threshold entirely.

With per-agent context profiles (V2.3), other agents cost even less:

| Agent | Approximate tokens per injection |
|---|---|
| gm | ~900–1,600 (full spectrum, threshold-filtered) |
| code-reviewer | ~200–600 (bugs only, critical/major) |
| test-writer | ~400–800 (bugs + optimizations) |
| phaser-scout | ~100–300 (API patterns only) |

**Why doesn't cost grow with project size?**

```
learnings.jsonl (500 entries)
         ↓
   filter.py (pre-filters locally)
         ↓
   Score threshold (0.15) filters irrelevant entries
         ↓
   Injects only relevant entries (3-20 depending on task)
         ↓
   ~900–2,400 tokens (scales with relevance, not file size)
```

The pre-filtering script runs locally (no LLM cost). It uses component names, BM25 full-text ranking, and a score threshold, so it's deterministic and fast.

---

## Real-World Example

**Task:** Implement coin spawner (step 6)

**Before coding:**
```
GM reads HANDOFF.md:
  "Step 5 complete. Pool system wired. tsc clean. Next: coin spawner."

GM runs: python filter.py --step 6 --components CoinSpawner,Pool
         --target_agent gm

Output:
## ⚠ WARNINGS
- [step-5, physics, gm] When despawning PooledEntity: ALWAYS disable physics
  body before setActive(false). Reason: ghost collisions on next frame.

## RELEVANT PATTERNS
- [step-3, performance, gm] When iterating pool children: use cached
  getChildren(), not per-frame allocation. Reason: avoids allocation in update().
```

**While coding:**
- GM reads the warnings and ensures `Coin.despawn()` disables physics body before `setActive(false)`
- GM uses cached `getChildren()` in the spawner's update loop

**After coding:**
- GM checks for duplicates: no existing entry matches this trigger + components
- GM logs a learning via filter.py:
  ```bash
  python filter.py --log '{"step": 6, "source_agent": "gm",
    "type": "optimization", "components": ["CoinSpawner", "Pool"],
    "trigger": "When pre-warming coin pool",
    "action": "ALWAYS call group.createMultiple() with quantity from GameConfig.ts",
    "reason": "Creating coins mid-game causes frame spikes on mobile",
    "importance": 8, "severity": "major"}'
  ```
- filter.py validates the JSON, checks for duplicates (none found), appends to learnings.jsonl
- GM overwrites HANDOFF.md with current state

**Code review:**
- Code-reviewer runs `filter.py --target_agent code-reviewer` — gets only bug-fix warnings
- Checks that GM's code respects the physics body warning
- Approves the PR
- Scores `utility_score += 1.0` on the physics warning (GM applied it)
- Toggles `resolved: true` on any learnings that the new code permanently fixed

---

## V2: The Self-Tuning Layer

V1 captures knowledge about the **codebase**. V2 teaches the system to capture knowledge about **itself** — its own workflows, retrieval math, context delivery, and memory quality. Four interlocking upgrades transform the memory layer from a passive log into a self-tuning feedback loop.

### V2.1 — Meta-Learning (Protocol Mutation)

**The problem:** V1 captures codebase knowledge but cannot evolve its own protocol. If `filter.py` constantly injects physics invariants during UI tasks, the agents have no way to flag that the retrieval logic itself is flawed.

**The concept:** Extend the `type` enum with `meta_learning` — a learning about the memory system's own behaviour. Agents log frustrations or optimizations targeting `filter.py`, `AGENT_MEMORY_GUIDE.md`, or `memory-config.json`.

**Example:**
```json
{
  "type": "meta_learning",
  "target_file": "filter.py",
  "proposed_patch": "Add --target_agent flag so UI tasks don't get physics warnings",
  "trigger": "When running filter.py for UI-only tasks",
  "action": "Ignore domain tags and strictly match UI components",
  "reason": "filter.py injected 4 physics warnings into a HUD layout task, wasting ~320 tokens"
}
```

**Sleep Cycle upgrade:** During consolidation, `meta_learning` entries are evaluated separately. Instead of promoting them to `SYSTEM_INVARIANTS.md`, the system outputs a **patch proposal** — a `sed` command, a `git diff`, or a structured edit instruction — to update the targeted protocol file. The developer reviews and applies the patch.

---

### V2.2 — Self-Tuning Decay Algorithms

**The problem:** V1 hardcodes the decay rate (`0.995`) and relevance weights (`1.0 / 0.7 / 0.4`) inside `filter.py`. These values were educated guesses.

**The concept:** Extract all tuning parameters into `memory/memory-config.json`. Agents propose mutations to this file based on observed task outcomes.

```json
{
  "decay_rate": 0.995,
  "component_weight": 1.0,
  "file_weight": 0.7,
  "domain_weight": 0.4,
  "score_threshold": 0.15,
  "max_warnings": 5,
  "max_patterns": 15
}
```

**How tuning works:** The code-reviewer notices that GM violated warnings that had decayed below the injection threshold. It logs a `meta_learning` proposing `"Increase decay_rate from 0.995 to 0.998"`. During the Sleep Cycle, the system evaluates the proposal, outputs a patch, and the developer approves it.

**Guardrails:** Config changes require developer approval. Changes are logged in `SYSTEM_INVARIANTS.md` under a `## Config Mutations` header. In V3, the golden set eval (`--eval`) becomes a hard gate — no config mutation is applied if it regresses retrieval quality.

---

### V2.3 — Swarm-Specific Context Delivery

**The problem:** V1 feeds the same filtered output to every agent. But the code-reviewer doesn't need optimization patterns — it needs strict compliance checklists. Feeding identical context to all agents wastes tokens and dilutes focus.

**The concept:** Add a `--target_agent` flag to `filter.py` that returns specialised context variants:

| `--target_agent` | Types returned | Severity filter | Focus |
|---|---|---|---|
| `gm` | All types | All severities | Full spectrum, weighted toward execution directives |
| `code-reviewer` | `bug_fix` only | `critical` and `major` only | Strict compliance checklist |
| `test-writer` | `bug_fix` and `optimization` | `major` and `critical` | Edge cases and performance traps |
| `phaser-scout` | `architectural_pattern` only | All severities | Phaser API constraints |

**Net effect:** Total swarm token cost per task drops from ~6K (3 agents × ~2K each) to ~3.7K total. Each agent gets higher-signal context.

---

### V2.4 — The Immune System (Memory Health Metrics)

**The problem:** V1 uses `access_count` as the primary signal for memory value. But a memory might be injected 10 times and ignored 10 times — high access, zero utility. Promoting such memories creates "bad laws" that pollute permanent context.

**The concept:** Add a `utility_score` field that tracks whether injected memories actually influenced agent behaviour.

**Scoring mechanics:**

| Event | Score change |
|---|---|
| GM applied the memory's action, reviewer confirmed | +1.0 |
| GM's code violated the memory, reviewer caught it | -0.5 |
| Memory injected but not applicable to the task | -0.2 |

**Sleep Cycle — Immune Phase** (runs before Distill):

| Action | Condition | Result |
|---|---|---|
| **PRUNE** | access_count >= 5 AND utility_score <= -1.0 | Delete "toxic memories" — frequently injected, consistently useless |
| **QUARANTINE** | access_count >= 3 AND utility_score == 0 | Flag "unproven memories" — do not promote, keep one more cycle |
| **FAST-TRACK** | utility_score >= 3.0 | Prioritize "proven memories" for promotion regardless of access_count |

---

## V3: Hardening & Hygiene

V1 captures knowledge about the **codebase**. V2 captures knowledge about the **system itself**. V3 hardens the pipes between them — the write path, retrieval recall, memory freshness, and enforcement — using only proven techniques from production agent-memory systems (Mem0, Letta/MemGPT, Zep, Stanford Generative Agents) and zero new dependencies (Python stdlib + git only).

| Failure mode V3 addresses | V3 answer |
|---|---|
| Duplicate/contradictory memories accumulate | V3.1 — Write-path hygiene |
| Prose rules get ignored; permanent context bloats | V3.2 — Codify to regression tests |
| Memories rot after refactors | V3.3 — Provenance & staleness detection |
| Exact-tag retrieval misses paraphrases | V3.4 — Hybrid BM25 + component retrieval |
| Self-tuning without evaluation is guesswork | V3.5 — Retrieval golden set |
| No working-memory tier; sessions cold-start | V3.6 — Session handoff note |

---

### V3.1 — Write-Path Hygiene (Dedup-or-Update Gate)

**The problem:** V1 lets every agent free-append raw JSON to `learnings.jsonl`. Malformed lines silently corrupt the log, near-duplicate memories accumulate (same lesson re-learned at steps 6, 11, and 19), and contradictory entries coexist with no arbitration.

**V1 already has lightweight dedup:** Before appending, agents check for an existing unresolved entry with matching `trigger` (exact string) + sorted `components`. If found, they increment `access_count` instead of creating a duplicate. This catches obvious duplicates but not semantic duplicates (same lesson, different wording).

**The V3 upgrade:** Make `filter.py --log` the *only* sanctioned write path. Every candidate memory is classified against existing entries before it touches the log, using Jaccard similarity on trigger + action to catch semantic duplicates:

```
┌────────────────────────────────────────────────────────────┐
│ 1. VALIDATE: Parse JSON, check required fields and enums.  │
│    On failure → append to quarantine.jsonl, exit non-zero. │
└────────────────────────────────────────────────────────────┘
                             ↓
┌────────────────────────────────────────────────────────────┐
│ 2. MATCH: Find existing unresolved entries sharing any     │
│    component. Compute token-overlap similarity on          │
│    trigger + action (Jaccard on word sets).                │
└────────────────────────────────────────────────────────────┘
                             ↓
┌────────────────────────────────────────────────────────────┐
│ 3. CLASSIFY:                                               │
│    similarity >= 0.7 → DUPLICATE: reject, bump existing    │
│      entry's access_count. Agent may re-submit with        │
│      --update <ts> to amend it.                            │
│    0.4–0.7 with opposing action → CONFLICT: append, but    │
│      warn agent to follow the Challenge Protocol.          │
│    otherwise → ADD: append as a single JSONL line.         │
└────────────────────────────────────────────────────────────┘
```

**Why ship with V1 Phase 1:** data hygiene cannot be retrofitted. One sprint of free-append produces a log the Sleep Cycle has to manually de-noise.

---

### V3.2 — Codify Phase: Promote Memories to Executable Checks

**The problem:** `SYSTEM_INVARIANTS.md` is prose. Prose costs tokens on every task forever, decays in salience as it grows, and depends on the agent *choosing* to comply.

**The concept:** The strongest form of memory is a regression test — it cannot be forgotten, costs zero context tokens, and self-enforces in CI. Before promoting a rule to prose, ask "can this be a Vitest test or a grep-able lint check instead?"

**Examples:**

| Invariant | Codified form |
|---|---|
| Entity Lifecycle (despawn disables body first) | Vitest test on each PooledEntity subclass |
| No magic numbers outside GameConfig.ts | grep check in CI over `src/**` |
| Async Safety (no await in update()) | grep check: await inside any update() method |
| Pool Pre-Warming | Test asserting group count matches GameConfig cap |

**Schema addition:** Optional `codified_as` field — path to the test or check. Setting it also sets `resolved: true`. Codified rules go into a `## Codified` index in `SYSTEM_INVARIANTS.md` (one line pointing at the test), not into the prose section.

---

### V3.3 — Provenance & Staleness Detection

**The problem:** Memories rot. A rule about `PooledEntity` written at step 5 may be wrong after a step-25 refactor. V1's only defense is time-based decay (which measures age, not correctness).

**The concept:** Anchor each memory to the code state it was learned from, then mechanically check drift before promotion.

**Schema addition:** `commit` field — short git hash, auto-stamped by `filter.py --log` at write time. Agents never fill it manually.

**Sleep Cycle — Stale Check** (runs after Immune phase, before Distill):

```
For each unresolved entry with files_touched:
  churn = git diff --stat <entry.commit>..HEAD -- <files_touched>
  IF churn is heavy (>60% lines changed):
    → flag STALE: do not promote. Re-verify against current code.
  IF files deleted:
    → mark resolved: true with reason "source removed".
```

---

### V3.4 — Hybrid Retrieval: SQLite FTS5 (BM25) + Component Matching

**The problem:** V1 retrieval depends entirely on agents tagging `components` consistently. A memory tagged `["Pool"]` is invisible to a task declared as `["ObjectRecycling", "Spawner"]`.

**The concept:** BM25 full-text ranking via SQLite FTS5 — which ships inside Python's stdlib `sqlite3` module, so this remains zero-install. ~30 lines of code.

**Scoring change:**

```python
relevance = max(
    component_relevance,          # V1: 1.0 / 0.7 / 0.4 hierarchy
    normalized_bm25 * 0.9         # capped below exact component match
)
score = recency * importance * relevance   # unchanged otherwise
```

Exact component matches still rank highest (deterministic, auditable); BM25 catches paraphrases and cross-domain hits the tags miss.

---

### V3.5 — Retrieval Golden Set (Eval Gate for Self-Tuning)

**The problem:** V2.2 lets agents propose mutations to decay rates and relevance weights — but with no measurement, tuning is guesswork.

**The concept:** A small, hand-curated golden set of retrieval cases in `memory/eval/golden.jsonl`, and a `--eval` mode that scores `filter.py` against it.

```json
{"name": "pool despawn task", "step": 10, "components": ["PooledEntity"],
 "files": ["src/entities/Coin.ts"], "domain": "physics",
 "expect_ts": ["2024-01-15T10:30:00Z"],
 "forbid_ts": ["2024-02-01T09:00:00Z"]}
```

| Field | Description |
|---|---|
| `expect_ts` | Entries that MUST appear in output (recall) |
| `forbid_ts` | Entries that must NOT appear (precision) |

**V2.2 guardrail:** A proposed `memory-config.json` mutation is only applied if `--eval` passes at or above the pre-mutation score. Regression = reject the patch.

**V3.5 is a hard prerequisite for V2 Phase 4 (Meta-Learning).** No config mutations without measurement.

---

### V3.6 — Session Handoff Note (Working-Memory Tier)

**The problem:** V1/V2 capture *durable* lessons but nothing captures *state*: "step 14 half-done, tsc failing in `Spawner.ts`, chose approach B over A because of pool cap." Every session cold-starts.

**The concept:** A disposable working-memory file — `memory/HANDOFF.md` — hard cap ~15 lines, **overwritten not appended**:

```markdown
# Handoff — 2026-06-11
- **Current step:** 14 (Spawner zone-aware scenery) — ~60% done
- **In flight:** bush/outback scenery configs wired, spawner switch untested
- **Failing:** npx tsc — 2 errors in Spawner.ts (missing ZoneId narrowing)
- **Decision:** approach B (per-zone spawn tables) over A (weighted single table)
- **Next action:** fix tsc errors, then test zone transition at 1000m
```

**Rules:**
- GM overwrites it at session end; reads it first at session start.
- Never consolidated, never injected by `filter.py`, never promoted.
- Anything durable still goes through `--log`; HANDOFF.md is state, not lessons.

---

## The Full Rollout (Integrated Phases)

```
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 1: STABILIZE (V1 + V3.1 + V3.6 + permission fixes)       │
│                                                                 │
│ Goal: Deploy the base memory system with clean write paths      │
│       and session continuity.                                   │
│                                                                 │
│ Includes:                                                       │
│ - V1 Steps 1-12 (schema, filter.py, swarm, sleep cycle)        │
│ - V3.1 write-path hygiene (cannot be retrofitted)              │
│ - V3.6 handoff note (trivial, immediate payoff)                │
│ - Permission fixes: gm and code-reviewer get filter.py access  │
│                                                                 │
│ Exit criteria:                                                  │
│ - 30+ learnings captured in learnings.jsonl                     │
│ - At least 1 Sleep Cycle completed successfully                 │
│ - SYSTEM_INVARIANTS.md has grown beyond seed content            │
│ - All agents comfortable with the V1 workflow                   │
└─────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 2: IMMUNE SYSTEM (V2.4 + V3.3)                           │
│                                                                 │
│ Goal: Prevent bad memories from entering permanent rules.       │
│       Detect stale memories after refactors.                    │
│                                                                 │
│ Includes:                                                       │
│ - V2.4 utility_score + Immune Phase in Sleep Cycle             │
│ - V3.3 commit stamping + Stale Check phase                     │
│                                                                 │
│ Exit criteria:                                                  │
│ - Code-reviewer consistently scores utility after each task     │
│ - At least 1 Sleep Cycle runs with Immune + Stale phases       │
│ - No "bad laws" promoted (manually verified)                    │
└─────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 3: CONTEXT & CODIFICATION (V2.3 + V3.4 + V3.2)          │
│                                                                 │
│ Goal: Reduce token waste. Improve retrieval recall.             │
│       Start converting prose rules to executable tests.         │
│                                                                 │
│ Includes:                                                       │
│ - V2.3 --target_agent context profiles                         │
│ - V3.4 hybrid BM25 + component retrieval                       │
│ - V3.2 Codify phase in Sleep Cycle                             │
│                                                                 │
│ Exit criteria:                                                  │
│ - Each agent receives only its profile's context types          │
│ - Total swarm token cost per task < 4K                         │
│ - At least 1 invariant codified as a regression test           │
└─────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────┐
│ GATE: V3.5 GOLDEN SET                                           │
│                                                                 │
│ Goal: Build the retrieval eval harness before enabling          │
│       self-tuning.                                              │
│                                                                 │
│ Includes:                                                       │
│ - memory/eval/golden.jsonl with 10-20 curated cases            │
│ - filter.py --eval mode                                         │
│                                                                 │
│ Exit criteria:                                                  │
│ - --eval runs and reports recall/precision                      │
│ - Golden set covers at least 3 retrieval edge cases            │
└─────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 4: META-LEARNING / SELF-TUNING (V2.1 + V2.2)            │
│                                                                 │
│ Goal: Enable the system to evolve its own protocol.             │
│                                                                 │
│ Includes:                                                       │
│ - V2.1 meta_learning type + Protocol Mutation phase            │
│ - V2.2 memory-config.json + self-tuning decay                  │
│ - Config mutations gated by V3.5 --eval (no regression)        │
│                                                                 │
│ Exit criteria:                                                  │
│ - At least 1 meta_learning logged by any agent                  │
│ - At least 1 config mutation proposed, eval-passed, approved   │
│ - filter.py reads all tuning params from memory-config.json    │
└─────────────────────────────────────────────────────────────────┘
```

**Phase dependency graph:**

```
Phase 1 (Stabilize + V3.1 + V3.6)
    │
    ├──→ Phase 2 (Immune + Staleness)
    │        │
    │        └──→ Phase 3 (Context + Retrieval + Codify)
    │                 │
    │                 └──→ V3.5 Golden Set (gate)
    │                          │
    │                          └──→ Phase 4 (Meta-Learning)
    │
    └──→ Phase 3 can also run in parallel with Phase 2
         (they are independent — Immune scores utility,
          Segmentation filters by agent role)
```

---

## Schema Summary (All Versions)

**V1 base fields:**

| Field | Type | Description |
|---|---|---|
| `step` | int | Plan step number |
| `source_agent` | string | Which agent created this |
| `type` | enum | `bug_fix`, `optimization`, `architectural_pattern` |
| `domain` | string | Knowledge domain (physics, performance, etc.) |
| `components` | string[] | Affected components |
| `files_touched` | string[] | Files modified |
| `trigger` | string | When this applies |
| `action` | string | What to do |
| `reason` | string | Why |
| `importance` | int | 1-10 |
| `severity` | enum | `critical`, `major`, `minor` |
| `access_count` | int | How many times injected |
| `resolved` | bool | Whether permanently fixed |
| `ts` | string | ISO timestamp |

**V2 additions:**

| Field | Type | Default | Used by |
|---|---|---|---|
| `meta_learning` (type enum value) | enum | — | meta_learning entries only |
| `utility_score` | float | 0.0 | All types |
| `utility_history` | object[] | [] | All types |
| `target_file` | string | null | meta_learning only |
| `proposed_patch` | string | null | meta_learning only |

**V3 additions:**

| Field | Type | Default | Set by |
|---|---|---|---|
| `commit` | string | auto | `filter.py --log` (auto-stamp) |
| `codified_as` | string | null | Sleep Cycle Codify phase |

---

## File Map

| File | Introduced | Purpose |
|---|---|---|
| `memory/learnings.jsonl` | V1 Phase 1 | Episodic memory log |
| `memory/filter.py` | V1 Phase 1 | Retrieval engine + write-path gate |
| `memory/SYSTEM_INVARIANTS.md` | V1 Phase 1 | Semantic memory (permanent rules) |
| `memory/AGENT_MEMORY_GUIDE.md` | V1 Phase 1 | Agent-facing documentation |
| `memory/memory-config.json` | V1 Phase 1 | Schema reference + V2.2 tuning params |
| `memory/HANDOFF.md` | V1 Phase 1 (V3.6) | Disposable working-memory tier |
| `memory/quarantine.jsonl` | V1 Phase 1 (V3.1) | Malformed/rejected write attempts |
| `memory/archive/sprint-N.jsonl` | V1 | Archived raw logs per sprint |
| `memory/archive/immune-log-sprint-N.jsonl` | Phase 2 | Immune Phase audit trail |
| `memory/eval/golden.jsonl` | Pre-Phase 4 (V3.5) | Hand-curated retrieval eval cases |

---

## Summary

**V1 — Foundation:**
- Agents learn from past mistakes via episodic + semantic memory
- Good patterns are preserved and propagated
- Rules evolve via Challenge Protocol
- Full audit trail (source_agent, step, reason)
- Write-time dedup prevents duplicate entries (exact trigger + components match)
- Score threshold (0.15) dynamically scales token cost to task relevance
- Token cost scales with relevance (~900-2,400 per task) regardless of project size

**V2 — Self-Tuning:**
- System learns about *itself* (meta-learning, protocol mutation)
- Retrieval math is tuned by agents, not hardcoded (including score threshold)
- Each agent gets specialised context (lower cost, higher signal)
- Immune system prevents "bad laws" from entering permanent rules

**V3 — Hardening:**
- Write-path gate (filter.py --log) prevents duplicates, malformed entries, and semantic duplicates (upgrades V1's lightweight dedup)
- Strongest rules are codified as regression tests (zero token cost, self-enforcing)
- Staleness detection catches memories that rot after refactors
- BM25 hybrid retrieval catches paraphrases that component tags miss
- Golden set eval gates all config mutations (no tuning without measurement)
- Session handoff eliminates cold-starts between sessions

**The result:** A memory system that starts simple, stays clean, and gets better over time — without embeddings, vector DBs, or cloud services. Just Python stdlib, git, and disciplined hygiene.
