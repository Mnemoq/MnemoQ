# Per-Prompt Learning Evaluation

A lightweight, real-time detection layer that evaluates every human or agent prompt to see if anything meets the criteria for a learning — without waiting for batch consolidation.

## The Concept in One Sentence

Instead of relying on agents to manually decide "should I log a learning?" or waiting for `--consolidate` to mine patterns in batch, a fast heuristic check runs after **every** prompt/response cycle to catch learnable moments as they happen.

---

## Why: The Problem This Solves

The current system has three gaps:

1. **Agent-dependent logging.** The GM workflow says "When to write a learning" and "When NOT to write a learning" — but this relies on the agent's judgment and context window. Agents forget, get distracted, or run low on tokens and skip logging. Valuable learnings are lost.

2. **Batch latency.** The `auto-learning-system-cb6d42` plan detects patterns (repeated fixes, retrieval failures, conflicts) but only during `--consolidate` or `--auto-learn`. A learning discovered in batch may be weeks old. The context is gone. The fix is already merged.

3. **No signal from human prompts.** When a human says "No, don't do it that way — use X instead," that correction is a high-value learning. But the engine never sees it. The agent might log it, might not. The human correction is the strongest learning signal in the entire interaction, and it's completely uncaptured.

**Per-prompt evaluation closes all three gaps**: it runs automatically, it runs in real-time, and it can see both human and agent messages.

---

## When: Trigger Model

| Aspect | Value |
|--------|-------|
| **Trigger** | After every human prompt, after every agent response, or both |
| **Frequency** | Every turn in the conversation |
| **Latency budget** | < 500ms (heuristic) or < 2s (lightweight LLM) |
| **Token budget** | 0 (pure heuristic) or < 200 tokens (minimal LLM call) |

The key insight: **most prompts will produce no learning.** The system is a gate, not a generator. It says "no" 95% of the time and "yes, this is worth logging" 5% of the time. The cost is paid on every prompt, but the value is in the 5%.

---

## What: What Constitutes a Learnable Moment

### Signals detectable from a single prompt/response cycle

**High-confidence signals (heuristic-detectable, zero tokens):**

- **Human correction** — human says "no", "don't", "actually", "instead", "wrong", "should be" in response to agent's output. This is the single strongest learning signal.
- **Error in agent response** — agent's code/output contains a traceback, error message, or exception. The fix (if the agent corrects it in a later turn) is a learning.
- **Revert/undo pattern** — agent or human reverts a previous change. "Undo that", "revert", "put it back". This is the per-prompt equivalent of the git revert detector.
- **Repeated question** — human asks the same question twice, meaning the agent's first answer was insufficient. The gap is a learning.
- **Explicit "remember this"** — human or agent says "remember", "note", "don't forget", "always do X". These are direct learning instructions that the current system relies on the agent to log manually.

**Medium-confidence signals (require lightweight analysis):**

- **Architectural decision** — agent chooses between options and explains why. "We'll use X because Y." The rationale is a learning.
- **Workaround applied** — agent works around a limitation. "We can't do X directly, so we'll do Y." The limitation + workaround is a learning.
- **Performance insight** — agent notes something about performance. "This is O(n²) but fine for small inputs." The trade-off is a learning.
- **Dependency constraint** — agent discovers a version/API constraint. "X requires Y >= 3.2." The constraint is a learning.

**Low-confidence signals (require LLM judgment):**

- **Implicit pattern** — the conversation reveals a pattern that isn't explicitly stated. "This is the third time we've dealt with X." The pattern is a learning but requires synthesis across turns.

### What the system receives as input

This is the critical design question. Options:

| Input | Token cost | Detection quality | Privacy |
|-------|-----------|-------------------|---------|
| **Full prompt + response text** | High (for LLM) / N/A (for heuristic) | Best — can detect anything | Exposes all conversation to engine |
| **Structured summary** (type, components, outcome, keywords) | Zero | Good — covers high/medium signals | Minimal exposure |
| **Metadata only** (prompt type, files touched, success/fail, latency) | Zero | Limited — only high-level patterns | No content exposure |

