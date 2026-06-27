# Per-Prompt Evaluation — Implementation Plan

Build a heuristic-only `evaluate_core(summary, paths, ctx)` in a new `engine/evaluate.py` that scores each turn's structured summary, **auto-logs high-confidence learnings** (suggests the rest) via a config threshold, exposed primarily as the **MCP tool `evaluate_prompt`** with a thin CLI `--evaluate` wrapper — mirroring how `auto-learn` shipped, deferring HTTP / LLM / stateful detection.

> The scored decisions below are the locked rationale; the phased plan follows under **Implementation Plan**.

> Source design doc: `per-prompt-evaluation-fef263.md`. Grounding: README ("MCP is the primary integration path for agents"), the project philosophy in `auto-learning-system-cb6d42.md` ("compliance is an optimization, not a dependency"), the `*_core()` + 4-wrapper architecture, and `log_core()`'s existing dedup safety net (`handlers.py`).

## TL;DR — Locked Picks

- **Surface:** MCP tool `evaluate_prompt` (primary). Build `evaluate_core()` + thin CLI `--evaluate` alongside (test/scripting). HTTP + IDE hook deferred.
- **Action:** Auto-log high-confidence signals; suggest the rest. Delivered via `evaluate_auto_log_threshold` (default ~0.9) so the *default* auto-logs only near-unambiguous signals.
- **Depth:** Heuristic feature-complete (core + config + tests + docs), mirroring how `auto-learn` shipped. No LLM, no server state in v1.
- **Input:** Structured summary (~100 bytes), extracted by the agent, passed as tool args. Zero engine tokens.

---

## Decision 1 — Trigger Surface

| Rank | Option | Score | Verdict |
|------|--------|-------|---------|
| 1 | **MCP tool** (`evaluate_prompt`) | **8.5** | Recommended primary |
| 2 | HTTP `POST /api/evaluate` | 7.0 | Fast-follow: stateful detection + IDE-hook backend |
| 3 | CLI `--evaluate` | 6.0 | Weak *runtime* trigger; build anyway as core + test harness |
| 4 | IDE hook | 5.0 (10 as north-star) | Defer — not buildable without IDE-vendor support |

**Rationale.** Per-prompt eval runs every turn. CLI's ~300ms process spawn **plus cold-loading the embedding model** (dedup calls `find_semantic_duplicate`) disqualifies it as the hot path. The MCP server is **long-running over stdio** — config + model load once and stay warm — and is the README's stated primary agent path, so MCP-wired clients call it with zero new plumbing. The `auto-learn` "no MCP" decision was for a **batch** op an agent never calls mid-loop; per-prompt is the **opposite cadence**, so that precedent does not transfer. HTTP is the correct home for later stateful detection and is the backend the IDE hook will eventually use.

## Decision 2 — Action Mode

| Rank | Option | Score | Verdict |
|------|--------|-------|---------|
| 1 | **Auto-log high-confidence, suggest the rest** | **9.0** | Recommended (via threshold config) |
| 2 | Configurable, default suggest-only | 6.5 | Right knob, wrong default |
| 3 | Auto-log everything above threshold | 5.5 | Eventual destination, premature now |
| 4 | Suggest-only | 3.0 | Reintroduces the compliance gap the feature exists to close |

**Rationale.** Suggest-only returns the decision to the agent — the exact gap the feature targets — and contradicts the project's "compliance-independent" philosophy. Blanket auto-log is risky: single-event heuristics are noisy, and for a retrieval system **corpus quality > coverage** (noise poisons future retrieval). Auto-log only near-unambiguous, highest-value signals (human correction, explicit "remember"); suggest the noisier medium-confidence ones. Two safety nets make this sound: (1) `log_core()` already **merges/dedups** (cosine ≥ 0.85 merge, similarity ≥ 0.7 increments `access_count`) so repeats reinforce one entry; (2) `source_agent:"system"` is already a valid provenance value. Implement as option 4's knob with option 1's default.

## Decision 3 — v1 Depth

| Rank | Option | Score | Verdict |
|------|--------|-------|---------|
| 1 | **Heuristic feature-complete** | **9.0** | Recommended |
| 2 | Thin slice | 7.0 | Good spike, below the project's shipping norm |
| 3 | Heuristic + optional LLM | 4.5 | Premature (adds the token cost the plan avoids) |
| 4 | Full plan (all phases) | 3.0 | Premature-abstraction trap |

**Rationale.** The unproven premise — *heuristics catch the valuable ~5% without flooding the corpus* — must be validated before investing in LLM tokens or stateful-server complexity. Feature-complete-heuristic mirrors how `auto-learn` shipped and is dogfoodable now. Config thresholds are **not optional polish** — they are the control surface for the #1 risk (noise), so a config-less "thin slice" can't be calibrated. LLM only helps the *suggest* tier (low marginal value); stateful detection needs session management/cleanup/isolation (big jump, violates "no premature abstractions").

