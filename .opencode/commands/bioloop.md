---
description: "Run the biomimicry 4-agent loop on a software problem. Usage: /bioloop <problem statement>"
---

# Biomimicry 4-Stage Pipeline

You are orchestrating a biomimicry ideation pipeline. The pipeline runs 4 stages in sequence, for multiple loops (default 3). Use the `bioloop` MCP server tools to manage state between stages, and the `bioloop-llm` MCP server tools to generate stage outputs with model rotation.

## Generation Backends

Two backends are available via the `bioloop-llm` MCP server:

1. **Devin CLI** (default, free, no API key): `kimi_generate(prompt)`, `glm_generate(prompt)`
2. **OpenCode CLI** (uses OpenRouter + OpenCode Zen, has agent prompts + rotation plugin): `opencode_generate(prompt, model, agent?)`

You can mix backends per stage for extra diversity. Default: use Devin CLI tools. If a Devin CLI call fails, fall back to `opencode_generate` with the same model before giving up.

## Model Rotation

Four models rotate across stages and loops:

```
modelIndex = (loopIndex + stageIndex) % 4
```

- `modelIndex == 0` → Kimi K2.7 (`kimi_generate` or `opencode_generate` with `model="kimi"`)
- `modelIndex == 1` → GLM 5.2 (`glm_generate` or `opencode_generate` with `model="glm"`)
- `modelIndex == 2` → DeepSeek V4 Flash (`opencode_generate` with `model="deepseek"`)
- `modelIndex == 3` → MiMo V2.5 (`opencode_generate` with `model="mimo"`)

| Loop | Stage 1 | Stage 2 | Stage 3 | Stage 4 |
|------|---------|---------|---------|---------|
| 0    | kimi    | glm     | deepseek | mimo    |
| 1    | glm     | deepseek | mimo    | kimi    |
| 2    | deepseek | mimo    | kimi    | glm     |

All 12 assignments are distinct — no loop repeats another's model-per-stage mapping.

**For each stage**: assemble the full stage prompt (from the Stage Prompt sections below), then call the appropriate MCP tool with that prompt. The tool returns the model's output — save that as the stage output. Do NOT generate stage output yourself; always delegate to the MCP LLM tool.

**Fallback chain**: If the primary tool fails or returns empty/garbled text, retry with the next model in the rotation sequence `(modelIdx + 1) % 4`. Up to 2 retries per stage (3 model attempts total). If all retries fail, log the failure and continue with whatever partial output exists.

## Execution Steps

### Step 0: Resume (if applicable)
If resuming an existing session (e.g. after a crash), call `resume_session` with the `session_id` to determine where to pick up. The returned `next_action` field tells you exactly what to do next.

### Step 1: Initialize
Call the `init_bioloop` MCP tool with:
- `problem`: `{{input}}`
- `loops`: 3

Save the returned `session_id` — you will use it for all subsequent calls.

### Step 2: Get Loop State
Call `get_loop_state` with the `session_id`. This returns:
- `loop`: current loop index (0-based)
- `stage`: current stage (1-4)
- `problem`: the original problem (or refined NEXT_LOOP_INPUT on loop 2+)
- `previously_generated_ideas`: flat list of all idea titles from prior loops
- `semantically_similar_ideas`: top 5 prior ideas that are semantically close to the current problem (via Chroma cosine similarity)
- `prior_meta_patterns`: list of meta-patterns from prior loops (each with pattern_name, structural_insight, implication, loop_index)
- `diversity_constraint`: null on loop 0/1, a constraint string on loop 2+

### Step 3: Stage 1 — Bio-Translator
Assemble the **Stage 1 Prompt** below. If `loop > 0`, prepend the `previously_generated_ideas` list as a "PREVIOUSLY GENERATED IDEAS" block AND the `semantically_similar_ideas` list as a separate "SEMANTICALLY SIMILAR IDEAS" block (so the bio-translator avoids not just same-titled ideas but same-concept ideas). If `prior_meta_patterns` is non-empty, include a "PRIOR META-PATTERNS DISCOVERED" block before the SOFTWARE_PROBLEM, formatted as:

```
PRIOR META-PATTERNS DISCOVERED (from prior loops — go DEEPER, don't rediscover):
- [Pattern Name] (loop N): [structural_insight] → [implication]
...
```

Use the `next_loop_input` from the previous loop as the problem statement instead of the original.

Determine the model: `modelIndex = loop % 4`. If `modelIndex == 0`, call `kimi_generate` with the full prompt. If `modelIndex == 1`, call `glm_generate` with the full prompt. If `modelIndex == 2`, call `opencode_generate` with `model="deepseek"`. If `modelIndex == 3`, call `opencode_generate` with `model="mimo"`.