The **structured summary** is the sweet spot. The agent (or IDE) extracts:
- `prompt_type`: "human" | "agent"
- `outcome`: "success" | "error" | "correction" | "revert" | "question" | "decision"
- `components`: list of components mentioned
- `files_touched`: list of files modified
- `keywords`: 3-5 keywords extracted from the prompt
- `correction_target`: if correction, what was corrected
- `error_text`: if error, the error message (truncated to 200 chars)

This is ~100 bytes of data. Zero tokens for heuristics. The engine processes it in < 10ms.

---

## Where: Integration Point Options — Debate

### Option A: Agent Calls CLI (`--evaluate`)

**How it works:**
The agent's workflow (e.g., GM) includes a step after each prompt/response cycle:
```
python -m agent_memory.cli --evaluate --prompt-type human --outcome correction --components auth,session --files src/auth.py --keywords "redirect,session,bug"
```

**Pros:**
- **Zero infrastructure.** No server, no plugin, no framework changes. The CLI already exists.
- **Agent controls timing.** The agent can skip evaluation for trivial prompts (e.g., "thanks", "continue") to save latency.
- **Works with any agent.** Any agent that can run shell commands can use it. No IDE lock-in.
- **Consistent with existing architecture.** The engine is already CLI-first. `--evaluate` is just another mode like `--step` or `--log`.
- **Easy to test.** Integration tests via subprocess, same as existing test patterns.

**Cons:**
- **Process spawn latency.** Each call spawns a Python process (~200-500ms on Windows). For every-prompt evaluation, this adds up. Mitigation: the agent can batch-evaluate at the end of its turn rather than after each sub-step.
- **Agent must comply.** If the agent's workflow doesn't include the `--evaluate` step, it doesn't run. Same compliance problem as the current `--log` approach — but with lower friction since the agent doesn't have to compose a full learning entry.
- **No human-prompt visibility.** The agent only sees its own responses. Human prompts that contain corrections are only visible if the agent's framework passes them through. The agent would need to extract the human's message and pass it as `--prompt-type human`.
- **Config loading overhead.** Each CLI invocation loads `config.json` and builds `ctx`. This is ~50-100ms of redundant work per call. Mitigation: a `--cache-config` flag or a daemon mode.

**Verdict:** Most pragmatic. Lowest barrier to entry. The latency concern is real but manageable — the evaluation itself is < 10ms; the overhead is Python startup. For agents that already make CLI calls (like GM does with `--step` and `--log`), one more call is incremental.

---

### Option B: HTTP Endpoint (`POST /api/evaluate`)

**How it works:**
A long-running server process (`--serve`) receives a POST after each prompt:
```json
POST /api/evaluate
{
  "prompt_type": "human",
  "outcome": "correction",
  "components": ["auth", "session"],
  "files_touched": ["src/auth.py"],
  "keywords": ["redirect", "session", "bug"],
  "correction_target": "session handling"
}
```
Returns:
```json
{
  "should_log": true,
  "suggested_entry": { ... },
  "confidence": 0.85,
  "signal_type": "human_correction"
}
```

**Pros:**
- **Low latency.** No process spawn. The server is already running with config loaded. Response time: < 50ms.
- **IDE-callable.** The IDE can call the endpoint directly via HTTP without spawning a process. This enables Option D (IDE hook) to use this as the backend.
- **Stateful.** The server can maintain in-memory state across calls — e.g., tracking conversation flow, counting repeated questions, correlating corrections with subsequent fixes. This enables detection patterns that are impossible in stateless CLI calls.
- **Streaming.** Could use SSE/WebSocket for real-time evaluation as the prompt is being typed (predictive evaluation). Future possibility.
- **SDK integration.** `MemoryClient.evaluate(...)` is a natural SDK method. The async client could call it without blocking the agent's turn.

**Cons:**
- **Server must be running.** If the server isn't up, evaluation doesn't happen. This is a deployment dependency. For solo developers or CI environments, running a server is friction.
- **New endpoint surface.** Adds to the API surface area. Needs request/response models, tests, documentation. More code to maintain.
- **State management complexity.** If the server tracks conversation state, it needs session management, cleanup, and multi-project isolation. This is a significant complexity increase over the stateless CLI.
- **Security.** The server is already behind an API key, but per-prompt calls increase the attack surface. Rate limiting becomes important.

