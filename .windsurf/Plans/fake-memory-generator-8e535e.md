# Fake Memory Generator

A script to bulk-generate valid synthetic memory entries for stress-testing the Agent Memory Engine pipeline, with direct-file and full-pipeline modes.

## Summary

`scripts/generate_fakes.py` generates valid JSONL learning entries with realistic text, optional near-duplicates, and weighted field distributions — then either writes them directly to a target file or routes each entry through `filter.py --log-file` so the full validation/embedding/dedup/stamping pipeline is exercised.

## Design Decisions (resolved)

| Decision | Choice |
|---|---|
| Output target (direct mode) | `<memory_dir>/fakes.jsonl`, `--target` to override |
| Pipeline mode target | Always `memory/learnings.jsonl` via `filter.py`; `--target` is rejected |
| Pipeline mode integration | Subprocess: `[sys.executable, src/filter.py, --log-file, tmp]` per entry |
| `memory_dir` resolution | Import `resolve_memory_dir` and `setup_paths` from `filter.py` |
| Config loading | Import `load_config` from `filter.py`; use `MAX_STEP` from config if set |
| Near-duplicate generation | `--duplicates N` (percentage, default 0); keep trigger identical, vary action/reason |
| Duplicate validation | Compute cosine at generation time; retry if below threshold |
| Step distribution | `--step-mode` flag: `sequential` (default), `random`, `clustered`; `--max-step` overrides config |
| Step-to-timestamp mapping | Linear: step 1 → `now - days_back`, step N → `now` |
| Optional fields | Weighted random (scope ~70%, debt_level ~50%, symptoms ~60% for bug_fix, verified ~30% true) |
| Embeddings (direct mode) | `--embed` flag (default off); pipeline mode always computes |
| Components/files | Domain-keyed hardcoded pools |
| Text generation | Template pools with slot-filling per type |
| Cleanup | `--clean` flag (default off, append by default); deletes the effective target file |
| Provenance fields | Populated in direct mode (project_id, origin_project, contributors, schema_version, commit) |
| Commit field | `git rev-parse --short HEAD` if available, else `"unknown"` (matches `stamp_entry()`) |
| access_count / reinforcement_count | Weighted: access 0-2 mostly, tail to ~15; reinforcement mostly 0, occasionally 1-3 |
| Resolved entries | `--resolved N` (percentage, default 5%) |
| Severity distribution | major 50%, minor 35%, critical 15% |
| Pipeline failures | Continue by default; add `--stop-on-error` to halt on first failure |
| Failure logging | Count reported in summary; quarantine already handled by `filter.py` |
| Script location | `scripts/generate_fakes.py` with `sys.path` trick for imports |
| Self-test | Validate all generated entries with `validate_entry()`; print summary |

## CLI Interface

```
python scripts/generate_fakes.py [OPTIONS]

Required:
  --count N              Number of entries to generate

Mode:
  --pipeline             Route each entry through filter.py --log-file (full pipeline)
                         Default: write directly to target file
  --stop-on-error        Halt on first pipeline failure (pipeline mode only)

Pipeline-only rules:
  --target and --embed are rejected with --pipeline
  --days-back is ignored in --pipeline (filter.py stamps timestamps)

Filtering:
  --type T               Restrict to one type (bug_fix, optimization, architectural_pattern)
  --domain D             Restrict to one domain
  --source-agent A       Restrict to one source agent

Output:
  --target PATH          Output file (default: <memory_dir>/fakes.jsonl; direct mode only)
  --clean                Delete effective target file before generating
  --embed                Compute embeddings in direct mode (requires sentence-transformers)

Distribution:
  --step-mode MODE       sequential (default) | random | clustered
  --max-step N           Max step cap (default: config max_step or 30)
                         Sequential mode: if count > max-step, steps are capped at max-step
                         Random/clustered mode: all steps are in [1, max-step]
  --days-back N          Spread timestamps over N days (default: 30, direct mode only)
  --duplicates N         % of entries that are near-duplicates (default: 0)
  --resolved N           % of entries marked resolved (default: 5)

Reproducibility:
  --seed N               Random seed for reproducible generation

Discovery:
  --memory-dir PATH      Memory directory (passed through to filter.py)
```

