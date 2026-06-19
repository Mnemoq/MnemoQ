# Agent Memory System V3 — Hardening & Hygiene

> **Location:** `.opencode/memory-design/agent-memory-system-v3.md`
> **Status:** Proposal. V3.1 and V3.6 should ship *inside* V1 Phase 1; everything else is additive and deferred.
> **Prerequisites:** `agent-memory-system-v1.md` (foundation), `agent-memory-system-v2.md` (self-tuning).

---

V1 captures knowledge about the **codebase**. V2 captures knowledge about the **system itself**. V3 hardens the pipes between them — the write path, retrieval recall, memory freshness, and enforcement — using only proven techniques from production agent-memory systems (Mem0, Letta/MemGPT, Zep, Reflexion, Stanford Generative Agents) and zero new dependencies (Python stdlib + git only).

A validating note: V1's scoring formula (`recency × importance × relevance`) is the retrieval function from Stanford's *Generative Agents* paper. The foundation is sound. V3 addresses the failure modes those systems documented around it:

| Failure mode | Documented where | V3 answer |
|---|---|---|
| Duplicate/contradictory memories accumulate | Mem0 write pipeline (ADD/UPDATE/DELETE/NOOP) | V3.1 |
| Prose rules get ignored; permanent context bloats | Universal; regression tests are the fix | V3.2 |
| Memories rot after refactors | Zep/Letta fact invalidation | V3.3 |
| Exact-tag retrieval misses paraphrases | Hybrid BM25 + structured retrieval (standard RAG practice) | V3.4 |
| Self-tuning without evaluation is guesswork | Every serious RAG deployment evals retrieval | V3.5 |
| No working-memory tier; sessions cold-start | MemGPT working memory, Claude Code compaction | V3.6 |

---

## V3.1 — Write-Path Hygiene (Dedup-or-Update Gate)

**The problem:** V1 lets every agent free-append raw JSON to `learnings.jsonl`. Three documented failure modes follow: malformed lines silently corrupt the log, near-duplicate memories accumulate (same lesson re-learned at steps 6, 11, and 19 becomes three entries), and contradictory entries coexist with no arbitration until the Sleep Cycle.

**The concept:** Make `filter.py` the *only* sanctioned write path, mirroring Mem0's write pipeline: every candidate memory is classified against existing entries before it touches the log.

**New CLI mode:**

```bash
python memory/filter.py --log '{"step": 11, "source_agent": "gm", "type": "bug_fix", ...}'
```

**Gate logic:**

```
┌────────────────────────────────────────────────────────────┐
│ 1. VALIDATE: Parse JSON, check required fields and enums   │
│    against the schema. On failure → append the raw line to │
│    memory/quarantine.jsonl with a reason, exit non-zero.   │
│    The main log is never corrupted.                        │
└────────────────────────────────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────┐
│ 2. MATCH: Find existing unresolved entries sharing any     │
│    component. Compute token-overlap similarity on          │
│    trigger + action (stdlib only: lowercase, split,        │
│    Jaccard on word sets).                                  │
└────────────────────────────────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────┐
│ 3. CLASSIFY:                                               │
│    similarity >= 0.7 → DUPLICATE: reject, print the        │
│      existing entry, increment its access_count.           │
│      Agent may re-submit with --update <ts> to amend it    │
│      (e.g. raise importance, refine action).               │
│    0.4–0.7 with opposing action → CONFLICT: append, but    │
│      print a warning instructing the agent to follow the   │
│      Challenge Protocol (V1 Step 4b) explicitly.           │
│    otherwise → ADD: append as a single JSONL line.         │
└────────────────────────────────────────────────────────────┘
```

**Guardrails:**
- Agents never edit `learnings.jsonl` directly except for the V1-sanctioned `resolved: true` toggle (code-reviewer GC duty). Everything else goes through `--log`.
- Quarantined lines are reviewed during the Sleep Cycle — chronic quarantine from one agent is itself a `meta_learning` signal (V2.1).
- Single-line appends keep concurrent writes from interleaving mid-record (multi-writer JSONL risk).

**Cross-cutting impacts on V1 plan:**
| V1 Step | Impact |
|---|---|
| Step 3 (filter.py) | Add `--log` and `--update` modes with validate → match → classify pipeline. |
| Step 6 (AGENTS.md) | Replace "append a learning to learnings.jsonl" with "log via `python memory/filter.py --log '<json>'`". |
| Step 8/9 (Agent prompts) | Same instruction change for GM and code-reviewer. |
| Step 11 (Testing) | Add tests: malformed entry quarantined; duplicate rejected with access_count bump; conflict flagged. |