**Verdict:** Best latency and richest detection. But the "server must be running" requirement is a deployment burden. Ideal for team/enterprise setups; overkill for solo developers. Could be layered on top of Option A (CLI for solo, HTTP for server-backed).

---

### Option C: Hook in Agent Prompt (Workflow-Embedded)

**How it works:**
The agent's system prompt/workflow file includes instructions:
```
After each prompt/response cycle, evaluate whether this interaction contains a learnable moment.
Check for: human corrections, errors, reverts, explicit "remember" instructions, architectural decisions.
If found, log via: python -m agent_memory.cli --log-file <temp-json>
```

The agent itself performs the evaluation using its own LLM reasoning, then uses the existing `--log` infrastructure to write the learning.

**Pros:**
- **Zero new code.** No new CLI mode, no new endpoint, no new module. The agent's LLM is the evaluator. The existing `--log` pipeline handles validation, dedup, and storage.
- **Best detection quality.** The agent's LLM can understand nuance, context, and implicit patterns that heuristics can't. It sees the full conversation, not a structured summary.
- **No latency overhead.** The evaluation happens as part of the agent's normal reasoning. No extra process spawn or HTTP call.
- **Self-correcting.** The agent can adjust its evaluation criteria based on feedback (e.g., if too many low-value learnings are logged, the agent can raise its threshold).

**Cons:**
- **Token cost.** Every prompt now includes evaluation instructions in the system prompt (~200-300 tokens) plus the agent's reasoning (~100-200 tokens per evaluation). Over a long session, this is thousands of additional tokens. This directly contradicts the "low token" requirement.
- **Agent compliance.** Same problem as current `--log` — the agent might skip evaluation when busy, run low on context, or simply forget. The system prompt is a suggestion, not a guarantee.
- **No human-prompt evaluation.** The agent evaluates its own responses but can't evaluate the human's prompt independently. It sees the human's message, but its evaluation is biased by its own perspective.
- **Inconsistent.** Different agents (GM, code-reviewer, test-writer) would need different evaluation instructions. The quality of evaluation depends on the agent's LLM model and prompt engineering.
- **Prompt pollution.** Adding evaluation instructions to every agent's system prompt increases prompt complexity and may interfere with the agent's primary task. The agent is now doing two jobs: coding and meta-evaluation.
- **No separation of concerns.** The agent is both the actor and the evaluator. It's asking itself "was what I just did a learning?" — which is a conflict of interest. The agent may over-log its own actions or under-log its own mistakes.

**Verdict:** Highest detection quality but highest token cost and least reliable. The "low token" requirement in the user's request effectively disqualifies this as the primary mechanism. However, it could serve as a **complement** — the heuristic system catches the obvious signals, and the agent's own judgment catches the subtle ones.

---

### Option D: IDE/Plugin Hook

**How it works:**
The IDE (Windsurf, Cursor, etc.) intercepts every human message and agent response, extracts a structured summary, and calls the engine (via CLI or HTTP) automatically. The agent is unaware that evaluation is happening.

**Pros:**
- **Truly automatic.** No agent compliance required. The evaluation happens whether the agent wants it or not. This is the only option that guarantees coverage of every prompt.
- **Sees both sides.** The IDE sees both the human's message and the agent's response. It can detect human corrections, agent errors, and interaction patterns that no other option can.
- **Zero agent token cost.** The evaluation is entirely outside the agent's context. The agent's prompt is not polluted. The agent doesn't even know it's happening.
- **Consistent.** Every agent, every workflow, every model gets the same evaluation. No per-agent prompt engineering needed.
- **Centralized.** The IDE can maintain conversation state (turn count, correction history, repeated questions) across the entire session, enabling temporal pattern detection.