## Implementation Plan

### 1. Bootstrap and path resolution (~20 lines)

```python
import sys, os, json, subprocess
from datetime import datetime, timedelta, timezone
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src_dir = os.path.join(repo_root, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

import uuid
from filter import resolve_memory_dir, setup_paths, load_config
from engine.constants import DEFAULTS, VALID_TYPES, VALID_DOMAINS, VALID_SEVERITIES, VALID_SOURCE_AGENTS, VALID_RETRIEVAL_ONLY_AGENTS
from engine.validation import validate_entry
from engine.git_utils import stamp_entry
from engine.metrics import _get_project_id
from engine.retrieval import embed_entry, encode_embedding, cosine_similarity


def _get_commit(repo_root):
    """Return current short commit, matching stamp_entry() behavior."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, cwd=repo_root
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
```

- `resolve_memory_dir` and `setup_paths` are reused unchanged from `filter.py` so path resolution stays consistent.
- `load_config` is used to read `MAX_STEP`, `EMBEDDING_MODEL`, `EMBEDDING_CACHE_DIR`, and the array-based valid lists.

### 2. Context builder (~20 lines)

```python
def build_ctx(memory_dir):
    paths = setup_paths(memory_dir)
    config = load_config()  # uses _get_paths() internally; setup_paths must be called first
    ctx = {k.lower(): v for k, v in DEFAULTS.items()}
    if config:
        ctx.update({k.lower(): v for k, v in config.items()})
    return ctx, paths
```

- Mirrors `filter.py`'s `_CTX` initialization.
- `ctx` is then passed to `validate_entry()`, `embed_entry()`, etc. per the engine contract.
- `_get_project_id(paths)` is used to derive `project_id` / `origin_project` for provenance fields.

### 3. Domain-keyed pools and vocabularies (~80 lines)

Hardcoded dicts mapping each of the 12 valid domains to:
- `components`: 3-5 realistic component names
- `files`: 3-5 realistic file paths
- `vocab`: type-specific slot values (error types, operations, anti-patterns, patterns, benefits)

Example:

```python
DOMAIN_POOLS = {
    "backend": {
        "components": ["Server", "Handlers", "Validation", "Storage"],
        "files": ["src/engine/server.py", "src/engine/handlers.py", "src/engine/validation.py"],
        "vocab": {
            "error_types": ["KeyError", "ValueError", "timeout"],
            "operations": ["handling a POST request", "serializing a response", "validating input"],
            "patterns": ["pass ctx dict and Paths to engine functions", "separate core logic from CLI wrappers"],
            "anti_patterns": ["read module globals inside core functions", "mix CLI argparse with business logic"],
            "benefits": ["testability", "reuse across API and CLI", "clear separation of concerns"],
        }
    },
    # ... one entry per valid domain
}
```

### 4. Template pools per type (~60 lines)

3-5 templates per type, each a dict with `{slot}` placeholders.

```python
TEMPLATES = {
    "bug_fix": {
        "trigger": [
            "When {component} raises {error_type} while {operation}",
            "When {component} fails silently during {operation}",
        ],
        "action": [
            "ALWAYS validate {precondition} before {operation}",
            "NEVER call {function} without {precondition}",
        ],
        "reason": [
            "{error_type} occurs because {root_cause}, which corrupts {artifact}",
            "Missing {precondition} leads to {error_type} in production under {condition}",
        ]
    },
    "optimization": { ... },
    "architectural_pattern": { ... }
}
```

Guarantees:
- `trigger` starts with `When`
- `action` contains `ALWAYS` or `NEVER`
- `reason` is non-empty

### 5. Entry generation function (~80 lines)