**Why ship with V1 Phase 1:** data hygiene cannot be retrofitted. One sprint of free-append produces a log the Sleep Cycle has to manually de-noise.

---

## V3.2 — Codify Phase: Promote Memories to Executable Checks

**The problem:** `SYSTEM_INVARIANTS.md` is prose. Prose costs tokens on every task forever, decays in salience as it grows, and depends on the agent *choosing* to comply. The repo already has Vitest + CI wired with an empty suite.

**The concept:** The strongest form of memory is a regression test — it cannot be forgotten, costs zero context tokens, and self-enforces in CI. Add a **Codify phase** to the Sleep Cycle: before promoting a rule to prose, ask "can this be a Vitest test or a grep-able lint check instead?"

**Examples from the existing seed invariants:**

| Invariant | Codified form |
|---|---|
| Entity Lifecycle (`despawn()` disables body before `setActive(false)`) | Vitest test on each `PooledEntity` subclass asserting body state after `despawn()` |
| No magic numbers outside `GameConfig.ts` | grep/lint check in CI over `src/**` for numeric literals in gameplay code |
| Async Safety (no `await` in `update()`) | grep check: `await` inside any `update(` method body |
| Pool Pre-Warming | Test asserting `group.getLength() === GameConfig` cap after `GameDirector.init` |

**Schema addition (optional field):**

| Field | Type | Description |
|---|---|---|
| `codified_as` | string | Path to the test or check that now enforces this learning (e.g., `tests/entities/pooled-entity-lifecycle.test.ts`). Setting it also sets `resolved: true`. |

**Sleep Cycle upgrade — Codify phase (runs during Distill):**

```
For each promotion candidate:
  1. Can it be expressed as a Vitest test or grep check?
     YES → write the check, set codified_as + resolved: true.
           Do NOT add to SYSTEM_INVARIANTS.md (one line in a
           "## Codified" index instead, pointing at the test).
     NO  → promote to prose invariant as in V1.
```

**Why this matters:** `SYSTEM_INVARIANTS.md` is always-loaded context. Without codification it grows monotonically and the V1 token budget (~500–800 tokens) is fiction by sprint 5. With it, the prose layer holds only rules that genuinely cannot be mechanised.

**Cross-cutting impacts:**
| Step | Impact |
|---|---|
| V1 Step 2 (Schema) | Add optional `codified_as` field. |
| V1 Step 12 (Sleep Cycle) | Insert Codify phase inside Distill, before Promote. |
| V2.4 (Immune) | Fast-tracked memories (`utility_score >= 3.0`) are prime codification candidates. |
| test-writer agent | Natural owner of writing the codified tests during consolidation. |

---

## V3.3 — Provenance & Staleness Detection

**The problem:** Memories rot. A rule about `PooledEntity` written at step 5 may be wrong after a step-25 refactor. V1's only defenses are time-based decay (which measures age, not correctness) and the Challenge Protocol (which requires an agent to *notice* the rot mid-task).

**The concept:** Anchor each memory to the code state it was learned from, then mechanically check drift before promotion — the same pattern Zep/Letta use for fact invalidation, implemented with git alone.

**Schema addition:**

| Field | Type | Description |
|---|---|---|
| `commit` | string | Short git hash (`git rev-parse --short HEAD`) at write time. Stamped automatically by `filter.py --log` (V3.1) — agents never fill it manually. |