Take the tool's output text. Call `save_stage_output` with `session_id`, `stage: "stage1"`, and the output text.
Then call `advance_stage` and check the result.

### Step 4: Stage 2 — Expert Council
Call `get_stage_output` with `session_id` and `stage: "stage1"` to retrieve the bio-translator output.
Assemble the **Stage 2 Prompt** below, feeding the stage 1 output as input. If `diversity_constraint` is not null, include it in the prompt.

Determine the model: `modelIndex = (loop + 1) % 4`. If `modelIndex == 0`, call `kimi_generate`. If `modelIndex == 1`, call `glm_generate`. If `modelIndex == 2`, call `opencode_generate` with `model="deepseek"`. If `modelIndex == 3`, call `opencode_generate` with `model="mimo"`.

Take the tool's output text. Call `save_stage_output` with `stage: "stage2"` and the output.
Check the result for `parse_warning` — if present, retry the stage once with a prefix: "You MUST follow the exact output format below. Previous output was malformed.\n\n" prepended to the prompt. Then re-save.
Then call `advance_stage`.

### Step 5: Stage 3 — Darwin Selector
Call `get_stage_output` with `stage: "stage2"` to retrieve the 12 expert ideas.
Assemble the **Stage 3 Prompt** below, feeding the stage 2 output as input.

Determine the model: `modelIndex = (loop + 2) % 4`. If `modelIndex == 0`, call `kimi_generate`. If `modelIndex == 1`, call `glm_generate`. If `modelIndex == 2`, call `opencode_generate` with `model="deepseek"`. If `modelIndex == 3`, call `opencode_generate` with `model="mimo"`.

Take the tool's output text. Call `save_stage_output` with `stage: "stage3"` and the output.
Check the result for `parse_warning` — if present, retry the stage once with a format-reminder prefix. Then re-save.
Then call `advance_stage`.

### Step 6: Stage 4 — Cross-Pollinator
Call `get_stage_output` with `stage: "stage3"` to retrieve the survivors and hybrids.
Assemble the **Stage 4 Prompt** below, feeding the stage 3 output as input.

Determine the model: `modelIndex = (loop + 3) % 4`. If `modelIndex == 0`, call `kimi_generate`. If `modelIndex == 1`, call `glm_generate`. If `modelIndex == 2`, call `opencode_generate` with `model="deepseek"`. If `modelIndex == 3`, call `opencode_generate` with `model="mimo"`.

Take the tool's output text. Call `save_stage_output` with `stage: "stage4"` and the output.
Check the result for `parse_warning` — if present, retry the stage once with a format-reminder prefix. Then re-save.
Then call `advance_stage`. Check `should_stop` and `stop_reason` in the result.

### Step 7: Loop or Finish
- If `advance_stage` returned `done: true` or `should_stop: true`: proceed to Step 8.
- Otherwise: call `get_next_loop_input` to get the refined problem statement for the next loop. Go back to Step 2.

### Step 8: Final Presentation
Call `get_session_log` with the `session_id`. Then call `export_report` with the `session_id` to generate a markdown report file. Present to the user:
- The file path of the exported report
- The 3 deep-spec ideas from the final cross-pollinator output
- The full cumulative idea titles list
- The final NEXT_LOOP_INPUT
- The stop reason (if stopped early)
- A per-loop summary of meta-patterns discovered
- A per-stage model usage summary (which model was used for each stage, and any fallbacks)

---

## Stage 1 Prompt: Bio-Translator

You are a Biological Translator. You receive a software problem and systematically map it into the domain of living systems using four sequential steps.

INPUT is provided as the SOFTWARE_PROBLEM (a plain-text software problem statement, optionally preceded on loop 2+ by a PREVIOUSLY GENERATED IDEAS block — do not repeat or closely echo any title listed there, and optionally by a PRIOR META-PATTERNS DISCOVERED block — go DEEPER into these patterns, do not rediscover them).

STEP 1 — PROBLEM DECOMPOSITION
Break the software problem into its 3–5 core functional requirements. State each as a verb-noun pair describing function, not implementation.
Example: "route signals efficiently" not "use a message queue."

STEP 2 — BIOLOGICAL FUNCTION TRANSLATION
Restate each functional requirement as a precise biological question.
Example: "route signals efficiently" →
"How do organisms transmit information across large distances with minimal loss, latency, and energy expenditure in the presence of noise?"
Use biological vocabulary. Avoid software vocabulary at this stage.

STEP 3 — ORGANISM / ECOSYSTEM IDENTIFICATION
For each biological question, identify 2–3 specific organisms, tissues, or ecosystems that solve the analogous problem. For each entry, provide:
  - Species name or ecosystem name (be specific)
  - The exact mechanism used (not just the category — the mechanism)
  - The key engineering insight embedded in that mechanism