**Cons:**
- **IDE-specific.** Each IDE needs its own integration. Windsurf hooks are different from Cursor hooks are different from VS Code extensions. This is N integrations, not one.
- **No current hook point.** Windsurf/Cursor don't currently have a "post-prompt" hook that can call external tools. This would require framework changes by the IDE vendor. We can't build this today.
- **Privacy concerns.** The IDE would be sending conversation metadata to the engine. If the engine is remote (HTTP), this is a data flow concern. If local (CLI), it's less concerning but still requires the IDE to parse and structure conversation data.
- **Extraction quality.** The IDE would need to extract `outcome`, `components`, `keywords` from raw conversation text. This is either a heuristic (limited) or an LLM call (tokens). The IDE doesn't naturally have structured metadata about what happened — it has raw text.
- **Fragile.** IDE updates could break the integration. The engine has no control over the IDE's hook API.

**Verdict:** The ideal end state — but not buildable today without IDE vendor support. This is the "north star" architecture. The pragmatic path is to build Option A (CLI) now, design the API surface so Option B (HTTP) is a natural extension, and propose Option D to IDE vendors as a future integration.

---

## How: The Evaluation Engine

Regardless of integration point, the core evaluation logic is the same. Here's what it does:

### Step 1: Signal Extraction (zero tokens)

Given a structured summary, run fast pattern matching:

```python
def evaluate_prompt(summary: dict, ctx: dict) -> dict | None:
    """Evaluate a prompt summary for learnable moments.
    
    Returns a suggested learning entry dict, or None if no learning is warranted.
    """
    signals = []
    
    # Human correction detection
    if summary["prompt_type"] == "human" and summary["outcome"] == "correction":
        signals.append(("human_correction", 0.9, _build_correction_entry(summary)))
    
    # Error detection
    if summary["outcome"] == "error" and summary.get("error_text"):
        signals.append(("agent_error", 0.7, _build_error_entry(summary)))
    
    # Revert detection
    if summary["outcome"] == "revert":
        signals.append(("revert", 0.85, _build_revert_entry(summary)))
    
    # Explicit "remember" instruction
    if "remember" in summary.get("keywords", []):
        signals.append(("explicit_remember", 0.95, _build_remember_entry(summary)))
    
    # ... more signals
    
    if not signals:
        return None
    
    # Return highest-confidence signal
    signals.sort(key=lambda s: s[1], reverse=True)
    return signals[0]
```

### Step 2: Dedup Check (fast, in-memory)

Before suggesting a learning, check if a similar learning already exists. This is the same `find_best_match` / `find_semantic_duplicate` logic from `log_core()`, but run as a pre-check:

```python
def evaluate_and_dedup(summary, entries, ctx):
    result = evaluate_prompt(summary, ctx)
    if result is None:
        return {"should_log": False}
    
    candidate = result[2]
    similarity, best_match = find_best_match(candidate, entries)
    if similarity >= 0.7:
        return {
            "should_log": False,
            "duplicate_of": best_match["ts"],
            "message": "Similar learning already exists"
        }
    
    return {
        "should_log": True,
        "suggested_entry": candidate,
        "confidence": result[1],
        "signal_type": result[0]
    }
```

### Step 3: Action

Two modes:

- **Auto-log mode:** If `should_log` is True and confidence >= threshold, log directly via `log_core()`. No agent involvement.
- **Suggest mode:** Return the suggestion to the caller. The agent (or IDE) decides whether to log. This is safer but reintroduces the compliance problem.

The recommended default is **suggest mode** with a configurable threshold for auto-log. High-confidence signals (human correction, explicit "remember") auto-log. Medium-confidence signals (architectural decision, workaround) suggest only.

---

## Relationship to the Existing Auto-Learn Plan

The `auto-learning-system-cb6d42` plan and this per-prompt system are **complementary, not competing**:

| Dimension | Auto-Learn (cb6d42) | Per-Prompt Evaluation |
|-----------|---------------------|----------------------|
| **Trigger** | Batch (`--consolidate`, `--auto-learn`) | Real-time (every prompt) |
| **Data sources** | Git history, metrics.jsonl, corpus analysis | Structured prompt summary |
| **Detection type** | Statistical patterns (repeated fixes, over-injection, conflicts) | Single-event signals (corrections, errors, reverts, explicit instructions) |
| **Latency** | Seconds to minutes | < 500ms |
| **Token cost** | Zero (heuristic) | Zero (heuristic) or < 200 (lightweight LLM) |
| **Coverage** | Misses single-event learnings | Misses statistical patterns |
| **False positive risk** | Low (threshold-based) | Medium (single-event heuristics are noisier) |