**Sleep Cycle upgrade — Stale Check (runs before Distill, after V2.4's Immune phase):**

```
For each unresolved entry with files_touched:
  churn = git diff --stat <entry.commit>..HEAD -- <files_touched>
  IF churn is heavy (file rewritten / lines changed > ~60%):
    → flag entry as STALE: do not promote this cycle.
      Re-verify against current code (read the file, confirm
      trigger/action still hold) or mark resolved: true.
  IF files deleted:
    → mark resolved: true with reason "source removed".
```

**Why components-first retrieval still wins:** staleness checking uses `files_touched` precisely because paths are brittle — that brittleness is the *signal* here. Retrieval continues to key on `components`.

**Cross-cutting impacts:**
| Step | Impact |
|---|---|
| V1 Step 2 (Schema) | Add `commit` field (auto-stamped). |
| V1 Step 3 (filter.py) | `--log` mode stamps `commit` via `subprocess` + git. |
| V1 Step 12 (Sleep Cycle) | Add Stale Check phase. |
| V2.4 (Immune) | Stale + low-utility = prune immediately. |

---

## V3.4 — Hybrid Retrieval: SQLite FTS5 (BM25) + Component Matching

**The problem:** V1 retrieval depends entirely on agents tagging `components` consistently. A memory tagged `["Pool"]` is invisible to a task declared as `["ObjectRecycling", "Spawner"]`. Embeddings would fix this but are overkill for a 30–500 entry corpus (heavy deps, non-determinism, cold-start cost).

**The concept:** BM25 full-text ranking via SQLite FTS5 — which ships inside Python's stdlib `sqlite3` module, so this remains zero-install. The most battle-tested lexical retrieval algorithm in existence, in ~30 lines.

**Mechanics:**

```python
# Inside filter.py, per invocation (corpus is tiny — rebuild is <10ms):
db = sqlite3.connect(":memory:")
db.execute("CREATE VIRTUAL TABLE mem USING fts5(ts, body)")
# body = trigger + action + reason + components + domain, per entry
# query = task components + files + domain, space-joined
rows = db.execute(
    "SELECT ts, bm25(mem) FROM mem WHERE mem MATCH ? ORDER BY bm25(mem)",
    (query,))
```

**Scoring change:**

```python
relevance = max(
    component_relevance,          # V1: 1.0 / 0.7 / 0.4 / 0.1 hierarchy
    normalized_bm25 * 0.9         # capped below exact component match
)
score = recency * importance * relevance   # unchanged otherwise
```

Exact component matches still rank highest (deterministic, auditable); BM25 catches paraphrases and cross-domain hits the tags miss. Output format, retention windows, and access_count behaviour are unchanged.

**Cross-cutting impacts:**
| Step | Impact |
|---|---|
| V1 Step 3 (filter.py) | Add FTS5 index + max() relevance blend. |
| V1 Step 11 (Testing) | Add test: paraphrased query retrieves entry with no tag overlap. |
| V2.2 (Self-tuning) | `bm25_weight` (0.9) and `bm25_floor` become `memory-config.json` parameters. |
| V2.3 (Segmentation) | Per-agent profiles filter the hybrid result set identically. |

---

## V3.5 — Retrieval Golden Set (Eval Gate for Self-Tuning)

**The problem:** V2.2 lets agents propose mutations to decay rates and relevance weights — but with no measurement, "increase decay_rate to 0.998" is vibes. Production RAG/memory systems never tune retrieval without an evaluation harness.

**The concept:** A small, hand-curated golden set of retrieval cases, and a `--eval` mode that scores `filter.py` against it. This becomes the **mandatory gate for all V2 Phase 4 config mutations**.

**New file: `memory/eval/golden.jsonl`** (10–20 cases, grown over time):

```json
{"name": "pool despawn task", "step": 10, "components": ["PooledEntity"], "files": ["src/entities/Coin.ts"], "domain": "physics", "expect_ts": ["2024-01-15T10:30:00Z"], "forbid_ts": ["2024-02-01T09:00:00Z"]}
```

| Field | Description |
|---|---|
| `expect_ts` | Entry timestamps that MUST appear in output (recall) |
| `forbid_ts` | Entries that must NOT appear (precision — e.g. physics noise in a UI task) |

**New CLI mode:**

```bash
python memory/filter.py --eval
# → recall: 18/19 (94.7%)   precision violations: 1   FAIL: "hud layout task" leaked physics entry
```

**V2.2 guardrail upgrade:** a proposed `memory-config.json` mutation is only applied if `--eval` passes at or above the pre-mutation score. Run before and after; regression = reject the patch. Golden cases are added whenever a retrieval failure is observed in the wild (a missed warning that caused a reviewer rejection becomes a new `expect_ts` case).

**Cross-cutting impacts:**
| Step | Impact |
|---|---|
| V1 Step 1 (Directory) | Add `memory/eval/golden.jsonl`. |
| V1 Step 3 (filter.py) | Add `--eval` mode. |
| V2.2 (Self-tuning) | Eval pass becomes a hard precondition for config mutation approval. |
| V2 Phase 4 | V3.5 is a prerequisite — do not enter Phase 4 without the golden set. |

---

## V3.6 — Session Handoff Note (Working-Memory Tier)

**The problem:** V1/V2 capture *durable* lessons but nothing captures *state*: "step 14 half-done, tsc failing in `Spawner.ts`, chose approach B over A because of pool cap." Every session cold-starts; the agent re-derives in-flight context from git archaeology.

**The concept:** A third, disposable memory tier below episodic — the working-memory pattern from MemGPT and Claude Code's auto-compaction.

**New file: `memory/HANDOFF.md`** — hard cap ~15 lines, **overwritten not appended**:

```markdown
# Handoff — 2026-06-11
- **Current step:** 14 (Spawner zone-aware scenery) — ~60% done
- **In flight:** bush/outback scenery configs wired, spawner switch untested
- **Failing:** npx tsc — 2 errors in Spawner.ts (missing ZoneId narrowing)
- **Decision:** approach B (per-zone spawn tables) over A (weighted single table) — pool cap pressure
- **Next action:** fix tsc errors, then test zone transition at 1000m
```

**Rules:**
- GM overwrites it at session end (or before context compaction); reads it first at session start.
- Never consolidated, never injected by `filter.py`, never promoted — it is deliberately disposable.
- Anything durable discovered mid-session still goes through `--log` (V3.1); HANDOFF.md is state, not lessons.

**Cross-cutting impacts:**
| Step | Impact |
|---|---|
| V1 Step 1 (Directory) | Add `memory/HANDOFF.md` (seed: "No active handoff"). |
| V1 Step 6 (AGENTS.md) | Add read-at-start / overwrite-at-end instructions. |
| V1 Step 8 (GM prompt) | Add handoff duty to the Workflow loop (read in "Plan & Orient", write after "Commit"). |

---

## Repo Gaps (must fix during V1 deployment)

These were verified against the current `opencode.json` and block the V1 plan as written:

### Gap 1 — Permissions block the swarm's memory duties

| Agent | V1 duty | Current permission | Fix |
|---|---|---|---|
| `code-reviewer` | Run `python memory/filter.py` (Rule Verification, Step 4c) | bash allowlist = `git diff/log/status` + `tsc` only → **denied** | Add `"python memory/filter.py *": "allow"` to its bash permissions |
| `code-reviewer` | Toggle `resolved: true` in `learnings.jsonl` (Garbage Collection, Step 4c) | `edit: "deny"` → **denied** | Scope edit: `{"memory/learnings.jsonl": "allow", "*": "deny"}` |
| `gm` | Run filter.py before every task | No `python` rule → falls to `"*": "ask"` → **prompts developer every task** | Add `"python memory/filter.py *": "allow"` to gm's bash permissions |

Note the allow rule is deliberately narrow (`python memory/filter.py *`, not `python *`) — agents get the memory tool, not a general Python interpreter.

### Gap 2 — Multi-writer JSONL concurrency

Multiple agents (gm, code-reviewer, test-writer, scouts) may write learnings in overlapping sessions. Mitigation is V3.1: single write path, single-line atomic appends, quarantine for anything malformed. No locking infrastructure needed at this scale.

---

## V3 Phased Rollout (extends the V2 phase graph)

```
┌─────────────────────────────────────────────────────────────────┐
│ V1 PHASE 1: STABILIZE  ← amended to include:                    │
│   + Gap 1 permission fixes (prerequisite for everything)        │
│   + V3.1 write-path hygiene (cannot be retrofitted)             │
│   + V3.6 handoff note (trivial, immediate payoff)               │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ V2 PHASE 2: IMMUNE SYSTEM                                       │
│   + V3.3 provenance & staleness (commit stamping + Stale Check  │
│     slots naturally beside the Immune phase)                    │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ V2 PHASE 3: CONTEXT SEGMENTATION                                │
│   + V3.4 hybrid retrieval (FTS5/BM25 blend)                     │
│   + V3.2 codify phase (from the first real Sleep Cycle onward)  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ V3.5 GOLDEN SET  ← hard prerequisite gate for ↓                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ V2 PHASE 4: META-LEARNING / SELF-TUNING                         │
│   (config mutations now measured against --eval, not vibes)     │
└─────────────────────────────────────────────────────────────────┘
```

---

## V3 Schema Summary (additions only)

| New in V3 | Type | Default | Set by | Used by |
|---|---|---|---|---|
| `commit` | string | auto | `filter.py --log` (auto-stamp) | Stale Check (V3.3) |
| `codified_as` | string | null | Sleep Cycle Codify phase | Promotion logic (V3.2) |

## V3 New Files

| File | Introduced in | Purpose |
|---|---|---|
| `memory/quarantine.jsonl` | V1 Phase 1 (V3.1) | Malformed/rejected write attempts, reviewed at Sleep Cycle |
| `memory/HANDOFF.md` | V1 Phase 1 (V3.6) | Disposable working-memory tier for session state |
| `memory/eval/golden.jsonl` | Pre-Phase-4 (V3.5) | Hand-curated retrieval eval cases |

## Explicit Non-Goals

- **No embeddings / vector DBs / cloud memory services.** The corpus is 30–500 entries; FTS5 BM25 covers semantic-ish recall at this scale. Revisit only if the golden set (V3.5) shows lexical retrieval failing systematically.
- **No new dependencies.** Everything above is Python stdlib (`json`, `sqlite3`, `subprocess`) + git + the existing Vitest/CI setup.
- **No rewrites of V1/V2.** Every V3 item is additive; cross-cutting impact tables list the exact insertion points.
