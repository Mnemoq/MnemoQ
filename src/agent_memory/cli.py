#!/usr/bin/env python3
"""
Agent Memory Filter
Retrieval engine + write-path gate for the episodic memory system.

CLI modes:
  --step N --components A,B [--files f1,f2] [--domain d]
      Retrieve and score relevant learnings for the current task.
  --log '<json>'
      Validate, dedup-check, and append a learning to learnings.jsonl.
  --log-file PATH
      Same as --log but reads JSON from a file (PowerShell-safe).
  --update <ts> (--log '<json>' | --log-file PATH)
      Amend an existing learning (identified by timestamp).
  --resolve <ts>
      Mark an existing learning as resolved (partial update, preserves all fields).
  --stats
      Print memory system statistics.
  --review-agents --step N [--threshold T]
      Diagnostic report on AGENTS.md section health (ACTIVE/COLD/UNMATCHED).
  --consolidate [--sprint N]
      Sleep Cycle: archive learnings, generate promotion report.
  --consolidate --confirm-reset
      Clear learnings.jsonl after review (requires recent --consolidate run).
  --verify
      Validate every entry in learnings.jsonl against the schema.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from agent_memory.engine_version import get_engine_version

# --- Paths Dataclass ---

@dataclass(frozen=True)
class Paths:
    """Immutable container for all path-dependent state."""
    memory_dir: str
    repo_root: str
    config_path: str
    learnings_path: str
    quarantine_path: str
    archive_dir: str
    session_file: str
    agents_md_path: str

# --- Constants ---

ENGINE_VERSION = get_engine_version()

# ponytail: module-level singleton, parameterize if multi-instance needed
PATHS: Paths | None = None


def _get_paths() -> Paths:
    """Get PATHS or raise if not initialized."""
    if PATHS is None:
        raise RuntimeError("PATHS not initialized. Call setup_paths() first.")
    return PATHS


# Import defaults from engine.constants
from agent_memory.engine.constants import DEFAULTS as _CONST_DEFAULTS

# Module-level ctx dict: seeded from DEFAULTS (lowercased), overlaid by load_config() in main()
# Engine modules expect lowercase keys; DEFAULTS and load_config() use UPPERCASE.
_CTX = {k.lower(): v for k, v in _CONST_DEFAULTS.items()}


# --- Path resolution ---

def resolve_memory_dir(memory_dir_arg: str | None) -> str:
    """Resolve memory directory path.
    
    Resolution priority:
      1. --memory-dir CLI flag
      2. AGENT_MEMORY_DIR environment variable
      3. <cwd>/memory/ (if it exists)
      4. Raise ValueError
    
    All paths are normalized to absolute paths.
    Explicit paths (--memory-dir and AGENT_MEMORY_DIR) are validated to exist.
    
    Raises:
        ValueError: If no valid memory directory is found.
    """
    if memory_dir_arg is not None:
        raw = memory_dir_arg.strip()
        if raw and os.path.isdir(raw):
            return os.path.abspath(raw)
        raise ValueError(f"--memory-dir path does not exist or is not a directory: {raw}")
    
    env_dir = os.environ.get("AGENT_MEMORY_DIR")
    if env_dir:
        raw = env_dir.strip()
        if raw and os.path.isdir(raw):
            return os.path.abspath(raw)
        raise ValueError(f"AGENT_MEMORY_DIR path does not exist or is not a directory: {raw}")
    
    cwd_memory = os.path.join(os.getcwd(), "memory")
    if os.path.isdir(cwd_memory):
        return os.path.abspath(cwd_memory)
    
    raise ValueError("No memory directory found. Use --memory-dir or run from a project root.")


def setup_paths(memory_dir_arg: str | None) -> Paths:
    """Resolve and return all path-dependent state.
    
    Returns a Paths dataclass. Does not mutate module state.
    
    Note: repo_root is derived as os.path.dirname(memory_dir). If memory_dir
    is the repo root itself (edge case: --memory-dir .), agents_md_path will
    resolve to <parent>/AGENTS.md. This is pre-existing behavior.
    """
    memory_dir = resolve_memory_dir(memory_dir_arg)
    repo_root = os.path.dirname(memory_dir)
    
    return Paths(
        memory_dir=memory_dir,
        repo_root=repo_root,
        config_path=os.path.join(memory_dir, "config.json"),
        learnings_path=os.path.join(memory_dir, "learnings.jsonl"),
        quarantine_path=os.path.join(memory_dir, "quarantine.jsonl"),
        archive_dir=os.path.join(memory_dir, "archive"),
        session_file=os.path.join(memory_dir, ".consolidate_session.json"),
        agents_md_path=os.path.join(repo_root, "AGENTS.md"),
    )

# --- Config loading ---

def load_config():
    """Load project-specific configuration from config.json.
    
    Returns a dict mapping Python constant names to their configured values.
    Only whitelisted keys are returned (see output contract below).
    
    Returns empty dict if config.json is missing or malformed.
    Raises TypeError if type validation fails.
    Raises ValueError if range validation fails.
    
    Output contract:
    {
        # Tuning parameters (float)
        "DECAY_RATE": float,              # Range: 0.0 < x < 1.0
        "SCORE_THRESHOLD": float,         # Range: 0.0 <= x <= 1.0
        "COMPONENT_WEIGHT": float,        # Range: x >= 0.0
        "FILE_WEIGHT": float,             # Range: x >= 0.0
        "DOMAIN_WEIGHT": float,           # Range: x >= 0.0
        "NO_MATCH_WEIGHT": float,         # Range: x >= 0.0
        
        # Tuning parameters (int)
        "MAX_WARNINGS": int,              # Range: x >= 0
        "MAX_PATTERNS": int,              # Range: x >= 0
        "MINOR_RETENTION": int,           # Range: x >= 0
        "MAJOR_RETENTION": int,           # Range: x >= 0
        "ESCALATION_THRESHOLD": int,      # Range: x >= 0
        
        # Configurable arrays (converted to sets for O(1) lookup)
        "VALID_DOMAINS": set,             # null in config → None in dict
        "VALID_SOURCE_AGENTS": set,       # null in config → None in dict
        "VALID_RETRIEVAL_ONLY_AGENTS": set,  # null in config → None in dict
        
        # Domain mappings (for profile context)
        "DOMAIN_MAPPINGS": dict,          # null or {} in config → None in dict
        
        # Step bound
        "MAX_STEP": int,                  # null in config → None in dict

        # Embedding config
        "EMBEDDING_ALPHA": float,         # Range: 0.0 <= x <= 1.0
        "EMBEDDING_MODEL": str,           # sentence-transformers model name
        "EMBEDDING_CACHE_DIR": str,       # path to model cache directory
    }
    
    Whitelist: Only the keys listed above are returned. All other config.json
    keys (e.g., project_name, engine_min_version) are metadata and not loaded.
    
    Array fields (VALID_DOMAINS, VALID_SOURCE_AGENTS, VALID_RETRIEVAL_ONLY_AGENTS)
    are converted from JSON arrays to Python sets for O(1) membership testing.
    If the config value is null, the dict value is None (skip validation).
    """
    config_path = Path(_get_paths().config_path)
    if not config_path.exists():
        return {}
    
    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        print(f"WARNING: config.json is malformed: {e}", file=sys.stderr)
        return {}
    
    result = {}
    
    # Whitelist of tuning parameters with type and range validation
    tuning = config.get("tuning", {})
    if not isinstance(tuning, dict):
        raise TypeError(f"tuning must be a dict, got {type(tuning).__name__}")
    
    # Float parameters with range validation
    float_params = {
        "decay_rate": ("DECAY_RATE", 0.0, 1.0, False, False),  # (config_key, python_key, min, max, min_inclusive, max_inclusive)
        "score_threshold": ("SCORE_THRESHOLD", 0.0, 1.0, True, True),
        "component_weight": ("COMPONENT_WEIGHT", 0.0, None, True, None),
        "file_weight": ("FILE_WEIGHT", 0.0, None, True, None),
        "domain_weight": ("DOMAIN_WEIGHT", 0.0, None, True, None),
        "no_match_weight": ("NO_MATCH_WEIGHT", 0.0, None, True, None),
        "bm25_k1": ("BM25_K1", 0.0, None, False, None),
        "bm25_b": ("BM25_B", 0.0, 1.0, True, True),
        "embedding_alpha": ("EMBEDDING_ALPHA", 0.0, 1.0, True, True),
        "semantic_dedup_threshold": ("SEMANTIC_DEDUP_THRESHOLD", 0.0, 1.0, True, True),
    }
    
    for config_key, (python_key, min_val, max_val, min_inclusive, max_inclusive) in float_params.items():
        if config_key in tuning:
            value = tuning[config_key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"tuning.{config_key} must be a number, got {type(value).__name__}")
            
            # Range validation
            if min_val is not None:
                if min_inclusive and value < min_val:
                    raise ValueError(f"tuning.{config_key} must be >= {min_val}, got {value}")
                elif not min_inclusive and value <= min_val:
                    raise ValueError(f"tuning.{config_key} must be > {min_val}, got {value}")
            
            if max_val is not None:
                if max_inclusive and value > max_val:
                    raise ValueError(f"tuning.{config_key} must be <= {max_val}, got {value}")
                elif not max_inclusive and value >= max_val:
                    raise ValueError(f"tuning.{config_key} must be < {max_val}, got {value}")
            
            result[python_key] = float(value)
    
    # Integer parameters with range validation
    int_params = {
        "max_warnings": ("MAX_WARNINGS", 0, None),
        "max_patterns": ("MAX_PATTERNS", 0, None),
        "minor_retention": ("MINOR_RETENTION", 0, None),
        "major_retention": ("MAJOR_RETENTION", 0, None),
        "escalation_threshold": ("ESCALATION_THRESHOLD", 0, None),
        "rrf_k": ("RRF_K", 1, None),
        "sleep_cycle_days": ("SLEEP_CYCLE_DAYS", 0, None),
        "sleep_cycle_quarantine_threshold": ("SLEEP_CYCLE_QUARANTINE_THRESHOLD", 0, None),
    }
    
    for config_key, (python_key, min_val, max_val) in int_params.items():
        if config_key in tuning:
            value = tuning[config_key]
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"tuning.{config_key} must be an integer, got {type(value).__name__}")
            
            if min_val is not None and value < min_val:
                raise ValueError(f"tuning.{config_key} must be >= {min_val}, got {value}")
            
            if max_val is not None and value > max_val:
                raise ValueError(f"tuning.{config_key} must be <= {max_val}, got {value}")
            
            result[python_key] = value
    
    # Array parameters (convert to sets, handle null)
    array_params = {
        "valid_domains": "VALID_DOMAINS",
        "valid_source_agents": "VALID_SOURCE_AGENTS",
        "retrieval_only_agents": "VALID_RETRIEVAL_ONLY_AGENTS",
    }
    
    for config_key, python_key in array_params.items():
        if config_key in config:
            value = config[config_key]
            if value is None:
                result[python_key] = None
            elif isinstance(value, list):
                result[python_key] = set(value)
            else:
                raise TypeError(f"{config_key} must be an array or null, got {type(value).__name__}")
    
    # Domain mappings (optional, for profile context)
    if "domain_mappings" in config:
        val = config["domain_mappings"]
        if val is not None:
            if not isinstance(val, dict):
                raise TypeError(f"domain_mappings must be a dict or null, got {type(val).__name__}")
            # Validate structure: dict[str, list[str]]
            for key, value in val.items():
                if not isinstance(key, str):
                    raise TypeError(f"domain_mappings keys must be strings, got {type(key).__name__}")
                if not isinstance(value, list):
                    raise TypeError(f"domain_mappings['{key}'] must be a list, got {type(value).__name__}")
                if not all(isinstance(s, str) for s in value):
                    raise TypeError(f"domain_mappings['{key}'] must contain only strings")
            # Empty dict {} and null are semantically equivalent for domain mappings
            # (both mean "no mappings"). Normalizing to None ensures consistent behavior
            # through the precedence chain without needing separate checks downstream.
            result["DOMAIN_MAPPINGS"] = val if val else None
        else:
            result["DOMAIN_MAPPINGS"] = None
    
    # Step bound (handle null)
    if "max_step" in config:
        value = config["max_step"]
        if value is None:
            result["MAX_STEP"] = None
        elif isinstance(value, int) and not isinstance(value, bool):
            if value < 1:
                raise ValueError(f"max_step must be >= 1, got {value}")
            result["MAX_STEP"] = value
        else:
            raise TypeError(f"max_step must be an integer or null, got {type(value).__name__}")

    # Top-level string params (embedding config)
    string_params = {"embedding_model": "EMBEDDING_MODEL", "embedding_cache_dir": "EMBEDDING_CACHE_DIR"}
    for config_key, python_key in string_params.items():
        if config_key in config:
            value = config[config_key]
            if not isinstance(value, str):
                raise TypeError(f"{config_key} must be a string, got {type(value).__name__}")
            result[python_key] = value

    # Reranker config (top-level)
    if "reranker" in config:
        value = config["reranker"]
        if not isinstance(value, str):
            raise TypeError(f"reranker must be a string, got {type(value).__name__}")
        valid_rerankers = _CONST_DEFAULTS.get("VALID_RERANKERS", set())
        if value not in valid_rerankers:
            raise ValueError(f"reranker must be one of {valid_rerankers}, got '{value}'")
        result["RERANKER"] = value

    if "reranker_top_n" in config:
        value = config["reranker_top_n"]
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"reranker_top_n must be an integer, got {type(value).__name__}")
        if value < 1:
            raise ValueError(f"reranker_top_n must be >= 1, got {value}")
        result["RERANKER_TOP_N"] = value

    if "reranker_model" in config:
        value = config["reranker_model"]
        if not isinstance(value, str):
            raise TypeError(f"reranker_model must be a string, got {type(value).__name__}")
        result["RERANKER_MODEL"] = value

    # Nullable string params (null = auto-probe / server default)
    for config_key, python_key in [("reranker_llm_endpoint", "RERANKER_LLM_ENDPOINT"),
                                   ("reranker_llm_model", "RERANKER_LLM_MODEL")]:
        if config_key in config:
            value = config[config_key]
            if value is None:
                result[python_key] = None
            elif isinstance(value, str):
                result[python_key] = value
            else:
                raise TypeError(f"{config_key} must be a string or null, got {type(value).__name__}")

    return result


# --- Context builder ---

def _build_ctx():
    """Build ctx dict from _CTX (defaults + config overlay).
    
    This replaces the old globals().update(config) pattern.
    All engine modules receive this ctx dict instead of reading module globals.
    """
    return dict(_CTX)


# --- Delegate wrappers ---
# Each delegates to the engine module, passing paths and ctx.

from agent_memory.engine.io import (
    append_learning as _io_append_learning,
)
from agent_memory.engine.io import (
    quarantine as _io_quarantine,
)
from agent_memory.engine.io import (
    read_learnings as _io_read_learnings,
)
from agent_memory.engine.io import (
    write_learnings as _io_write_learnings,
)


def read_learnings():
    """Read all entries from learnings.jsonl, skipping malformed lines."""
    return _io_read_learnings(_get_paths())


def append_learning(entry):
    """Append a single entry to learnings.jsonl with Windows retry."""
    _io_append_learning(_get_paths(), entry)


def write_learnings(entries):
    """Rewrite learnings.jsonl atomically via temp file with Windows retry."""
    _io_write_learnings(_get_paths(), entries)


def quarantine(raw_input, reason):
    """Append a malformed/rejected entry to quarantine.jsonl."""
    _io_quarantine(_get_paths(), raw_input, reason)


from agent_memory.engine.validation import (
    validate_entry as _val_validate_entry,
)


def validate_entry(entry):
    """Validate an entry against the schema. Returns list of error strings."""
    return _val_validate_entry(entry, _build_ctx())


from agent_memory.engine.agents_review import (
    check_agents_conflict as _ar_check_agents_conflict,
)
from agent_memory.engine.agents_review import (
    handle_review_agents as _ar_handle_review_agents,
)


def check_agents_conflict(entry):
    """Check if a learning overlaps with AGENTS.md sections."""
    return _ar_check_agents_conflict(entry, _get_paths())


def handle_review_agents(current_step, threshold):
    """Handle --review-agents mode."""
    return _ar_handle_review_agents(current_step, threshold, _get_paths())


from agent_memory.engine.handlers import (
    handle_log as _h_handle_log,
)
from agent_memory.engine.handlers import (
    handle_resolve as _h_handle_resolve,
)
from agent_memory.engine.handlers import (
    handle_stats as _h_handle_stats,
)
from agent_memory.engine.handlers import (
    handle_update as _h_handle_update,
)


def handle_log(json_str):
    """Handle --log mode."""
    return _h_handle_log(json_str, _get_paths(), _build_ctx())


def handle_update(ts, json_str):
    """Handle --update mode."""
    return _h_handle_update(ts, json_str, _get_paths(), _build_ctx())


def handle_resolve(ts):
    """Handle --resolve mode."""
    return _h_handle_resolve(ts, _get_paths())


def handle_stats():
    """Handle --stats mode."""
    return _h_handle_stats(_get_paths(), ctx=_CTX)


from agent_memory.engine.retrieval import (
    handle_retrieval as _ret_handle_retrieval,
)
from agent_memory.engine.retrieval import (
    is_in_retention as _ret_is_in_retention,
)
from agent_memory.engine.retrieval import (
    score_entry as _ret_score_entry,
)


def score_entry(entry, current_step, task_components, task_files, task_domain):
    """Score an entry against the current task context."""
    return _ret_score_entry(entry, current_step, task_components, task_files, task_domain, _build_ctx())


def is_in_retention(entry, current_step):
    """Check if an entry is within its retention window."""
    return _ret_is_in_retention(entry, current_step, _build_ctx())


def handle_retrieval(current_step, task_components, task_files, task_domain, no_profile=False):
    """Handle retrieval mode: score, filter, and print relevant learnings."""
    return _ret_handle_retrieval(current_step, task_components, task_files, task_domain, _build_ctx(), _get_paths(), no_profile=no_profile)


from agent_memory.engine.consolidation import (
    handle_confirm_reset as _con_handle_confirm_reset,
)
from agent_memory.engine.consolidation import (
    handle_consolidate as _con_handle_consolidate,
)
from agent_memory.engine.metrics import handle_metrics as _met_handle_metrics


def handle_consolidate(sprint_number=None, confirm_reset=False, force=False):
    """Handle --consolidate mode: Sleep Cycle for episodic memory."""
    return _con_handle_consolidate(sprint_number, confirm_reset, force, _get_paths(), _build_ctx())


def handle_confirm_reset():
    """Handle --consolidate --confirm-reset: clear learnings.jsonl after review."""
    return _con_handle_confirm_reset(_get_paths(), _build_ctx())




# --- Main ---

def main():
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr.encoding != "utf-8":
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    # Parse args first (before config loading) so --version works even with broken config
    parser = argparse.ArgumentParser(
        description="Agent Memory Filter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python filter.py --version
  python filter.py --step 6 --components Player,Enemy
  python filter.py --log '{"step":6,"source_agent":"code-reviewer",...}'
  python filter.py --log-file learning.json
  python filter.py --update 2026-06-11T22:00:00Z --log '{"step":6,...}'
  python filter.py --update 2026-06-11T22:00:00Z --log-file learning.json
  python filter.py --resolve 2026-06-11T22:00:00Z
  python filter.py --stats
        """
    )

    parser.add_argument("--version", action="store_true", help="Show engine version and exit")

    max_step_default = _CTX.get("max_step")
    if max_step_default is None:
        step_help = "Current plan step number (no upper bound)"
    else:
        step_help = f"Current plan step number (1-{max_step_default})"
    parser.add_argument("--step", type=int, help=step_help)
    parser.add_argument("--components", type=str, help="Comma-separated component names")
    parser.add_argument("--files", type=str, help="Comma-separated file paths")
    parser.add_argument("--domain", type=str, help="Coarse domain tag")
    parser.add_argument("--log", type=str, help="JSON string to log")
    parser.add_argument("--log-file", type=str, metavar="PATH", help="Path to JSON file to log (PowerShell-safe alternative to --log)")
    parser.add_argument("--update", type=str, metavar="TS", help="Timestamp of existing entry to amend (requires --log or --log-file)")
    parser.add_argument("--resolve", type=str, metavar="TS", help="Timestamp of existing entry to mark as resolved")
    parser.add_argument("--stats", action="store_true", help="Print memory system statistics")
    parser.add_argument("--review-agents", action="store_true", help="Diagnostic report on AGENTS.md section health")
    major_ret_default = _CTX["major_retention"]
    parser.add_argument("--threshold", type=int, default=major_ret_default, help=f"Step window for --review-agents (default: {major_ret_default})")
    parser.add_argument("--consolidate", action="store_true", help="Sleep Cycle: archive learnings, generate promotion report")
    parser.add_argument("--sprint", type=int, help="Sprint number for --consolidate (default: inferred from max step)")
    parser.add_argument("--confirm-reset", action="store_true", help="Clear learnings.jsonl after review (requires recent --consolidate)")
    parser.add_argument("--force", action="store_true", help="Force overwrite existing archive in --consolidate")
    parser.add_argument("--migrate-schema", action="store_true", help="Run schema migration on learnings.jsonl and write updated file")
    parser.add_argument("--eval", action="store_true", help="Run grading harness: test retrieval quality against memory/eval/grading.jsonl")
    parser.add_argument("--memory-dir", type=str, help="Path to memory directory (default: <cwd>/memory)")
    parser.add_argument("--no-profile", action="store_true", help="Skip developer profile loading (for deterministic output)")

    # Metrics flags
    parser.add_argument("--metrics", action="store_true", help="Print metrics summary report")
    parser.add_argument("--metrics-retrieval", action="store_true", help="Deep-dive on retrieval effectiveness")
    parser.add_argument("--metrics-logging", action="store_true", help="Deep-dive on logging patterns")
    parser.add_argument("--metrics-consolidation", action="store_true", help="Consolidation history")
    parser.add_argument("--metrics-trend", action="store_true", help="Time-series trend (last 30 days)")
    parser.add_argument("--metrics-all-projects", action="store_true", help="Cross-project comparison across all registered projects")
    parser.add_argument("--metrics-json", action="store_true", help="Output metrics as JSON (for piping to jq/scripts)")
    parser.add_argument("--metrics-since", type=str, metavar="YYYY-MM-DD", help="Only include events on or after this date")
    parser.add_argument("--metrics-export", type=str, metavar="PATH", help="Export raw metrics events to a file (JSONL format)")

    # API server flags
    parser.add_argument("--serve", action="store_true", help="Start HTTP API server (requires agent-memory[api])")
    parser.add_argument("--dashboard", action="store_true", help="Start HTTP API server with web dashboard UI")
    parser.add_argument("--port", type=int, default=8765, help="Port for --serve/--dashboard (default: 8765)")
    parser.add_argument("--mcp", action="store_true", help="Start MCP server (JSON-RPC over stdio)")
    parser.add_argument("--verify", action="store_true", help="Validate every entry in learnings.jsonl against schema")

    args = parser.parse_args()

    # --version is zero-dependency: works even if config.json is broken
    if args.version and any([args.step, args.log, args.log_file, args.stats, args.consolidate, args.review_agents]):
        parser.error("--version cannot be combined with other operational flags")

    if args.version:
        print(f"agent-memory-engine v{ENGINE_VERSION}", file=sys.stderr)
        return 0

    # Resolve memory directory paths (must happen before load_config)
    global PATHS
    try:
        PATHS = setup_paths(args.memory_dir)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Load project-specific config and overlay onto _CTX (lowercased)
    try:
        config = load_config()
        if config:
            _CTX.update({k.lower(): v for k, v in config.items()})
    except (TypeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Handle --log-file: read JSON from file
    if args.log and args.log_file:
        parser.error("--log and --log-file are mutually exclusive")

    log_json = args.log
    if args.log_file:
        try:
            with open(args.log_file, encoding="utf-8-sig") as f:
                log_json = f.read()
        except OSError as e:
            print(f"ERROR: Cannot read --log-file: {e}", file=sys.stderr)
            sys.exit(1)

    if args.update and not log_json:
        parser.error("--update requires --log or --log-file")

    if args.stats and any([args.step is not None, log_json, args.resolve, args.update, args.review_agents, args.consolidate]):
        parser.error("--stats cannot be combined with --step, --log, --log-file, --resolve, --update, --review-agents, or --consolidate")

    if args.review_agents and args.step is None:
        parser.error("--review-agents requires --step")

    if args.review_agents and any([log_json, args.resolve, args.update, args.consolidate]):
        parser.error("--review-agents cannot be combined with --log, --log-file, --resolve, --update, or --consolidate")

    if args.consolidate and any([args.step, log_json, args.resolve, args.update, args.review_agents, args.stats]):
        parser.error("--consolidate cannot be combined with --step, --log, --log-file, --resolve, --update, --review-agents, or --stats")

    if args.confirm_reset and not args.consolidate:
        parser.error("--confirm-reset requires --consolidate")

    if args.metrics and any([args.step is not None, log_json, args.resolve, args.update, args.review_agents, args.consolidate, args.stats]):
        parser.error("--metrics cannot be combined with operational flags")

    if any([args.metrics_retrieval, args.metrics_logging, args.metrics_consolidation, args.metrics_trend]) and not args.metrics:
        parser.error("--metrics-* deep-dive flags require --metrics")

    if args.migrate_schema and any([args.step is not None, log_json, args.resolve, args.update, args.review_agents, args.consolidate, args.stats, args.metrics]):
        parser.error("--migrate-schema cannot be combined with other operational flags")

    if args.eval and any([args.step is not None, log_json, args.resolve, args.update, args.review_agents, args.consolidate, args.stats, args.metrics, args.migrate_schema]):
        parser.error("--eval cannot be combined with other operational flags")

    if (args.serve or args.dashboard) and any([args.step is not None, log_json, args.resolve, args.update, args.review_agents, args.consolidate, args.stats, args.metrics, args.migrate_schema, args.eval]):
        parser.error("--serve/--dashboard cannot be combined with other operational flags")

    if args.mcp and any([args.step is not None, log_json, args.resolve, args.update, args.review_agents, args.consolidate, args.stats, args.metrics, args.migrate_schema, args.eval, args.serve, args.dashboard]):
        parser.error("--mcp cannot be combined with other operational flags")

    if args.verify and any([args.step is not None, log_json, args.resolve, args.update, args.review_agents, args.consolidate, args.stats, args.metrics, args.migrate_schema, args.eval, args.serve, args.dashboard, args.mcp]):
        parser.error("--verify cannot be combined with other operational flags")

    if args.migrate_schema:
        from agent_memory.engine.migrate import run_migration
        return run_migration(_get_paths())

    if args.eval:
        from agent_memory.engine.eval import run_eval
        return run_eval(_get_paths(), _build_ctx())

    if args.mcp:
        from agent_memory.engine.mcp_server import run_server
        run_server(args.memory_dir)
        return 0

    if args.serve or args.dashboard:
        try:
            import uvicorn
        except ImportError:
            print("ERROR: --serve requires the [api] optional dependencies.", file=sys.stderr)
            print("Install with: pip install agent-memory[api]", file=sys.stderr)
            return 1
        from agent_memory.engine.server import create_app
        api_key = _CTX.get("api_key") or None
        app = create_app(_get_paths(), _build_ctx(), api_key=api_key, dashboard=args.dashboard)
        url = f"http://127.0.0.1:{args.port}"
        if args.dashboard:
            print(f"Agent Memory Dashboard starting on {url}", file=sys.stderr)
            import threading
            import webbrowser
            threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
        else:
            print(f"Agent Memory Engine API starting on {url}", file=sys.stderr)
            print(f"API docs at {url}/docs", file=sys.stderr)
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
        return 0

    if args.verify:
        entries = read_learnings()
        if not entries:
            print("learnings.jsonl is empty or does not exist.", file=sys.stderr)
            return 0
        ctx = _build_ctx()
        bad = []
        for i, entry in enumerate(entries):
            errs = _val_validate_entry(entry, ctx)
            if errs:
                bad.append((i, entry.get("ts", "?"), errs))
        total = len(entries)
        if bad:
            print(f"VERIFICATION FAILED: {len(bad)}/{total} entries invalid", file=sys.stderr)
            for idx, ts, errs in bad[:20]:
                print(f"  line {idx+1} (ts={ts}): {'; '.join(errs)}", file=sys.stderr)
            if len(bad) > 20:
                print(f"  ... and {len(bad) - 20} more", file=sys.stderr)
            return 1
        print(f"VERIFICATION PASSED: {total} entries valid", file=sys.stderr)
        return 0

    if args.metrics:
        return _met_handle_metrics(args, _get_paths())

    if args.review_agents:
        return handle_review_agents(args.step, args.threshold)

    if args.consolidate:
        return handle_consolidate(args.sprint, args.confirm_reset, args.force)

    if args.resolve:
        return handle_resolve(args.resolve)
    elif args.stats:
        return handle_stats()
    elif log_json:
        if args.update:
            return handle_update(args.update, log_json)
        else:
            return handle_log(log_json)
    elif args.step is not None and (args.components or args.files or args.domain):
        task_components = [c.strip() for c in args.components.split(",")] if args.components else []
        task_files = [f.strip() for f in args.files.split(",")] if args.files else []
        task_domain = args.domain
        return handle_retrieval(args.step, task_components, task_files, task_domain, no_profile=args.no_profile)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
