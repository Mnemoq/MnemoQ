# Option A — Multi-Project Memory: Implementation Plan

Make the V1.12 memory engine reusable across all projects by externalizing the Magpie-specific schema into a per-project `config.json` (this also ships your pending **V2.2**), then distributing the engine as self-contained copies kept in sync from one master via `scaffold.py` / `update.py` — delivered in 12 progressive steps across 5 milestones, with Magpie behavior provably unchanged.

---

## Double-check findings (verification pass)

Verified against `filter.py` / `profile.py`. **Confirmed correct:** `stamp_entry` wraps git in try/except, so non-git projects log fine with `commit:"unknown"` (`:222-231`); `profile.py` is the only local import (copying two files suffices); reassign-at-startup is valid since every check reads module globals at call time. **Corrections folded into the steps below:**

- **New blocker — `step` bound is hardcoded `1–30`** (`:153`): a project with >30 steps would be quarantined. → added nullable `max_step` to the contract + Step 3.
- **`--step` retrieval is side-effecting** — it bumps `access_count` and rewrites `learnings.jsonl` (`:754-758`). → Steps 1/5 diff **stdout only** against a **fresh fixture copy per run**.
- **Baseline must be read-only** — `--consolidate` archives learnings + writes a session file even without `--confirm-reset`. → dropped from Step 1.
- **Empty `learnings.jsonl`** (post sleep-cycle) gives zero regression signal. → regress against a copy of `archive/sprint-10.jsonl` (4 real entries).
- **Version stamp must not pollute retrieval stdout** (agents parse it). → emit `ENGINE_VERSION` via `--version` / stderr, never the retrieval body.
- **`null` taxonomy needs `is not None` guards** before each `in` check — incl. `retrieval_only_agents` at `:551`/`:614` — else `null` config raises `TypeError`.
- **Keep universal schema fixed** (do NOT externalize): `importance` 1–10, `trigger` "When", `action` ALWAYS/NEVER, and the type/severity/scope/debt enums.

## Scope & non-goals

- **In scope:** project-agnostic engine, per-project `config.json`, global master, scaffold + update scripts, opencode templating, regression + smoke tests, the optional A→B env seam.
- **Non-goals:** the path-resolution refactor (Option B), packaging (Option C), and implementing other V2/V3 features. Data **format is unchanged** — no learnings migration.

## Target layout

```
~/.agent-memory/                     # global home (developer-profile.json already lives here)
├── developer-profile.json           # EXISTING — shared cross-project preferences
├── engine/                          # NEW master (single source of truth)
│   ├── filter.py  profile.py        # engine
│   ├── scaffold.py  update.py       # tooling
│   └── templates/                   # config.json, starter md, opencode snippet, generic prompts
└── projects.txt                     # NEW registry: one absolute project path per line

<each project>/memory/               # self-contained copy
├── filter.py  profile.py            # engine (managed by update.py)
├── config.json                      # per-project schema + tuning (NEVER touched by update)
├── learnings.jsonl  quarantine.jsonl
├── SYSTEM_INVARIANTS.md  HANDOFF.md
└── archive/.gitkeep
```

## Engine vs data — what `update.py` may and may not touch

- **Engine (overwritten by update):** `filter.py`, `profile.py` only.
- **Per-project, NEVER touched by update:** `config.json`, `*.jsonl`, `*.md`, `archive/`, `.consolidate_session.json`.
- **Not engine, not copied to new projects:** `migrate_reinforcement.py` (one-off), `developer-profile-sample.json` (belongs to global home), all Magpie `*_PLAN.md` / analysis docs.

## `config.json` contract

```json
{
  "project_name": "Magpie Swoop",
  "engine_min_version": "1.12.0",
  "max_step": 30,
  "valid_domains": ["physics", "ui", "...", "asset_pipeline"],
  "valid_source_agents": ["gm", "code-reviewer", "..."],
  "retrieval_only_agents": ["basic-reviewer", "pro-reviewer"],
  "domain_mappings": { "entities": ["phaser", "typescript"] },
  "tuning": { "decay_rate": 0.995, "score_threshold": 0.15, "max_warnings": 5,
              "max_patterns": 15, "minor_retention": 5, "major_retention": 20,
              "escalation_threshold": 30 }
}
```

**Loader rules:** any missing key → fall back to the engine's current hardcoded default (so a missing `config.json` = today's exact behavior). `valid_domains` / `valid_source_agents` / `retrieval_only_agents` set to `null` → **accept any non-empty string**; `max_step: null` → **no upper bound** (the escape hatches for varied stacks). Universal schema — `importance` 1–10, the `trigger`/`action` rules, and the type/severity/scope/debt enums — stays fixed in code, not configurable.