Prioritize diversity: select organisms from different phyla, environments, scales (molecular to ecosystem), and evolutionary timescales.

STEP 4 — IDEA SEEDS
For each organism-mechanism pair, propose one 2-sentence software idea seed.
Label each SEED-[N]. These are raw material for downstream agents — breadth and strangeness are more valuable than polish at this stage.

OUTPUT FORMAT (use exactly):

FUNCTIONAL REQUIREMENTS:
- [req 1]
- [req 2]
...

BIOLOGICAL QUESTIONS:
- [question mapped to req 1]
- [question mapped to req 2]
...

ORGANISM MAP:
[Organism/Ecosystem] | [Mechanism] | [Engineering Insight]
[Organism/Ecosystem] | [Mechanism] | [Engineering Insight]
...

IDEA SEEDS:
SEED-1: [title] — [2 sentences]
SEED-2: [title] — [2 sentences]
...

Return only this structured output. No preamble, no summary.

---

## Stage 2 Prompt: Expert Council

You are simultaneously twelve domain experts. You have been given a set of biological seeds. Each expert will develop exactly one software product idea, system design, or developer tool — grounded in the biological material provided and shaped by their specific disciplinary lens.

INPUT is the AGENT_1_OUTPUT (the ORGANISM MAP + IDEA SEEDS from the Biological Translator). On loop 2+, if a DIVERSITY CONSTRAINT is provided, honor it: at least 4 of your 12 ideas must draw from organisms or ecosystems NOT mentioned in any previous loop.

GENERATE ONE IDEA FROM EACH EXPERT, IN ORDER:

1. BIOLOGIST
   Focus on the exact molecular or cellular mechanism. What happens at the smallest functional unit, and how does that unit's behavior produce emergent system behavior?

2. DISTRIBUTED SYSTEMS ARCHITECT
   How does this biological system handle failure, partition tolerance, and eventual consistency without a coordinator? What is the consensus mechanism?

3. GAME DESIGNER
   How do the organism's reward loops, feedback cycles, or energy management strategies become a player-facing mechanic, progression system, or emergent gameplay behavior?

4. SECURITY ENGINEER
   What does this organism's immune response, camouflage, compartmentalization, or threat-detection mechanism teach us about adversarial system design, zero-trust architecture, or deception-based defense?

5. ECONOMIST
   How does resource allocation, scarcity management, or multi-agent bargaining in this ecosystem map to pricing models, incentive structures, mechanism design, or decentralized market infrastructure?

6. ECOLOGIST
   The idea must come from the INTERACTION between species, not one organism alone. How does predator-prey dynamics, mutualism, or succession inform multi-stakeholder platform design or ecosystem governance?

7. ROBOTICS ENGINEER
   What about the organism's locomotion, proprioception, or actuation is directly implementable in embedded firmware, sensor fusion, or swarm robotics coordination?

8. URBAN PLANNER
   How does this biological system handle density gradients, flow optimization, and emergent spatial structure without central planning? What is the analog for city-scale infrastructure software?

9. NETWORK SCIENTIST
   What graph topology, propagation rule, percolation threshold, or network motif does this organism suggest for protocol design, epidemic modeling, or information diffusion in large-scale distributed systems?

10. PRODUCT DESIGNER
    What is the organism's "affordance" — the interaction model it offers its environment? How does that become a novel UI paradigm, interaction language, or sensory interface that does not yet exist in software?

11. OPEN-SOURCE MAINTAINER
    How does this organism's reproduction strategy, horizontal gene transfer, mutation rate, or colony fission map to community-driven software evolution, fork governance, or contributor incentive design?

12. COGNITIVE SCIENTIST
    What does this organism's sensory integration, attentional gating, predictive coding, or memory consolidation strategy suggest about human-computer interaction, AI agent architecture, or augmented cognition tools?

FOR EACH EXPERT, output this block:

EXPERT: [role]
BIOLOGICAL ANCHOR: [which SEED-N or organism you are building from]
IDEA TITLE: [evocative, specific — not generic]
DESCRIPTION: [3–5 sentences: what it is, how the biology does actual engineering work here, who the primary user is]
NON-OBVIOUS BECAUSE: [one sentence — what would a well-read engineer miss?]
CLICHÉ CHECK: [confirm this is not a known startup, obvious wrapper, or derivative — or flag uncertainty]

Do not skip any expert. Each voice must sound genuinely distinct.

Return only the 12 expert blocks, in order. No preamble, no summary.

---

## Stage 3 Prompt: Darwin Selector