---

## Derived Answers to the Doc's 6 Open Questions

| # | Open question | Recommended answer |
|---|---------------|--------------------|
| 1 | Who extracts the summary? | Agent builds it, passes as MCP tool args (near-zero marginal cost) |
| 2 | Auto-log vs suggest | Threshold-gated (Decision 2) |
| 3 | Statefulness | v1 stateless; cross-turn memory comes free from `log_core` dedup. Stateful → HTTP phase |
| 4 | Threshold tuning | `evaluate_enabled`, `evaluate_auto_log_threshold`, `evaluate_max_per_turn` (trust-boundary safety cap) |
| 5 | Human-prompt access | Agent forwards corrections as `prompt_type=human`; true auto-capture awaits IDE hook (document limit) |
| 6 | Multi-agent | Each agent self-evaluates its turns; `source_agent:"system"` stamps provenance |

---

## Implementation Plan

`evaluate_core()` lives in a **new `engine/evaluate.py`** (mirrors `auto_learn.py`: pure detectors + one orchestrator), *not* in `handlers.py`. Every auto-logged entry routes through the existing `log_core()` — reusing validation, dedup/merge, conflict detection, provenance, metrics, and embedding — and reuses `_derive_domain()` from `auto_learn.py`. `validate_entry()` gates auto-log vs suggest.

### Phase 1 — Schema, config, wiring

- **`engine/constants.py`** — add `EVALUATE_ENABLED = True`, `EVALUATE_AUTO_LOG_THRESHOLD = 0.9`, `EVALUATE_MAX_PER_TURN = 3`; add all three to `DEFAULTS`.
- **`templates/config.json`** — add to `tuning`: `evaluate_enabled`, `evaluate_auto_log_threshold`, `evaluate_max_per_turn`.
- **`cli.py` `load_config()`** — add `evaluate_enabled` to `bool_params`; `evaluate_auto_log_threshold` to `float_params` (0.0–1.0 inclusive); `evaluate_max_per_turn` to `int_params` (min 1).

### Phase 2 — `engine/evaluate.py` (NEW)

**Input — structured summary** (agent-built tool args; fields optional, detectors tolerate gaps):

| Field | Use |
|-------|-----|
| `step` (int) | entry step (agent knows it; avoids a corpus read) |
| `prompt_type` (`human`/`agent`) | gates the human-correction detector |
| `outcome` (str) | `correction`/`preference`/`bug_fixed`/`decision`/`workaround`/`none` |
| `text` (str) | salient gist — keyword matching + `reason` |
| `corrected_action` / `rejected_action` (str) | build the `ALWAYS`/`NEVER` clauses |
| `components`, `files_touched` (list[str]) | entry targeting + `_derive_domain` |
| `error_text` (str, opt) | bug-fixed detector |

**Pure detectors** `(summary, ctx) -> (confidence, candidate) | None`, calibrated so the default `0.9` threshold auto-logs only the top two:

| Detector | Conf | Type | Tier |
|----------|------|------|------|
| `detect_human_correction` | 0.95 | architectural_pattern / bug_fix | auto-log |
| `detect_explicit_remember` | 0.92 | architectural_pattern | auto-log |
| `detect_bug_fixed` | 0.70 | bug_fix (major) | suggest |
| `detect_decision` | 0.60 | architectural_pattern | suggest |
| `detect_workaround` | 0.55 | bug_fix (`debt_level` set) | suggest |

- **`_build_candidate(...)`** — stamps `source_agent="system"`, `resolved=False`; ensures `trigger` starts with `When` and `action` carries `ALWAYS`/`NEVER`; sets `domain=_derive_domain(files[0])`; requires non-empty `components`/`files_touched`.
- **`evaluate_core(summary, paths, ctx)`**:
  1. Early-return `{"disabled": True}` if `not ctx.get("evaluate_enabled", True)`.
  2. Guard that `summary` is a dict (trust-boundary validation).
  3. Run detectors → collect `(confidence, candidate)`; sort desc; cap at `evaluate_max_per_turn`.
  4. For each: `errors = validate_entry(candidate, ctx)`.
     - invalid → **downgrade to suggestion** (never auto-log junk; record `errors`).
     - valid & `confidence >= evaluate_auto_log_threshold` → `log_core(json.dumps(candidate), paths, ctx)`; bucket by status (`added`/`duplicate`/`semantic_duplicate`/`conflict`).
     - valid & below threshold → suggestion.
  5. `log_event(paths, "evaluate", signals=…, auto_logged=…, suggested=…, latency_ms=…)`.
  6. Return `{exit_code, status, signals_detected, auto_logged:[…], suggestions:[…], skipped_invalid:[…]}`.

### Phase 3 — CLI (`cli.py`)