## Milestones

- **M1 — Engine is project-agnostic** (Steps 1–5). *The core multi-project deliverable + V2.2.*
- **M2 — New projects can be created** (Steps 6–8).
- **M3 — Upgrades propagate to all projects** (Steps 9–10).
- **M4 — opencode projects fully templated** (Step 11).
- **M5 — Verified + documented** (Step 12).

---

## Steps

### Phase A — Safety net

**Step 1 — Branch + golden baseline**
- **Goal:** a meaningful, **read-only** regression oracle before any edit.
- **Why:** the whole refactor rides on proving Magpie's behavior is unchanged; without a trustworthy "before" snapshot there is no way to detect a subtle scoring or validation regression.
- **Context:** `learnings.jsonl` is currently empty (post sleep-cycle) → no retrieval signal; `--step` retrieval is **side-effecting** (bumps `access_count`, rewrites the file at `:754-758`); `--consolidate` also writes (archive + session file) even without `--confirm-reset`.
- **Do:** create a git branch. Build a fixture by copying `memory/archive/sprint-10.jsonl` (4 real entries) into a throwaway memory dir. Capture **stdout only** of read-only modes — `--stats` plus several `--step …` retrievals (component / domain / critical-severity mixes) — against a **fresh fixture copy per run**. Also capture `--log` outcomes (ADDED / DUPLICATE / QUARANTINED) for a few crafted entries. Do **not** run `--consolidate`.
- **Done-when:** baseline stdout + outcomes saved to `memory/.baseline/`; branch active.

### Phase B — Project-agnostic engine → **M1**

**Step 2 — Author `config.json` schema + Magpie's own config**
- **Goal:** define the contract; preserve Magpie behavior.
- **Why:** `config.json` is the single seam that turns a Magpie-specific engine into a project-agnostic one; encoding Magpie's current values in its own copy guarantees this milestone is a no-op for the existing game.
- **Context:** mirrors constants at `filter.py:44-66` and `profile.py:20-34`.
- **Do:** write Magpie `memory/config.json` with values **identical** to current constants; write a stack-neutral template into the engine `templates/`.
- **Done-when:** valid JSON; values byte-match current constants.

**Step 3 — `load_config()` + apply-at-startup in `filter.py`**
- **Goal:** drive schema/tuning from `config.json` with zero change to call sites.
- **Why:** this is the heart of multi-project support — one engine serves any stack by reading its taxonomy and tuning from config, while a missing config falls back to today's exact behavior.
- **Context (precise):** constants are module globals read in `validate_entry` (`:156-163`), `handle_log`/`handle_update` (`:551`,`:614`), `score_entry` (`:690`), `is_in_retention` (`:716-718`), `handle_retrieval` (`:739-752`). `--threshold` default reads `MAJOR_RETENTION` at parser-build time (`:1393`).
- **Do:** add `CONFIG_PATH = os.path.join(MEMORY_DIR, "config.json")` and `load_config()`; as the **first action in `main()`** (after the stdout/stderr reconfigure, before the parser at `:1371`) apply config via **`globals().update({...})`** — a bare `NAME = …` inside `main()` would create locals and silently no-op. Rebind `VALID_DOMAINS`, `VALID_SOURCE_AGENTS`, `VALID_RETRIEVAL_ONLY_AGENTS`, `DECAY_RATE`, weights, `SCORE_THRESHOLD`, `MAX_*`, retention/escalation, and a new `MAX_STEP`. Add `is not None` guards before the `in` checks at `validate_entry` (`:156`,`:162`) **and** the `retrieval_only_agents` checks at `:551`/`:614`. Externalize the step bound at `:153` → `1 <= step <= MAX_STEP` (skip the upper bound when `MAX_STEP is None`). De-Magpie the argparse `description` (`:1372`) and `--step` help text (`:1384`). Leave all universal-schema checks untouched.
- **Done-when:** with Magpie `config.json` AND with it deleted, all Step 1 baselines reproduce identically.