```python
def _generate_symptoms(rng, vocab):
    return rng.sample(vocab["symptoms"], k=rng.randint(1, 2)) if "symptoms" in vocab else []

def generate_entry(step, rng, ctx, *, type=None, domain=None, source_agent=None, resolved_pct=5):
    entry = {}
    entry["step"] = step
    entry["type"] = type or rng.choice(sorted(VALID_TYPES))
    entry["domain"] = domain or rng.choice(sorted(ctx["valid_domains"] or VALID_DOMAINS))
    # In pipeline mode, avoid retrieval-only agents because filter.py will quarantine them
    loggable_agents = (ctx["valid_source_agents"] or VALID_SOURCE_AGENTS) - (ctx.get("valid_retrieval_only_agents") or VALID_RETRIEVAL_ONLY_AGENTS)
    entry["source_agent"] = source_agent or rng.choice(sorted(loggable_agents))
    entry["components"] = rng.sample(DOMAIN_POOLS[entry["domain"]]["components"], k=rng.randint(1, 2))
    entry["files_touched"] = rng.sample(DOMAIN_POOLS[entry["domain"]]["files"], k=rng.randint(1, 2))
    entry["importance"] = rng.choices(range(1, 11), weights=[1,1,2,3,5,7,8,7,5,3])[0]
    entry["severity"] = rng.choices(["major", "minor", "critical"], weights=[50, 35, 15])[0]
    entry["access_count"] = rng.choices([0,1,2,3,5,8,12,15], weights=[40,25,15,8,5,3,2,2])[0]
    entry["reinforcement_count"] = rng.choices([0,1,2,3], weights=[70,20,7,3])[0]
    entry["scope"] = rng.choices(["file", "module", "system"], weights=[50, 35, 15])[0] if rng.random() < 0.7 else None
    entry["debt_level"] = rng.choices(["proper", "workaround", "temporary"], weights=[60, 30, 10])[0] if rng.random() < 0.5 else None
    entry["symptoms"] = _generate_symptoms(rng, DOMAIN_POOLS[entry["domain"]]["vocab"]) if entry["type"] == "bug_fix" and rng.random() < 0.6 else None
    entry["verified"] = rng.random() < 0.3
    entry["resolved"] = rng.random() < (resolved_pct / 100.0)
    entry["trigger"], entry["action"], entry["reason"] = _fill_templates(entry, rng)
    return entry
```

- `resolved_pct` is passed through from CLI args.
- `_generate_symptoms()` samples 1-2 symptom strings from domain vocab when present.
- Random source-agent selection excludes retrieval-only agents (`basic-reviewer`, `pro-reviewer`) so pipeline mode does not quarantine them.
- If `--source-agent` explicitly names a retrieval-only agent, it is allowed in direct mode but the pipeline-mode writer will surface the expected quarantine as a counted failure.

### 6. Timestamp and provenance stamping (~25 lines)

```python
def stamp_for_direct(entry, step, total_steps, days_back, rng, paths):
    # Step 1 -> now - days_back; step total_steps -> now
    base = datetime.now(timezone.utc) - timedelta(days=days_back)
    fraction = (step - 1) / max(total_steps - 1, 1)
    jitter = rng.uniform(-0.5, 0.5)  # half-day jitter
    days_offset = days_back * fraction + jitter
    entry["ts"] = (base + timedelta(days=days_offset)).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry["commit"] = _get_commit(paths.repo_root)
    project_id = _get_project_id(paths)
    entry["project_id"] = project_id
    entry["origin_project"] = project_id
    entry["contributing_projects"] = []
    entry["contributors"] = [entry["source_agent"]]
    entry["schema_version"] = 1
    entry.setdefault("access_count", 0)
    entry.setdefault("reinforcement_count", 0)
    return entry
```

- `_get_commit()` runs `git rev-parse --short HEAD` in `repo_root` (or returns `"unknown"`), matching `stamp_entry()`.
- `stamp_entry()` is not called directly because it would also overwrite `ts` and `access_count`/`reinforcement_count`; we want the generated values to survive.
- Provenance fields are populated identically to `log_core()` in `engine.handlers`.