**Together:** Per-prompt catches the "human said don't do X" learning in real-time. Auto-learn catches the "X has been fixed 4 times in 20 commits" pattern in batch. Neither subsumes the other.

**Implementation order:** Per-prompt is simpler and higher-value-per-line-of-code. It could ship first, with auto-learn following as the batch complement.

---

## Open Questions

1. **Structured summary extraction:** Who extracts the summary from raw conversation text? The agent? The IDE? A lightweight LLM call? This is the hardest engineering problem and determines the token cost.

2. **Auto-log vs. suggest:** Should the system log learnings automatically, or just suggest them? Auto-log risks noise; suggest risks the same compliance problem as today.

3. **Statefulness:** Does the evaluation need conversation history (e.g., "this is the 3rd correction about auth")? If yes, the CLI approach can't do it without a state file. The HTTP server can.

4. **Threshold tuning:** How do we calibrate the heuristic thresholds to avoid flooding the corpus with low-value learnings? The existing `auto_learn_max_per_run` cap doesn't apply to per-prompt (which runs once per prompt, not once per batch).

5. **Human prompt access:** How does the engine see human prompts? Currently it only sees what the agent passes via `--step` or `--log`. Getting human-side text requires either IDE integration or the agent forwarding it.

6. **Multi-agent sessions:** In a session with multiple agents (GM + code-reviewer + test-writer), who evaluates? Each agent evaluates its own turns? One evaluator for all?

---

## Recommended Architecture

**Phase 1 (ship now):** Option A (CLI `--evaluate`), heuristic-only, suggest mode.

- New CLI mode: `--evaluate --prompt-type <type> --outcome <outcome> --components <list> --files <list> --keywords <list>`
- Pure heuristics, zero tokens, < 10ms evaluation time.
- Returns a suggestion (JSON to stdout). The agent decides whether to `--log` it.
- No new module — lives in `handlers.py` as `evaluate_core()`, parallel to `log_core()`.
- Config: `evaluate_enabled`, `evaluate_auto_log_threshold` (confidence threshold for auto-log, default off).

**Phase 2 (ship next):** Option B (HTTP endpoint), same logic via `POST /api/evaluate`.

- Same `evaluate_core()` function, different transport.
- Enables IDE plugins and SDK integration.
- Optional: lightweight LLM evaluation mode for medium-confidence signals.

**Phase 3 (future):** Option D (IDE hook), using Phase 2's HTTP endpoint as backend.

- Requires IDE vendor support.
- The engine is ready; the IDE integration is the bottleneck.

**Option C (agent prompt hook)** is not recommended as a primary mechanism due to token cost, but the existing GM workflow's "When to write a learning" section should remain as a complementary human-judgment layer.

---

## Summary Table: Options at a Glance

| Criterion | A: CLI | B: HTTP | C: Prompt Hook | D: IDE Hook |
|-----------|--------|---------|----------------|-------------|
| **Latency** | ~300ms (process spawn) | ~50ms | 0ms (inline) | ~50ms (if HTTP backend) |
| **Token cost** | 0 | 0 | 200-500/turn | 0 (or IDE-side LLM) |
| **Agent compliance required** | Yes | Yes (or IDE calls) | Yes | No |
| **Sees human prompts** | Only if agent forwards | Only if caller forwards | Yes (agent sees them) | Yes (IDE sees them) |
| **Buildable today** | Yes | Yes | Yes | No (needs IDE vendor) |
| **New code** | Small (CLI mode + handler) | Medium (endpoint + models) | None (prompt text only) | Large (per-IDE plugin) |
| **Detection quality** | Good (heuristic) | Good (heuristic) + optional LLM | Best (full LLM reasoning) | Depends on extraction |
| **Statefulness** | No (stateless) | Yes (server state) | Yes (agent memory) | Yes (IDE state) |
| **Privacy** | Good (local) | Good (local server) | Best (no data leaves agent) | Medium (data to engine) |