- Add `--evaluate '<json>'` and `--evaluate-file PATH` (PowerShell-safe; mirrors `--log`/`--log-file`).
- Extend the existing mutual-exclusion guards so `--evaluate` can't combine with other operational flags.
- Dispatch → `evaluate_core(summary, _get_paths(), _build_ctx())`; print via a new `_print_evaluate_verbose(result)` (mirrors `_print_auto_learn_verbose`).

### Phase 4 — MCP (`engine/mcp_server.py`)  *(parallel to Phase 3)*

- Add an `evaluate_prompt` entry to `TOOLS` with an `inputSchema` matching the summary fields.
- Add `elif name == "evaluate_prompt":` in `_call_tool()` — build the summary from `arguments`, call `evaluate_core(summary, paths, ctx)`, return the standard `{"content":[{"type":"text","text": json.dumps(result,…)}]}`.
- Update the module-docstring tool list.

### Phase 5 — Tests

- **`tests/test_pure_functions.py`** — per-detector unit tests, `_build_candidate` schema-validity, threshold gating, and the **downgrade-on-invalid** guard. (Requires extending the AGENTS.md import exception.)
- **`tests/test_evaluate.py`** (NEW) — CLI subprocess integration: high-confidence summary → entry in `learnings.jsonl`; medium → suggestion only; `outcome:"none"` → empty; re-run → dedup; `evaluate_enabled:false` → early return.
- **`tests/test_server.py`** — `evaluate_prompt` tool returns the expected dict (follow existing MCP harness; else route via CLI).

### Phase 6 — Docs, version, deploy

- `AGENTS.md` § Testing — extend the `test_pure_functions.py` import exception to include `evaluate`.
- `docs/cli-reference.md` (`--evaluate`), `docs/config-tuning.md` (`evaluate_*` table), `docs/architecture-overview.md` (Per-Prompt Evaluation section + MCP tool), `templates/agents-memory-section.md` (agents may call `evaluate_prompt` each turn).
- `CHANGELOG.md` entry; bump `VERSION` (minor, e.g. `1.22.0`).
- `scripts/deploy.ps1` after `python -m pytest tests/` passes (per AGENTS.md).

### File Change Summary

| File | Change |
|------|--------|
| `engine/evaluate.py` | **NEW** — detectors + `_build_candidate` + `evaluate_core` |
| `engine/constants.py` | 3 `EVALUATE_*` defaults + `DEFAULTS` |
| `templates/config.json` | 3 `evaluate_*` tuning keys |
| `cli.py` | `load_config()` params; `--evaluate`/`--evaluate-file`; mutex; dispatch; printer |
| `engine/mcp_server.py` | `evaluate_prompt` tool + dispatch branch + docstring |
| `tests/test_pure_functions.py` | detector/builder/threshold/downgrade unit tests |
| `tests/test_evaluate.py` | **NEW** — CLI integration |
| `tests/test_server.py` | `evaluate_prompt` tool test |
| `AGENTS.md` | extend test import exception to `evaluate` |
| `docs/*`, `templates/agents-memory-section.md` | feature docs |
| `CHANGELOG.md`, `VERSION` | feature entry + minor bump |

### Execution order

Phase 1 → 2 → (3 ∥ 4) → 5 → 6.

### Assumptions / risks

- **`source_agent:"system"`** is already valid (`constants.py`); a project overriding `valid_source_agents` must include it (same caveat as auto-learn).
- **No cold-start dependency** — unlike auto-learn, evaluate works on turn 1 (it reads the passed summary, not accumulated metrics).
- **Embedding cost** — `log_core` embeds each auto-logged entry; warm on MCP (model loads once), cold per-call on CLI (acceptable — CLI is the test/scripting path, not the hot path, per Decision 1).
- **Quality guard** — a high-confidence signal lacking `corrected_action`/`rejected_action` can't form a valid `ALWAYS`/`NEVER` action → it **downgrades to a suggestion** rather than auto-logging junk.

## Explicitly Deferred (and why)

- **HTTP `POST /api/evaluate`** — needed for stateful detection + as the IDE-hook backend; ship once the heuristic premise holds.
- **LLM evaluation mode** — adds token cost; only benefits the suggest tier. Revisit if heuristics prove insufficient.
- **Stateful detection** (repeated questions, correction→fix correlation) — session-management complexity; HTTP phase.
- **IDE hook** — north-star (compliance-free, sees both sides), blocked on vendor support.

## Key Risks & Notes

- **Corpus noise is the #1 risk.** Mitigated by the auto-log threshold + `log_core` dedup/merge. Calibrate the threshold during dogfooding.
- **Entry quality depends on summary richness.** If the summary can't yield a schema-valid entry (e.g., no corrected-action text), return a suggestion instead of auto-logging.
- **MCP vs precedent.** This intentionally diverges from `auto-learn`'s "no MCP tool" decision; justification is the per-turn (not batch) cadence and the README's "MCP is primary" stance.
