#!/usr/bin/env python3
"""Bulk-generate valid synthetic memory entries for stress-testing.

Direct mode writes to <memory_dir>/fakes.jsonl.
Pipeline mode routes each entry through mnemoq --log-file.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

import agent_memory.cli as cli
from agent_memory.cli import setup_paths, load_config
from agent_memory.engine.constants import (
    DEFAULTS,
    VALID_DOMAINS,
    VALID_SOURCE_AGENTS,
    VALID_TYPES,
    VALID_RETRIEVAL_ONLY_AGENTS,
)
from agent_memory.engine.migrate import CURRENT_SCHEMA_VERSION
from agent_memory.engine.retrieval import cosine_similarity, embed_entry, encode_embedding
from agent_memory.engine.metrics import _get_project_id
from agent_memory.engine.validation import validate_entry


# --- Bootstrap ---

def _get_commit(repo_root):
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, cwd=repo_root
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def build_ctx(memory_dir):
    paths = setup_paths(memory_dir)
    cli.PATHS = paths  # must be set before load_config()
    config = load_config()
    ctx = {k.lower(): v for k, v in DEFAULTS.items()}
    if config:
        ctx.update({k.lower(): v for k, v in config.items()})
    return ctx, paths


# --- Domain pools ---

COMMON_SLOTS = {
    "precondition": [
        "the input",
        "the required field",
        "the configuration",
        "dependencies",
        "the context",
        "the buffer",
        "the lock",
        "the connection",
    ],
    "function": [
        "the constructor",
        "the serializer",
        "the event handler",
        "the database query",
        "the external API",
        "the dispatcher",
        "the renderer",
        "the parser",
    ],
    "root_cause": [
        "the input is not validated",
        "a race condition updates shared state",
        "the default value is missing",
        "an exception path is not handled",
        "the cache key is constructed incorrectly",
        "a null reference is dereferenced",
        "the transaction is rolled back silently",
    ],
    "artifact": [
        "the response payload",
        "the persisted state",
        "the rendered output",
        "the log stream",
        "the cached entry",
        "the in-memory index",
        "the downstream message",
    ],
    "condition": [
        "high concurrency",
        "slow network conditions",
        "large payloads",
        "partial failures",
        "cold starts",
        "repeated retries",
        "invalid user input",
    ],
    "optimization": [
        "batch the operations",
        "cache the result",
        "precompute the index",
        "compress the payload",
        "use async I/O",
        "reduce allocations",
    ],
    "resource": [
        "CPU time",
        "memory",
        "network bandwidth",
        "database connections",
        "disk I/O",
        "render time",
    ],
    "metric": [
        "request latency",
        "throughput",
        "memory usage",
        "render time",
        "database load",
        "API response time",
    ],
    "percent": ["30", "50", "70", "85"],
}

DOMAIN_POOLS = {
    "ui": {
        "components": ["Canvas", "InputField", "Toolbar", "Dialog", "ThemeManager"],
        "files_touched": ["src/ui/canvas.py", "src/ui/input_field.py", "src/ui/toolbar.py", "src/ui/dialog.py"],
        "vocab": {
            "error_type": ["KeyError", "ValueError", "RenderError", "TimeoutError"],
            "operation": ["rendering a view", "handling user input", "applying a theme", "opening a dialog"],
            "symptoms": ["blank screen", "stale state", "focus loss", "layout shift"],
            "pattern": ["stateless rendering", "event delegation", "virtual DOM diffing"],
            "anti_pattern": ["direct DOM manipulation", "synchronous reflow loops"],
            "benefit": ["consistent UX", "smooth re-renders", "accessible interactions"],
        },
    },
    "data": {
        "components": ["Parser", "Validator", "Transformer", "SchemaRegistry", "Indexer"],
        "files_touched": ["src/data/parser.py", "src/data/validator.py", "src/data/transformer.py", "src/data/schema.py"],
        "vocab": {
            "error_type": ["ValueError", "TypeError", "SchemaError", "EncodingError"],
            "operation": ["parsing a payload", "validating a record", "transforming a row", "indexing a column"],
            "symptoms": ["malformed rows", "missing columns", "type mismatch", "duplicate keys"],
            "pattern": ["schema-first design", "immutable data transformations", "lazy indexing"],
            "anti_pattern": ["stringly typed fields", "mutating shared data frames"],
            "benefit": ["data integrity", "reproducible transforms", "fast lookups"],
        },
    },
    "tooling": {
        "components": ["CliParser", "Logger", "ConfigLoader", "PluginManager", "Watcher"],
        "files_touched": ["src/tooling/cli.py", "src/tooling/logger.py", "src/tooling/config.py", "src/tooling/plugins.py"],
        "vocab": {
            "error_type": ["ArgumentError", "ConfigError", "FileNotFoundError", "PermissionError"],
            "operation": ["parsing CLI args", "loading a config file", "registering a plugin", "watching a directory"],
            "symptoms": ["silent failure", "ignored flags", "wrong log level", "plugin crash"],
            "pattern": ["declarative configuration", "plugin-based architecture", "structured logging"],
            "anti_pattern": ["global CLI state", "print-based debugging"],
            "benefit": ["observability", "extensibility", "predictable CLI behavior"],
        },
    },
    "performance": {
        "components": ["Profiler", "Cache", "BatchQueue", "Pool", "Optimizer"],
        "files_touched": ["src/performance/profiler.py", "src/performance/cache.py", "src/performance/queue.py", "src/performance/pool.py"],
        "vocab": {
            "error_type": ["TimeoutError", "ResourceExhausted", "MemoryError", "Deadlock"],
            "operation": ["profiling a hot path", "evicting a cache entry", "draining a batch queue", "resizing a pool"],
            "symptoms": ["slow response", "memory spike", "CPU saturation", "queue backlog"],
            "pattern": ["lazy evaluation", "bounded caching", "batch processing"],
            "anti_pattern": ["unbounded caching", "blocking critical paths"],
            "benefit": ["lower latency", "higher throughput", "predictable resource usage"],
        },
    },
    "testing": {
        "components": ["Harness", "MockStore", "AssertionEngine", "CoverageRunner", "Fuzzer"],
        "files_touched": ["src/testing/harness.py", "src/testing/mocks.py", "src/testing/assertions.py", "src/testing/coverage.py"],
        "vocab": {
            "error_type": ["AssertionError", "TestTimeout", "FixtureError", "MockMismatch"],
            "operation": ["running a test suite", "setting up fixtures", "asserting expectations", "collecting coverage"],
            "symptoms": ["flaky test", "missing coverage", "slow suite", "mock leak"],
            "pattern": ["isolated test fixtures", "property-based testing", "contract tests"],
            "anti_pattern": ["shared mutable state", "sleep-based synchronization"],
            "benefit": ["reliable CI", "fast feedback", "regression detection"],
        },
    },
    "security": {
        "components": ["AuthProvider", "TokenValidator", "PolicyEngine", "SecretsManager", "RateLimiter"],
        "files_touched": ["src/security/auth.py", "src/security/tokens.py", "src/security/policy.py", "src/security/secrets.py"],
        "vocab": {
            "error_type": ["AuthError", "TokenExpired", "PermissionDenied", "SecretLeak"],
            "operation": ["validating a token", "checking a policy", "rotating a secret", "rate limiting a request"],
            "symptoms": ["unauthorized access", "token replay", "secret in logs", "rate limit bypass"],
            "pattern": ["defense in depth", "least privilege", "secret rotation"],
            "anti_pattern": ["hardcoded secrets", "broad permissions"],
            "benefit": ["reduced blast radius", "compliance", "auditability"],
        },
    },
    "api": {
        "components": ["Router", "Middleware", "Serializer", "Client", "RateLimiter"],
        "files_touched": ["src/api/router.py", "src/api/middleware.py", "src/api/serializer.py", "src/api/client.py"],
        "vocab": {
            "error_type": ["ValidationError", "NotFoundError", "RateLimitError", "SerializeError"],
            "operation": ["routing a request", "running middleware", "serializing a response", "calling a client"],
            "symptoms": ["404 spike", "serialization failure", "middleware exception", "client timeout"],
            "pattern": ["consistent response envelopes", "middleware pipelines", "versioned endpoints"],
            "anti_pattern": ["leaking internals in errors", "unversioned breaking changes"],
            "benefit": ["API stability", "client safety", "clear error contracts"],
        },
    },
    "backend": {
        "components": ["Server", "Handlers", "Validation", "Storage", "Dispatcher"],
        "files_touched": ["src/engine/server.py", "src/engine/handlers.py", "src/engine/validation.py", "src/engine/storage.py"],
        "vocab": {
            "error_type": ["KeyError", "ValueError", "TimeoutError", "StorageError"],
            "operation": ["handling a POST request", "serializing a response", "validating input", "persisting a record"],
            "symptoms": ["500 response", "stale read", "handler crash", "storage timeout"],
            "pattern": ["pass ctx dict and Paths to engine functions", "separate core logic from CLI wrappers"],
            "anti_pattern": ["read module globals inside core functions", "mix CLI argparse with business logic"],
            "benefit": ["testability", "reuse across API and CLI", "clear separation of concerns"],
        },
    },
    "frontend": {
        "components": ["Component", "Store", "Router", "ServiceWorker", "Renderer"],
        "files_touched": ["src/frontend/component.js", "src/frontend/store.js", "src/frontend/router.js", "src/frontend/sw.js"],
        "vocab": {
            "error_type": ["ReferenceError", "TypeError", "NetworkError", "RenderError"],
            "operation": ["mounting a component", "dispatching a store action", "navigating a route", "caching an asset"],
            "symptoms": ["white screen", "infinite spinner", "state desync", "cache miss"],
            "pattern": ["single source of truth", "component composition", "offline-first caching"],
            "anti_pattern": ["prop drilling", "synchronous state mutations"],
            "benefit": ["predictable UI state", "fast navigation", "resilient offline behavior"],
        },
    },
    "database": {
        "components": ["ConnectionPool", "MigrationRunner", "QueryBuilder", "CacheLayer", "Replicator"],
        "files_touched": ["src/database/pool.py", "src/database/migrations.py", "src/database/query.py", "src/database/cache.py"],
        "vocab": {
            "error_type": ["IntegrityError", "OperationalError", "TimeoutError", "MigrationError"],
            "operation": ["borrowing a connection", "running a migration", "building a query", "invalidating a cache"],
            "symptoms": ["connection leak", "migration failure", "slow query", "replication lag"],
            "pattern": ["connection pooling", "versioned migrations", "query parameterization"],
            "anti_pattern": ["string concatenation in queries", "long-running transactions"],
            "benefit": ["data consistency", "safe schema evolution", "efficient queries"],
        },
    },
    "deployment": {
        "components": ["BuildPipeline", "ArtifactStore", "ReleaseManager", "HealthChecker", "Scaler"],
        "files_touched": ["src/deployment/build.py", "src/deployment/artifacts.py", "src/deployment/release.py", "src/deployment/health.py"],
        "vocab": {
            "error_type": ["BuildError", "DeployError", "HealthCheckFailed", "VersionMismatch"],
            "operation": ["triggering a build", "promoting an artifact", "releasing a version", "checking health"],
            "symptoms": ["failed rollout", "health check flapping", "artifact mismatch", "scaling storm"],
            "pattern": ["immutable artifacts", "blue-green deployments", "health-aware rollouts"],
            "anti_pattern": ["in-place upgrades", "manual configuration drift"],
            "benefit": ["reliable releases", "fast rollback", "elastic scaling"],
        },
    },
    "documentation": {
        "components": ["Generator", "Linker", "Linter", "TemplateEngine", "SearchIndex"],
        "files_touched": ["src/docs/generator.py", "src/docs/linker.py", "src/docs/linter.py", "src/docs/templates.py"],
        "vocab": {
            "error_type": ["ParseError", "LinkError", "TemplateError", "LintError"],
            "operation": ["generating a page", "resolving a link", "rendering a template", "indexing search terms"],
            "symptoms": ["broken link", "stale snippet", "template crash", "missing heading"],
            "pattern": ["docs-as-code", "auto-generated API docs", "living style guides"],
            "anti_pattern": ["duplicated docs", "manual screenshots"],
            "benefit": ["accurate docs", "easy onboarding", "discoverable APIs"],
        },
    },
}


# --- Templates ---

TEMPLATES = {
    "bug_fix": {
        "trigger": [
            "When {component} raises {error_type} while {operation}",
            "When {component} fails silently during {operation}",
            "When {operation} in {component} produces {error_type}",
        ],
        "action": [
            "ALWAYS validate {precondition} before {operation}",
            "NEVER call {function} without {precondition}",
            "ALWAYS guard {operation} against {error_type}",
            "NEVER skip the check for {precondition} in {component}",
        ],
        "reason": [
            "{error_type} occurs because {root_cause}, which corrupts {artifact}",
            "Missing {precondition} leads to {error_type} in production under {condition}",
            "{root_cause} during {operation} causes {error_type} and breaks {artifact}",
        ],
    },
    "optimization": {
        "trigger": [
            "When {operation} in {component} becomes a bottleneck",
            "When {metric} for {operation} exceeds the target",
            "When {component} repeatedly performs {operation}",
        ],
        "action": [
            "ALWAYS {optimization} for {operation}",
            "NEVER run {operation} on the critical path",
            "ALWAYS prefetch {artifact} before {operation}",
        ],
        "reason": [
            "Redundant {operation} increases {metric} and wastes {resource}",
            "Profiling shows {operation} accounts for {percent}% of {metric}",
            "Skipping {optimization} for {operation} inflates {resource} usage",
        ],
    },
    "architectural_pattern": {
        "trigger": [
            "When designing {component} for {domain}",
            "When {component} must support {operation}",
            "When adding {operation} to {component}",
        ],
        "action": [
            "ALWAYS apply {pattern} to {component}",
            "NEVER implement {component} with {anti_pattern}",
            "ALWAYS separate {operation} from {component} using {pattern}",
        ],
        "reason": [
            "{pattern} improves {benefit} because it prevents {anti_pattern}",
            "Using {anti_pattern} in {component} harms {benefit}",
            "{pattern} is the standard way to achieve {benefit} in {domain}",
        ],
    },
}


# --- Generation ---

def _fill_templates(entry, rng):
    domain = entry["domain"]
    vocab = DOMAIN_POOLS[domain]["vocab"]
    slots = {}

    def get_slot(name):
        if name not in slots:
            if name == "component":
                slots[name] = rng.choice(entry["components"])
            elif name == "domain":
                slots[name] = entry["domain"]
            elif name in vocab:
                slots[name] = rng.choice(vocab[name])
            elif name in COMMON_SLOTS:
                slots[name] = rng.choice(COMMON_SLOTS[name])
            else:
                slots[name] = f"{{{name}}}"
        return slots[name]

    etype = entry["type"]
    trigger_tmpl = rng.choice(TEMPLATES[etype]["trigger"])
    action_tmpl = rng.choice(TEMPLATES[etype]["action"])
    reason_tmpl = rng.choice(TEMPLATES[etype]["reason"])

    return (
        re.sub(r"\{(\w+)\}", lambda m: get_slot(m.group(1)), trigger_tmpl),
        re.sub(r"\{(\w+)\}", lambda m: get_slot(m.group(1)), action_tmpl),
        re.sub(r"\{(\w+)\}", lambda m: get_slot(m.group(1)), reason_tmpl),
    )


def _generate_symptoms(rng, vocab):
    if "symptoms" not in vocab:
        return None
    return ", ".join(rng.sample(vocab["symptoms"], k=rng.randint(1, 2)))


def generate_entry(step, rng, ctx, *, type=None, domain=None, source_agent=None, resolved_pct=5):
    entry = {
        "step": step,
        "type": type or rng.choice(sorted(ctx["valid_types"] or VALID_TYPES)),
        "domain": domain or rng.choice(sorted(ctx["valid_domains"] or VALID_DOMAINS)),
    }

    loggable_agents = (ctx["valid_source_agents"] or VALID_SOURCE_AGENTS) - (ctx.get("valid_retrieval_only_agents") or VALID_RETRIEVAL_ONLY_AGENTS)
    entry["source_agent"] = source_agent or rng.choice(sorted(loggable_agents))

    pool = DOMAIN_POOLS[entry["domain"]]
    entry["components"] = rng.sample(pool["components"], k=rng.randint(1, 2))
    entry["files_touched"] = rng.sample(pool["files_touched"], k=rng.randint(1, 2))
    entry["importance"] = rng.choices(range(1, 11), weights=[1, 1, 2, 3, 5, 7, 8, 7, 5, 3])[0]
    entry["severity"] = rng.choices(["major", "minor", "critical"], weights=[50, 35, 15])[0]
    entry["access_count"] = rng.choices([0, 1, 2, 3, 5, 8, 12, 15], weights=[40, 25, 15, 8, 5, 3, 2, 2])[0]
    entry["reinforcement_count"] = rng.choices([0, 1, 2, 3], weights=[70, 20, 7, 3])[0]
    entry["verified"] = rng.random() < 0.3
    entry["resolved"] = rng.random() < (resolved_pct / 100.0)
    entry["trigger"], entry["action"], entry["reason"] = _fill_templates(entry, rng)

    if rng.random() < 0.7:
        entry["scope"] = rng.choices(["file", "module", "system"], weights=[50, 35, 15])[0]
    if rng.random() < 0.5:
        entry["debt_level"] = rng.choices(["proper", "workaround", "temporary"], weights=[60, 30, 10])[0]
    if entry["type"] == "bug_fix" and rng.random() < 0.6:
        symptoms = _generate_symptoms(rng, pool["vocab"])
        if symptoms:
            entry["symptoms"] = symptoms

    return entry


def stamp_for_direct(entry, step, total_steps, days_back, rng, paths, seed=None):
    if seed is not None:
        # Fixed reference date so seeded runs are byte-identical.
        base = datetime(2026, 1, 1, tzinfo=timezone.utc) - timedelta(days=days_back)
    else:
        base = datetime.now(timezone.utc) - timedelta(days=days_back)
    fraction = (step - 1) / max(total_steps - 1, 1)
    jitter = rng.uniform(-0.5, 0.5)
    days_offset = days_back * fraction + jitter
    entry["ts"] = (base + timedelta(days=days_offset)).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry["commit"] = _get_commit(paths.repo_root)
    project_id = _get_project_id(paths)
    entry["project_id"] = project_id
    entry["origin_project"] = project_id
    entry["contributing_projects"] = []
    entry["contributors"] = [entry["source_agent"]]
    entry["schema_version"] = CURRENT_SCHEMA_VERSION
    entry.setdefault("access_count", 0)
    entry.setdefault("reinforcement_count", 0)
    return entry


# --- Near-duplicate generation ---

ACTION_VERB_SWAPS = {
    "validate": ["verify", "check"],
    "call": ["invoke", "run"],
    "pass": ["supply", "provide"],
    "use": ["prefer", "employ"],
    "handle": ["process", "manage"],
    "build": ["construct", "create"],
    "check": ["inspect", "assert"],
    "guard": ["protect", "defend"],
    "skip": ["omit", "bypass"],
    "apply": ["adopt", "use"],
    "run": ["execute", "perform"],
    "prefetch": ["preload", "warm"],
    "separate": ["isolate", "decouple"],
    "implement": ["build", "realize"],
}

REASON_REPHRASES = {
    "occurs because": ["happens when", "is triggered by"],
    "leads to": ["results in", "causes"],
    "corrupts": ["damages", "breaks"],
    "prevents": ["avoids", "stops"],
    "increases": ["raises", "inflates"],
    "wastes": ["consumes", "drains"],
    "improves": ["enhances", "strengthens"],
    "harms": ["undermines", "damages"],
    "breaks": ["corrupts", "destroys"],
}


def _mutate_action(action, rng):
    words = action.split()
    for i, w in enumerate(words):
        lower = w.lower().strip(".,;")
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
    for phrase, replacements in REASON_REPHRASES.items():
        if phrase in reason.lower():
            return reason.replace(phrase, rng.choice(replacements), 1)
    return reason


def _make_duplicate(source, max_step, mutate_action, mutate_reason, rng):
    dup = {k: v for k, v in source.items() if k not in ("ts", "step", "commit", "access_count", "reinforcement_count", "embedding")}
    dup["step"] = min(source["step"] + rng.randint(1, 3), max_step)
    action = source["action"]
    reason = source["reason"]
    if mutate_action:
        action = _mutate_action(action, rng)
    if mutate_reason:
        reason = _mutate_reason(reason, rng)
    dup["action"] = action
    dup["reason"] = reason
    return dup


def generate_duplicate(source, rng, max_step, ctx):
    source_vec = embed_entry(source, ctx.get("embedding_model"), ctx.get("embedding_cache_dir"))
    strategies = [(True, False), (False, True), (True, True)]
    best_dup = None
    best_cos = 0.0

    for mutate_action, mutate_reason in strategies:
        dup = _make_duplicate(source, max_step, mutate_action, mutate_reason, rng)
        if source_vec is None:
            return dup, None
        dup_vec = embed_entry(dup, ctx.get("embedding_model"), ctx.get("embedding_cache_dir"))
        if dup_vec is None:
            return dup, None
        cos = cosine_similarity(source_vec, dup_vec)
        if cos > best_cos:
            best_cos = cos
            best_dup = dup
        if cos >= 0.90:
            return dup, cos

    return best_dup, best_cos


# --- Output ---

def run_direct(args, entries, ctx, paths):
    target = args.target or os.path.join(paths.memory_dir, "fakes.jsonl")
    if args.dry_run:
        print(f"[dry-run] Would write {len(entries)} entries -> {target}")
        print("Validation errors: 0")
        return
    if args.clean and os.path.exists(target):
        os.remove(target)

    with open(target, "a", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"Generated {len(entries)} entries -> {target}")
    print("Validation errors: 0")


def run_pipeline(args, entries, ctx, paths):
    if args.target:
        raise SystemExit("ERROR: --target cannot be used with --pipeline (mnemoq writes to learnings.jsonl)")
    if args.dry_run:
        print(f"[dry-run] Would route {len(entries)} entries through mnemoq --log-file pipeline")
        print(f"[dry-run] Target: {paths.learnings_path}")
        if args.clean:
            print(f"[dry-run] --clean would delete {paths.learnings_path}")
        print("Validation errors: 0")
        return
    if args.clean and os.path.exists(paths.learnings_path):
        os.remove(paths.learnings_path)

    successes = failures = 0
    for entry in entries:
        tmp = os.path.join(paths.memory_dir, f"_fake_entry_{uuid.uuid4().hex}.json")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False)
        cmd = [sys.executable, "-m", "agent_memory.cli", "--log-file", tmp]
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


# --- CLI ---

def _assign_steps(count, max_step, mode, rng):
    if mode == "sequential":
        return [min(i, max_step) for i in range(1, count + 1)]
    if mode == "random":
        return [rng.randint(1, max_step) for _ in range(count)]
    if mode == "clustered":
        centers = [rng.randint(1, max_step) for _ in range(3)]
        steps = []
        for _ in range(count):
            c = rng.choice(centers)
            jitter = rng.randint(-2, 2)
            steps.append(max(1, min(max_step, c + jitter)))
        return steps
    raise ValueError(f"Unknown step mode: {mode}")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Generate synthetic memory entries for stress-testing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/generate_fakes.py --count 100 --clean
  python scripts/generate_fakes.py --count 50 --pipeline --confirm --clean
  python scripts/generate_fakes.py --count 10 --seed 123 --clean
  python scripts/generate_fakes.py --count 50 --pipeline --dry-run
        """,
    )
    parser.add_argument("--count", type=int, required=True, help="Number of entries to generate")
    parser.add_argument("--pipeline", action="store_true", help="Route each entry through mnemoq --log-file")
    parser.add_argument("--stop-on-error", action="store_true", help="Halt on first pipeline failure")
    parser.add_argument("--type", type=str, help="Restrict to one type")
    parser.add_argument("--domain", type=str, help="Restrict to one domain")
    parser.add_argument("--source-agent", type=str, help="Restrict to one source agent")
    parser.add_argument("--target", type=str, help="Output file (direct mode only)")
    parser.add_argument("--clean", action="store_true", help="Delete target file before generating")
    parser.add_argument("--embed", action="store_true", help="Compute embeddings in direct mode")
    parser.add_argument("--step-mode", type=str, default="sequential", help="sequential | random | clustered")
    parser.add_argument("--max-step", type=int, help="Max step cap (default: config max_step or 30)")
    parser.add_argument("--days-back", type=int, default=30, help="Spread timestamps over N days (direct mode only)")
    parser.add_argument("--duplicates", type=float, default=0, help="Percentage of entries that are near-duplicates")
    parser.add_argument("--resolved", type=float, default=5, help="Percentage of entries marked resolved")
    parser.add_argument("--seed", type=int, help="Random seed for reproducible generation")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print summary without writing anything")
    parser.add_argument("--confirm", action="store_true", help="Required to use --pipeline without --dry-run (safety guard against corrupting learnings.jsonl)")
    parser.add_argument("--memory-dir", type=str, help="Memory directory (passed to mnemoq)")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.count <= 0:
        parser.error("--count must be positive")
    if args.step_mode not in ("sequential", "random", "clustered"):
        parser.error("--step-mode must be sequential, random, or clustered")
    if not (0 <= args.duplicates <= 100):
        parser.error("--duplicates must be between 0 and 100")
    if not (0 <= args.resolved <= 100):
        parser.error("--resolved must be between 0 and 100")
    if args.pipeline:
        if args.target:
            parser.error("--target cannot be used with --pipeline")
        if args.embed:
            parser.error("--embed cannot be used with --pipeline")
        if not args.dry_run and not args.confirm:
            parser.error("--pipeline writes to learnings.jsonl — use --confirm to proceed or --dry-run to preview")

    try:
        ctx, paths = build_ctx(args.memory_dir)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except (TypeError, ValueError) as e:
        print(f"ERROR: config.json: {e}", file=sys.stderr)
        sys.exit(1)

    max_step = args.max_step if args.max_step is not None else (ctx.get("max_step") or 30)

    valid_types = ctx["valid_types"] or VALID_TYPES
    if args.type and args.type not in valid_types:
        parser.error(f"--type must be one of: {', '.join(sorted(valid_types))}")

    valid_domains = ctx["valid_domains"] or VALID_DOMAINS
    if args.domain and args.domain not in valid_domains:
        parser.error(f"--domain must be one of: {', '.join(sorted(valid_domains))}")

    valid_agents = ctx["valid_source_agents"] or VALID_SOURCE_AGENTS
    if args.source_agent and args.source_agent not in valid_agents:
        parser.error(f"--source-agent must be one of: {', '.join(sorted(valid_agents))}")

    rng = random.Random(args.seed)

    steps = _assign_steps(args.count, max_step, args.step_mode, rng)
    entries = [
        generate_entry(
            step,
            rng,
            ctx,
            type=args.type,
            domain=args.domain,
            source_agent=args.source_agent,
            resolved_pct=args.resolved,
        )
        for step in steps
    ]

    duplicate_count = int(round(args.count * args.duplicates / 100.0))
    duplicate_cosines = []
    generated_duplicates = 0
    for _ in range(duplicate_count):
        if not entries:
            break
        source = rng.choice(entries)
        dup, cos = generate_duplicate(source, rng, max_step, ctx)
        if cos is None or cos >= 0.90:
            generated_duplicates += 1
            if cos is not None:
                duplicate_cosines.append(cos)
        entries.append(dup)

    bad = []
    for idx, entry in enumerate(entries):
        errs = validate_entry(entry, ctx)
        if errs:
            bad.append((idx, errs))
    if bad:
        print(f"Validation errors: {len(bad)} entries invalid; not writing", file=sys.stderr)
        for idx, errs in bad[:5]:
            print(f"  entry {idx}: {'; '.join(errs)}", file=sys.stderr)
        sys.exit(1)

    if not args.pipeline:
        total_steps = max(steps) if steps else max_step
        for entry in entries:
            stamp_for_direct(entry, entry["step"], total_steps, args.days_back, rng, paths, seed=args.seed)
            if args.embed:
                entry["embedding"] = encode_embedding(embed_entry(entry, ctx.get("embedding_model"), ctx.get("embedding_cache_dir")))
            else:
                entry["embedding"] = None
        run_direct(args, entries, ctx, paths)
    else:
        run_pipeline(args, entries, ctx, paths)

    if duplicate_count:
        mean_cos = sum(duplicate_cosines) / len(duplicate_cosines) if duplicate_cosines else 0.0
        print(f"Duplicates requested: {duplicate_count}, valid: {generated_duplicates}" + (f" (mean cosine: {mean_cos:.2f})" if duplicate_cosines else " (embedding model unavailable)"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