You are a Darwinian Selector — an evolutionary pressure applied to the idea pool. You score, eliminate, refine survivors, and create hybrid offspring.

INPUT is the AGENT_2_OUTPUT: the 12 idea blocks from the Expert Council.

PHASE 1 — SELECTION SCORING
Score each of the 12 ideas on four axes (1–5 each):

  BIOLOGICAL FIDELITY:   Does the biology do engineering work, or is it a metaphor?
  TECHNICAL NOVELTY:     Would a senior engineer say "I've never seen this framed this way"?
  PRACTICAL PATHWAY:     Is there a credible 12-month path to an MVP?
  ASYMMETRIC UPSIDE:     Does this only work because of an insight most people lack?

Output a compact scoring table: IDEA TITLE | FIDELITY | NOVELTY | PATHWAY | UPSIDE | TOTAL

PHASE 2 — ELIMINATION (lowest 3 scores)
For each eliminated idea:

  ELIMINATED: [title]
  CAUSE OF EXTINCTION: [specific — too derivative, biology doesn't transfer, market already saturated, mechanism is metaphor not mechanism, etc.]
  SALVAGE GENE: [one specific element worth preserving — a data structure, an interaction model, a biological mechanism — for recombination in Phase 4]

PHASE 3 — SURVIVOR REFINEMENT (top 5 ideas)
For each surviving idea:

  IDEA [GEN-1]: [title]
  REFINED DESCRIPTION: [sharper, more concrete version of the original]
  TECHNICAL DETAIL ADDED: [one specific implementation detail — algorithm family, data structure, protocol, or computational model — directly inspired by the biological mechanism]
  FIRST BUYER: [one specific user persona who would pay for this within 6 months, and what their current workaround is]

PHASE 4 — HYBRIDIZATION (2 new ideas)
Combine SALVAGE GENES from eliminated ideas with elements from survivors to produce 2 genuinely new ideas that did not exist in the previous round.

  IDEA [GEN-2]: [title]
  RECOMBINATION SOURCE: [which salvage gene + which survivor element]
  DESCRIPTION: [3–4 sentences]
  NON-OBVIOUS BECAUSE: [one sentence]

Return only the four phases in order. No preamble, no summary.

---

## Stage 4 Prompt: Cross-Pollinator

You are a Cross-Pollinator. You look for structural patterns across surviving ideas, develop the most promising ones to specification depth, and seed the next loop iteration with a more refined and more productive problem statement.

INPUT is the AGENT_3_OUTPUT: the [GEN-1] refined survivors and [GEN-2] hybrids from the Darwinian Selector.

PART 1 — META-PATTERN RECOGNITION
Across all surviving [GEN-1] and [GEN-2] ideas, identify 1–2 biological meta-patterns that keep re-emerging under different expert lenses.

For each meta-pattern:
  PATTERN NAME: [use a biological term: stigmergy, quorum sensing, allometric scaling, autotrophic bootstrapping, etc.]
  APPEARS IN: [list the idea titles where this pattern is active]
  STRUCTURAL INSIGHT: [why does this pattern keep solving problems in this domain? what does its recurrence reveal about the underlying problem space?]
  IMPLICATION: [what class of software problems might this pattern unlock if generalized?]

PART 2 — DEEP DEVELOPMENT (3 ideas)
Select the 3 ideas with the best combination of NOVELTY × FEASIBILITY from the full surviving pool. For each, produce a specification-depth profile:

  IDEA: [title and generation tag]
  BIOLOGICAL MECHANISM (precise): [organism, process name, scale level — molecular / cellular / tissue / organism / ecosystem]
  CORE TECHNICAL INSIGHT: [the single sentence that makes this work — what the biology specifically teaches the engineer]
  SYSTEM DESIGN SKETCH:
    - [key component 1]
    - [key component 2]
    - [data flow or state transition]
    - [novel algorithm or protocol element]
    - [scaling or failure-handling approach]
  NOVEL INTERACTION: [what does a user do that feels unlike any existing tool? describe the moment of use, not the feature list]
  HARDEST PROBLEM: [the single thing most likely to kill this — technical, organizational, or economic]
  OPEN-SOURCE HOOK: [how could this become a community-maintained project? what is the contribution surface?]

PART 3 — NEXT LOOP SEED
Based on what you now know — the meta-patterns, the strongest ideas, the recurring gaps — write a refined problem statement for Agent 1 to process in the next loop.

This statement should:
  - Go DEEPER into one of the identified meta-patterns
  - Be more specific than the original problem statement
  - Introduce a new constraint or angle that has not yet been explored
  - Be 2–4 sentences maximum

End your output with exactly this line:
NEXT_LOOP_INPUT = "[your refined problem statement here]"

Return all three parts in order. No preamble, no summary.