**Step 4 — `profile.py` override + version stamp + (optional) env seam**
- **Goal:** per-project domain mappings; visible version; A→B door.
- **Why:** non-Phaser projects need their own domain→stack mappings for profile preferences to stay relevant; the version stamp makes copy drift visible; the env seam keeps the cheap future upgrade to Option B open.
- **Context:** `get_profile_context(profile, task_domain)` called at `filter.py:775`; mapping default at `profile.py:20-34`. Retrieval **stdout is parsed by agents**, so its body must stay byte-stable.
- **Do:** change signature to `get_profile_context(profile, task_domain, domain_mappings=None)` with precedence **config > profile JSON > default**; pass `cfg["domain_mappings"]` from `filter.py:775`. Add `ENGINE_VERSION = "1.12.0"` exposed via a `--version` flag and emitted to **stderr** — **not** into the retrieval stdout body (else Step 5's zero-diff fails and downstream parsers shift). **Future-proofing seam (one line, `filter.py:36`):** `MEMORY_DIR = os.environ.get("AGENT_MEMORY_DIR") or os.path.dirname(os.path.abspath(__file__))` — inert for Option A, makes the A→B switch shim-only and lets Steps 1/5 point the engine at the fixture dir.
- **Done-when:** `--version` reports the engine version; retrieval stdout is unchanged; domain mappings resolve from config; env-unset behavior unchanged.

**Step 5 — Magpie regression gate**
- **Goal:** prove M1 caused no behavioral drift.
- **Why:** a hard gate — nothing propagates to other projects until the engine is provably identical on Magpie, both with and without a config file present.
- **Do:** re-run the Step 1 read-only captures against **fresh fixture copies** (so `access_count` write-back starts equal), pointing the engine at the fixture via `AGENT_MEMORY_DIR`; diff **stdout + `--log` outcomes** vs baseline. Run twice — with Magpie `config.json` present, and with it deleted (fallback path).
- **Done-when:** zero diff in both modes. **→ Milestone M1.**

### Phase C — Master + scaffold → **M2**

**Step 6 — Establish the global master**
- **Goal:** single source of truth co-located with the existing profile.
- **Why:** every project copy must trace back to one canonical engine so updates have a single origin; placing it beside the existing global profile keeps all shared assets together.
- **Do:** create `~/.agent-memory/engine/`; copy the M1 `filter.py`+`profile.py` and `templates/`; create `projects.txt` and register Magpie's absolute path.
- **Done-when:** running the master copy against Magpie's `memory/` (via cwd) behaves identically.

**Step 7 — `scaffold.py <target-project>`**
- **Goal:** one command stamps a working `memory/` into any project.
- **Why:** reducing "add memory to a new project" to a single command is the practical payoff of the whole effort and the operation you'll run most often.
- **Context:** cross-platform (Windows) → Python + `pathlib`/`shutil.copy2`, not bash.
- **Do:** copy engine files; write a prompted `config.json` (name/domains/agents, or `null`); create empty `learnings.jsonl`/`quarantine.jsonl`, `archive/.gitkeep`, starter `SYSTEM_INVARIANTS.md`/`HANDOFF.md`, and a `memory/.gitignore` (`__pycache__/`, `*.tmp`, `.consolidate_session.json`); append path to `projects.txt`.
- **Done-when:** scaffolded dir runs `python memory/filter.py --step 1 --domain X` → clean `(none)`.

**Step 8 — New-project smoke test (varied stack)**
- **Goal:** prove the quarantine blocker is gone.
- **Why:** validates the core promise on a non-game stack — foreign domains/agents that today's engine would reject must now be accepted.
- **Do:** scaffold a throwaway project with `valid_domains:["frontend","backend"]`, `valid_source_agents:null`; `--log` a `domain:"frontend"` learning.
- **Done-when:** result is `ADDED`, not `QUARANTINED`. **→ Milestone M2.**

### Phase D — Propagation → **M3**

**Step 9 — `update.py`**
- **Goal:** push engine improvements to all registered projects safely.
- **Why:** this is A's answer to its one weakness (drift) — it lets a single engine fix reach every project on demand without ever risking their accumulated learnings.
- **Context:** must honor the engine-vs-data boundary above.
- **Do:** for each path in `projects.txt`: skip if engine checksums already match; else back up existing `filter.py`/`profile.py` to `*.bak`, copy master files, print per-project `ENGINE_VERSION` before/after. **Hard guard:** only `filter.py`/`profile.py` are write targets; assert no `*.jsonl`/`*.md`/`config.json`/`archive/` is ever opened for write.
- **Done-when:** dry-run lists targets; real run updates engine only.

**Step 10 — Update test**
- **Why:** propagation is only trustworthy if it provably updates engine code while leaving every project's data byte-for-byte intact.
- **Do:** bump master `ENGINE_VERSION`; run `update.py`; confirm Magpie + smoke project report the new version and their data-file checksums are unchanged.
- **Done-when:** version propagated, data untouched. **→ Milestone M3.**

### Phase E — opencode templating → **M4**

**Step 11 — opencode wiring + portable prompts**
- **Goal:** new opencode projects get memory wired automatically.
- **Why:** your other projects also run opencode, so a scaffolded project should arrive fully wired (instructions, permissions, prompts) — not just with engine files that nothing invokes.
- **Do:** add to `templates/`: an `opencode.json` snippet (the 3 `instructions` entries + `"python memory/filter.py *": "allow"` permission), stack-neutral `gm`/`code-reviewer`/`test-writer` prompts, a portable **AGENTS.md "Memory" section**, and per-stack `config.json` presets. Have `scaffold.py --opencode` optionally merge the snippet.
- **Done-when:** a scaffolded opencode project loads `SYSTEM_INVARIANTS.md`/`HANDOFF.md` and permits the filter command. **→ Milestone M4.**

### Phase F — Verify & document → **M5**

**Step 12 — Tests + docs**
- **Why:** automated checks lock the new behavior against future edits, and the docs let future-you or another AI IDE operate and extend the system without this conversation as context.
- **Do:** add a minimal `pytest` (config load, fallback-to-defaults, `null` accept-any for domains/agents, `max_step` bound incl. `null`, `--version`/stderr, retrieval-stdout stability); add a "Multi-Project" section to `AGENT_MEMORY_GUIDE.md` documenting `config.json`, master/registry, and scaffold/update usage.
- **Done-when:** tests pass; guide updated. **→ Milestone M5.**

---

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| M1 silently changes Magpie behavior | Step 1 golden baseline + Step 5 zero-diff gate; fallback-to-defaults loader |
| `update.py` clobbers project data | Engine-only write whitelist + `*.bak` backup + checksum skip + write-path assertion (Step 9) |
| Over-strict taxonomy blocks a new stack | `null` domains/agents = accept-any (Steps 2/8) |
| Engine drift across copies | `ENGINE_VERSION` printed every retrieval + one-command `update.py` |
| Windows path/shell issues | All tooling in Python (`pathlib`/`shutil`), invoked via `python` |
| Reassigning globals misses a site / creates locals | Use `globals().update()`; grounded in usage map (`:153, :156-163, :551, :614, :690, :716-752`) |
| Hardcoded `step ≤ 30` quarantines other projects | Externalize as nullable `MAX_STEP` (Step 3) |
| Version banner breaks parsed retrieval stdout | Emit version via stderr / `--version`, never the retrieval body (Step 4) |
| Retrieval write-back confounds regression | Diff stdout against fresh fixture copies; never reuse a mutated fixture (Steps 1/5) |

## Rollback

Per phase and fully reversible: Step 1 branch isolates everything; deleting `config.json` restores current behavior (loader falls back); `update.py` leaves `*.bak` for instant engine restore; no data migration occurs at any point.

## Effort / sequencing

Sequential by milestone; **M1 (Steps 1–5) is the only must-do** for multi-project capability — M2–M5 are additive tooling/quality. Steps 2–4 are small, mechanical edits to two files; Steps 6–11 are new standalone scripts/templates that don't modify the engine logic.

---

## Appendix — Future upgrades (Option B & C)

A, B, and C are rungs of one ladder built on the same Part 1 base and the **same data contract** — switching later is a code-distribution change, not a data migration, so every project's learnings carry over untouched.

**Option B — Central engine + shim.** *Climb when the engine has stabilized and running `update.py` across many projects feels tedious.*
- **What:** one engine at `~/.agent-memory/engine/`; each project keeps a ~12-line `memory/filter.py` shim that sets `AGENT_MEMORY_DIR` and execs the master. Invocation (`python memory/filter.py …`) is unchanged.
- **Adds:** instant propagation — edit once, live everywhere, no copy step.
- **Costs:** refactor the module-level path constants into a resolver (the Step 4 env seam makes this shim-only if added up front); a bad edit now hits all projects at once; projects are no longer fully self-contained.

**Option C — pip CLI package.** *Climb when you want versioned releases / CI gating, or to run the engine off this machine or share it.*
- **What:** package the engine with a `console_scripts` entry point (`agent-memory …`) and discover each project's data dir via the same resolver.
- **Adds:** semver, `pip install --upgrade` everywhere, isolation and testability.
- **Costs:** packaging ceremony (pyproject, build, version mgmt); the invocation rename ripples into `opencode.json` + all agent prompts (or you keep a shim anyway); Windows venv/PATH friction.

**Recommended trajectory:** ship A now → the Step 4 env seam keeps **A→B** shim-only → reach for **C** only if formal distribution becomes a real need.