### 7. Near-duplicate generation with cosine validation (~40 lines)

```python
ACTION_VERB_SWAPS = {
    "validate": ["verify", "check"],
    "call": ["invoke", "run"],
    "pass": ["supply", "provide"],
    "use": ["prefer", "employ"],
    "handle": ["process", "manage"],
    "build": ["construct", "create"],
}

REASON_REPHRASES = {
    "occurs because": ["happens when", "is triggered by"],
    "leads to": ["results in", "causes"],
    "corrupts": ["damages", "breaks"],
    "prevents": ["avoids", "stops"],
}

def _mutate_action(action, rng):
    # Swap at most one verb in the action while preserving ALWAYS/NEVER
    words = action.split()
    for i, w in enumerate(words):
        lower = w.lower().strip(".,;")
        # Match base form or common third-person endings
        candidates = None
        if lower in ACTION_VERB_SWAPS:
            candidates = ACTION_VERB_SWAPS[lower]
        elif lower.endswith("es") and lower[:-2] in ACTION_VERB_SWAPS:
            candidates = ACTION_VERB_SWAPS[lower[:-2]]
        elif lower.endswith("s") and lower[:-1] in ACTION_VERB_SWAPS:
            candidates = ACTION_VERB_SWAPS[lower[:-1]]
        if candidates:
            words[i] = rng.choice(candidates)
            return " ".join(words)
    return action

def _mutate_reason(reason, rng):
    # Replace one phrase with a near-synonym
    for phrase, replacements in REASON_REPHRASES.items():
        if phrase in reason.lower():
            return reason.replace(phrase, rng.choice(replacements), 1)
    return reason

def generate_duplicate(source, rng, max_step):
    dup = {k: v for k, v in source.items() if k not in ("ts", "step", "commit", "access_count", "reinforcement_count", "embedding")}
    # Keep trigger identical; vary action or reason slightly
    if rng.random() < 0.5:
        dup["action"] = _mutate_action(source["action"], rng)
    else:
        dup["reason"] = _mutate_reason(source["reason"], rng)
    dup["step"] = min(source["step"] + rng.randint(1, 3), max_step)
    return dup
```

- Trigger is kept identical because it carries the most semantic weight.
- `_mutate_action()` swaps a single verb for a synonym while preserving the ALWAYS/NEVER directive; it handles both base and third-person forms.
- `_mutate_reason()` replaces one phrase with a near-synonym.
- `generate_duplicate()` caps the duplicate step at `max_step` so it stays within the valid range.
- After generating the duplicate, compute its embedding and compare against the source using `cosine_similarity()`.
- If cosine < 0.90 (safety margin above the 0.85 dedup threshold), retry with a smaller mutation (e.g., only change the verb, then only change the reason, then keep both unchanged and vary a single word).
- Report: `N duplicates requested, M duplicates generated with mean cosine X.XX`.

### 8. Direct mode writer (~40 lines)

```python
def run_direct(args, entries, ctx, paths):
    target = args.target or os.path.join(paths.memory_dir, "fakes.jsonl")
    if args.clean and os.path.exists(target):
        os.remove(target)

    # Validate before writing so we fail fast on bad entries
    bad = [i for i, e in enumerate(entries) if validate_entry(e, ctx)]
    if bad:
        print(f"Validation errors: {len(bad)} entries invalid; not writing", file=sys.stderr)
        return

    with open(target, "a", encoding="utf-8") as f:
        for entry in entries:
            if args.embed:
                entry["embedding"] = encode_embedding(embed_entry(entry, ctx["embedding_model"], ctx["embedding_cache_dir"]))
            else:
                entry["embedding"] = None
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"Generated {len(entries)} entries -> {target}")
    print(f"Validation errors: 0")
```

### 9. Pipeline mode writer (~50 lines)

```python
def run_pipeline(args, entries, ctx, paths):
    if args.target:
        raise SystemExit("ERROR: --target cannot be used with --pipeline (filter.py writes to learnings.jsonl)")
    if args.clean and os.path.exists(paths.learnings_path):
        os.remove(paths.learnings_path)
    successes = failures = 0
    for entry in entries:
        tmp = os.path.join(paths.memory_dir, f"_fake_entry_{uuid.uuid4().hex}.json")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False)
        cmd = [sys.executable, os.path.join(paths.repo_root, "src", "filter.py"),
               "--log-file", tmp]
        if args.memory_dir:
            cmd += ["--memory-dir", args.memory_dir]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=paths.repo_root)
        if result.returncode == 0:
            successes += 1
        else:
            failures += 1
            print(f"FAILURE: {result.stderr.strip()[:200]}", file=sys.stderr)
            if args.stop_on_error:
                raise SystemExit("Stopping on first pipeline failure")
        os.remove(tmp)
    print(f"Pipeline: {successes} succeeded, {failures} failed")
```

- Working directory is `repo_root` so `filter.py` can resolve `memory/` by default.
- `--memory-dir` is passed through explicitly if provided.
- Failures are already quarantined by `filter.py`; the generator just counts and reports.

### 10. CLI + main (~40 lines)

- argparse with all flags documented above
- `--max-step` default is read from `ctx["max_step"]`; if `None`, fall back to `30`
- `--step-mode sequential` with `--count` > `--max-step`: cap step at `max_step` (produces multiple entries at the final step)
- `--step-mode random`: uniform random steps in `[1, max_step]`
- `--step-mode clustered`: pick 3 random cluster centers in `[1, max_step]`, assign each entry to a cluster, then add small jitter
- `--duplicates` and `--resolved` are percentages; clamp to `[0, 100]` and validate
- `--step-mode` validates against the three allowed values
- `--pipeline` rejects `--target` and `--embed`; `--days-back` is ignored in pipeline mode
- Main loop:
  1. Generate `count` base entries with chosen step-mode
  2. For each requested duplicate, clone a previous entry and stamp it at a later step
  3. Validate all entries
  4. Write or pipeline them
- `_get_project_id()` is imported from `engine.metrics` to keep provenance identical to the real pipeline; this is intentional coupling to a private helper.

## Verification Commands

After generating, these should pass:

```powershell
# Direct mode
python scripts/generate_fakes.py --count 100 --clean
python src/filter.py --stats
python src/filter.py --step 50 --components Server --domain backend

# Pipeline mode
python scripts/generate_fakes.py --count 50 --pipeline --clean
python src/filter.py --stats

# Reproducibility
python scripts/generate_fakes.py --count 10 --seed 123 --clean
# run again, diff should be identical
```

## Estimated size

~375-425 lines

## Risks / Edge Cases

- **Subprocess speed**: `--pipeline` with 1000 entries is slow (~100-200ms each). Acceptable for a test utility.
- **Git commit**: If run outside a git repo, `commit` is `"unknown"`. `check_staleness()` returns `(False, 0, error)` and does not crash.
- **Embedding model missing**: If `--embed` is used without `sentence-transformers`, `embed_entry()` returns `None`; direct mode stores `null`, pipeline mode quarantines the entry.
- **Duplicate threshold**: Mutations are validated at generation time; if the model is unavailable, duplicate generation falls back to a conservative mutation that preserves most of the original text.
- **Config validation**: `load_config()` raises on bad config; the generator catches this and prints a clear error before exiting.

## What this enables testing

- **Retrieval quality**: 100+ entries with realistic text → BM25 + embedding fusion + RRF ranking
- **Semantic dedup**: `--duplicates 15` injects near-duplicates and reports whether they were generated above threshold
- **Retention windows**: `--step-mode random` + severity distribution tests `is_in_retention()`
- **Sleep cycle**: 60 entries with 5% resolved → 57 unresolved (>50 threshold)
- **Dashboard**: realistic data for all views (severity breakdown, time-series, domain distribution)
- **Consolidation**: enough entries to test `--consolidate` archive + promotion logic
- **Full pipeline**: `--pipeline` mode exercises validation → embedding → dedup → stamping → append
