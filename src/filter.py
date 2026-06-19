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
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from profile import load_profile, get_profile_context
from engine_version import get_engine_version

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

SESSION_EXPIRY_MINUTES = 10

DECAY_RATE = 0.995
SCORE_THRESHOLD = 0.15
COMPONENT_WEIGHT = 1.0
FILE_WEIGHT = 0.7
DOMAIN_WEIGHT = 0.4
NO_MATCH_WEIGHT = 0.1

MAX_WARNINGS = 5
MAX_PATTERNS = 15

MINOR_RETENTION = 5
MAJOR_RETENTION = 20
ESCALATION_THRESHOLD = 30
MAX_STEP = 30  # Default; overridden by config.json if present

VALID_SOURCE_AGENTS = {"gm", "code-reviewer", "test-writer", "phaser-scout", "asset-scout", "plan-reviewer", "basic-reviewer", "pro-reviewer"}

# Universal schema constraints — not configurable per-project.
# 
# Rationale: These define the fundamental structure of a learning entry.
# Making them configurable would allow project-specific types/severities
# but would break cross-project learning sharing.
#
# Decision: Keep hardcoded for now. Revisit if a concrete use case emerges
# where a project needs custom types (e.g., "feature_request", "documentation")
# or severities (e.g., "blocker", "trivial").
#
# Tradeoff: We value cross-project learning sharing over per-project flexibility.
# If all projects use the same schema, learnings can be shared between projects.
# If each project has custom schema, sharing breaks (a learning with type
# "feature_request" from Project A would fail validation in Project B).
VALID_TYPES = {"bug_fix", "optimization", "architectural_pattern"}
VALID_DOMAINS = {"physics", "ui", "audio", "data", "tooling", "entities", "scenes", "spawner", "performance", "mobile", "testing", "phaser_api", "asset_pipeline"}
VALID_SEVERITIES = {"minor", "major", "critical"}
VALID_SCOPES = {"file", "module", "system"}
VALID_DEBT_LEVELS = {"proper", "workaround", "temporary"}
VALID_RETRIEVAL_ONLY_AGENTS = {"basic-reviewer", "pro-reviewer"}

# Two-phase initialization:
# Phase 1 (module load): DOMAIN_MAPPINGS = None (default, use profile/hardcoded)
# Phase 2 (main() startup): load_config() may set DOMAIN_MAPPINGS via globals().update()
# This allows config.json to override the default at runtime.
DOMAIN_MAPPINGS = None  # None means "use profile.py's DEFAULT_DOMAIN_MAPPINGS"

STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "for", "on",
    "with", "at", "by", "from", "as", "into", "through", "during", "before",
    "after", "above", "below", "between", "and", "but", "or", "not", "so",
    "yet", "both", "either", "neither", "each", "every", "all", "any", "few",
    "more", "most", "other", "some", "such", "no", "only", "own", "same",
    "than", "too", "very", "just", "because", "until", "while", "if", "then",
    "else", "when", "where", "why", "how", "this", "that", "these", "those",
    "which", "who", "whom", "always", "never", "must", "required", "optional",
    "use", "using", "used", "make", "made", "get", "got", "set", "run",
    "new", "old", "first", "last", "long", "great", "little", "own",
    "its", "it", "he", "she", "they", "them", "his", "her", "their",
    "my", "your", "our", "we", "you", "i", "me", "him", "us",
}

TS_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

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
        with open(config_path, "r", encoding="utf-8") as f:
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
    
    return result

# --- I/O helpers ---

def read_learnings():
    """Read all entries from learnings.jsonl, skipping malformed lines."""
    entries = []
    if not os.path.exists(_get_paths().learnings_path):
        return entries
    with open(_get_paths().learnings_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"WARNING: Skipping malformed line {i} in learnings.jsonl: {e}",
                      file=sys.stderr)
    return entries


def append_learning(entry):
    """Append a single entry to learnings.jsonl with Windows retry."""
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with open(_get_paths().learnings_path, "a", encoding="utf-8") as f:
                f.write(line)
            return
        except PermissionError:
            if attempt < max_retries - 1:
                time.sleep(0.1 * (2 ** attempt))
            else:
                print(f"ERROR: Cannot write to {_get_paths().learnings_path} after {max_retries} attempts", file=sys.stderr)
                print("File may be locked by antivirus or another process.", file=sys.stderr)
                raise


def write_learnings(entries):
    """Rewrite learnings.jsonl atomically via temp file with Windows retry."""
    tmp_path = _get_paths().learnings_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            os.replace(tmp_path, _get_paths().learnings_path)
            return
        except PermissionError:
            if attempt < max_retries - 1:
                time.sleep(0.1 * (2 ** attempt))
            else:
                print(f"WARNING: Atomic write failed after {max_retries} attempts, using fallback", file=sys.stderr)
                try:
                    with open(_get_paths().learnings_path, "w", encoding="utf-8") as f:
                        for entry in entries:
                            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    os.remove(tmp_path)
                except PermissionError:
                    print(f"ERROR: Fallback write also failed. File may be locked.", file=sys.stderr)
                    print(f"Temp file preserved at: {tmp_path}", file=sys.stderr)
                    raise
                except OSError as e:
                    print(f"ERROR: Fallback write failed: {e}", file=sys.stderr)
                    print(f"Temp file preserved at: {tmp_path}", file=sys.stderr)
                    raise


def quarantine(raw_input, reason):
    """Append a malformed/rejected entry to quarantine.jsonl."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "raw": raw_input,
        "reason": reason,
        "ts": ts
    }
    with open(_get_paths().quarantine_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# --- Validation ---

def validate_entry(entry):
    """Validate an entry against the schema. Returns list of error strings."""
    errors = []

    required_fields = [
        "step", "source_agent", "type", "domain", "components",
        "files_touched", "trigger", "action", "reason",
        "importance", "severity"
    ]

    for field in required_fields:
        if field not in entry:
            errors.append(f"Missing required field: {field}")

    if errors:
        return errors

    if not isinstance(entry["step"], int) or entry["step"] < 1:
        errors.append("step must be a positive integer")
    elif MAX_STEP is not None and entry["step"] > MAX_STEP:
        errors.append(f"step must be <= {MAX_STEP}")

    if VALID_SOURCE_AGENTS is not None and entry["source_agent"] not in VALID_SOURCE_AGENTS:
        errors.append(f"source_agent must be one of: {', '.join(sorted(VALID_SOURCE_AGENTS))}")

    if entry["type"] not in VALID_TYPES:
        errors.append(f"type must be one of: {', '.join(sorted(VALID_TYPES))}")

    if VALID_DOMAINS is not None and entry["domain"] not in VALID_DOMAINS:
        errors.append(f"domain must be one of: {', '.join(sorted(VALID_DOMAINS))}")

    if entry["severity"] not in VALID_SEVERITIES:
        errors.append(f"severity must be one of: {', '.join(sorted(VALID_SEVERITIES))}")

    if not isinstance(entry["importance"], int) or not (1 <= entry["importance"] <= 10):
        errors.append("importance must be integer 1-10")

    if not isinstance(entry["components"], list) or len(entry["components"]) == 0:
        errors.append("components must be non-empty list of strings")
    elif not all(isinstance(c, str) for c in entry["components"]):
        errors.append("components must be list of strings")

    if not isinstance(entry["files_touched"], list) or len(entry["files_touched"]) == 0:
        errors.append("files_touched must be non-empty list of strings")
    elif not all(isinstance(f, str) for f in entry["files_touched"]):
        errors.append("files_touched must be list of strings")

    if not isinstance(entry["trigger"], str) or not entry["trigger"].strip():
        errors.append("trigger must be non-empty string")
    elif not entry["trigger"].lower().startswith("when"):
        errors.append("trigger must start with 'When' (case-insensitive)")

    if not isinstance(entry["action"], str) or not entry["action"].strip():
        errors.append("action must be non-empty string")
    elif "ALWAYS" not in entry["action"].upper() and "NEVER" not in entry["action"].upper():
        errors.append("action must contain 'ALWAYS' or 'NEVER' (case-insensitive)")

    if not isinstance(entry["reason"], str) or not entry["reason"].strip():
        errors.append("reason must be non-empty string")

    # reinforcement_count is optional (defaults to 0)
    if "reinforcement_count" in entry:
        if not isinstance(entry["reinforcement_count"], int) or entry["reinforcement_count"] < 0:
            errors.append("reinforcement_count must be non-negative integer")

    if "verified" in entry:
        if not isinstance(entry["verified"], bool):
            errors.append("verified must be boolean")

    if "scope" in entry:
        if entry["scope"] not in VALID_SCOPES:
            errors.append(f"scope must be one of: {', '.join(sorted(VALID_SCOPES))}")

    if "symptoms" in entry:
        if not isinstance(entry["symptoms"], str):
            errors.append("symptoms must be string")

    if "debt_level" in entry:
        if entry["debt_level"] not in VALID_DEBT_LEVELS:
            errors.append(f"debt_level must be one of: {', '.join(sorted(VALID_DEBT_LEVELS))}")

    return errors


# --- Write mode ---

def stamp_entry(entry):
    """Auto-inject commit and ts fields if not provided."""
    if "commit" not in entry or not entry["commit"]:
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL,
                cwd=_get_paths().memory_dir
            ).decode().strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            commit = "unknown"
        entry["commit"] = commit

    if "ts" not in entry or not entry["ts"]:
        entry["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if "access_count" not in entry:
        entry["access_count"] = 0

    if "reinforcement_count" not in entry:
        entry["reinforcement_count"] = 0

    if "resolved" not in entry:
        entry["resolved"] = False

    if "verified" not in entry:
        entry["verified"] = False

    if "scope" not in entry:
        entry["scope"] = "file"

    if "symptoms" not in entry:
        entry["symptoms"] = ""

    if "debt_level" not in entry:
        entry["debt_level"] = "proper"

    return entry


def jaccard_similarity(text1, text2):
    """Compute Jaccard similarity between two texts."""
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    if not words1 and not words2:
        return 0.0
    union = words1 | words2
    if len(union) == 0:
        return 0.0
    intersection = words1 & words2
    return len(intersection) / len(union)


def actions_oppose(action1, action2):
    """Check if two actions oppose each other (ALWAYS vs NEVER)."""
    a1_upper = action1.upper()
    a2_upper = action2.upper()
    a1_has_always = "ALWAYS" in a1_upper
    a1_has_never = "NEVER" in a1_upper
    a2_has_always = "ALWAYS" in a2_upper
    a2_has_never = "NEVER" in a2_upper
    return (a1_has_always and a2_has_never) or (a1_has_never and a2_has_always)


def find_best_match(entry, entries):
    """Find the highest similarity match among entries sharing components."""
    entry_components_lower = {c.lower() for c in entry["components"]}
    best_similarity = 0.0
    best_match = None

    for existing in entries:
        if existing.get("resolved", False):
            continue
        existing_components_lower = {c.lower() for c in existing.get("components", [])}
        if not (entry_components_lower & existing_components_lower):
            continue

        trigger_action_new = entry["trigger"] + " " + entry["action"]
        trigger_action_existing = existing["trigger"] + " " + existing["action"]
        similarity = jaccard_similarity(trigger_action_new, trigger_action_existing)

        if similarity > best_similarity:
            best_similarity = similarity
            best_match = existing

    return best_similarity, best_match


# --- AGENTS.md review utilities ---

def parse_agents_sections(agents_md_path):
    """Parse AGENTS.md into a list of (heading, content) tuples.

    Extracts ##, ###, and #### headings. Content is everything from after
    the heading until the next heading of equal or higher level.
    Returns empty list if file doesn't exist or has no headings.
    """
    if not os.path.exists(agents_md_path):
        return []

    with open(agents_md_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    heading_re = re.compile(r"^(#{2,4})\s+(.+)$")
    sections = []
    current_heading = None
    current_lines = []

    for line in lines:
        m = heading_re.match(line)
        if m:
            if current_heading is not None:
                sections.append((current_heading, "\n".join(current_lines)))
            current_heading = m.group(2).strip()
            current_lines = []
        else:
            if current_heading is not None:
                current_lines.append(line)

    if current_heading is not None:
        sections.append((current_heading, "\n".join(current_lines)))

    return sections


def extract_section_keywords(heading, content):
    """Extract a set of lowercase keywords from a section heading and content.

    Strips code blocks (triple-backtick), extracts table cell keywords
    (split on |), strips inline backticks (preserving content), lowercases,
    splits on whitespace, and removes stop-words.
    """
    # Strip triple-backtick code blocks
    content = re.sub(r"```.*?```", "", content, flags=re.DOTALL)

    # Fallback: if unbalanced backticks remain, strip everything after last fence
    if content.count("```") % 2 != 0:
        last_fence = content.rfind("```")
        if last_fence != -1:
            content = content[:last_fence]

    # Extract table cell keywords: split lines on |, take each cell
    table_keywords = []
    for line in content.split("\n"):
        if "|" in line:
            cells = line.split("|")
            for cell in cells:
                cell = cell.strip()
                if cell and cell.replace("-", "") != "":
                    table_keywords.append(cell)

    # Combine heading + content + table keywords
    text = heading + " " + content + " " + " ".join(table_keywords)

    # Strip inline backticks but preserve content: `PooledEntity` -> PooledEntity
    text = text.replace("`", "")

    # Use shared tokenizer
    return tokenize_keywords(text)


def tokenize_keywords(text):
    """Shared tokenizer: lowercase, split, remove stop-words, keep alphanumeric tokens.

    Used by both extract_section_keywords() and check_agents_conflict() to ensure
    consistent keyword extraction across section and learning text.
    """
    words = text.lower().split()

    # Remove stop-words and non-alphanumeric tokens (allow digits, hyphens, underscores)
    keywords = set()
    for word in words:
        word = word.strip(".,;:!?()[]{}\"'")
        if word and word not in STOP_WORDS and re.match(r'^[a-zA-Z0-9._-]+$', word):
            keywords.add(word)

    return keywords


def handle_review_agents(current_step, threshold):
    """Handle --review-agents mode: diagnostic report on AGENTS.md section health."""
    sections = parse_agents_sections(_get_paths().agents_md_path)

    if not sections:
        print("## AGENTS.md Section Health Report")
        print(f"(step {current_step}, threshold {threshold})")
        print()
        if not os.path.exists(_get_paths().agents_md_path):
            print("WARNING: AGENTS.md not found at:", _get_paths().agents_md_path)
        else:
            print("WARNING: AGENTS.md has no ## headings — nothing to report.")
        return 0

    # Extract keywords for each section
    section_keywords = []
    for heading, content in sections:
        keywords = extract_section_keywords(heading, content)
        section_keywords.append((heading, keywords))

    # Read learnings within the threshold window
    entries = read_learnings()
    recent_entries = [
        e for e in entries
        if not e.get("resolved", False)
        and (current_step - e.get("step", 0)) <= threshold
    ]

    # Cross-reference: count matches per section
    section_ref_counts = {heading: 0 for heading, _ in sections}
    unmatched_learnings = []

    for entry in recent_entries:
        trigger_action = entry.get("trigger", "") + " " + entry.get("action", "")
        trigger_action_words = tokenize_keywords(trigger_action)

        matched_sections = []
        for heading, keywords in section_keywords:
            if keywords and (keywords & trigger_action_words):
                section_ref_counts[heading] += 1
                matched_sections.append(heading)

        if not matched_sections:
            unmatched_learnings.append(entry)
        elif len(matched_sections) > 1:
            print(f"WARNING: Learning matches multiple sections: {matched_sections}", file=sys.stderr)
            print(f"  Learning: {entry.get('trigger', '')}", file=sys.stderr)

    # Output report
    print("## AGENTS.md Section Health Report")
    print(f"(step {current_step}, threshold {threshold})")
    print()

    # ACTIVE sections
    active = [(h, c) for h, c in section_ref_counts.items() if c > 0]
    if active:
        print("### ACTIVE — Referenced by learnings")
        for heading, count in active:
            print(f"- {heading.lower().replace(' ', '-')} ({count} refs)")
        print()

    # COLD sections
    cold = [(h, c) for h, c in section_ref_counts.items() if c == 0]
    if cold:
        print("### COLD — No references in last {} steps".format(threshold))
        for heading, count in cold:
            print(f"- {heading.lower().replace(' ', '-')} (0 refs) — may be foundational or stale")
        print()

    # UNMATCHED learnings
    if unmatched_learnings:
        print("### UNMATCHED — Learnings with no section match")
        for entry in unmatched_learnings:
            print(f"- [step-{entry.get('step', '?')}, {entry.get('domain', '?')}] {entry.get('trigger', '')}")
        print()

    return 0


def check_agents_conflict(entry):
    """Check if a learning overlaps with AGENTS.md sections.

    Returns (overlap_detected, best_section, jaccard_score, containment_hits) or
    (False, None, 0.0, 0) if no AGENTS.md reference found.
    """
    files_touched = entry.get("files_touched", [])
    components = entry.get("components", [])

    # Check for AGENTS.md reference
    has_agents_ref = (
        any("AGENTS.md" in f for f in files_touched) or
        any("agents" in c.lower() for c in components)
    )

    if not has_agents_ref:
        return False, None, 0.0, 0

    # Parse AGENTS.md sections
    sections = parse_agents_sections(_get_paths().agents_md_path)
    if not sections:
        return False, None, 0.0, 0

    # Get learning keywords (use shared tokenizer for consistency)
    trigger_action = entry.get("trigger", "") + " " + entry.get("action", "")
    learning_keywords = tokenize_keywords(trigger_action)

    best_section = None
    best_jaccard = 0.0
    best_containment = 0

    for heading, content in sections:
        section_keywords = extract_section_keywords(heading, content)
        if not section_keywords:
            continue

        # Jaccard similarity
        union = learning_keywords | section_keywords
        intersection = learning_keywords & section_keywords
        jaccard = len(intersection) / len(union) if union else 0.0

        # Containment hits (how many learning keywords appear in section)
        containment = len(intersection)

        if jaccard > best_jaccard or (jaccard == best_jaccard and containment > best_containment):
            best_jaccard = jaccard
            best_containment = containment
            best_section = heading

    # Threshold: 0.1 Jaccard OR >=3 containment hits
    # (Lenient for informational warning; large sections dilute Jaccard)
    if best_jaccard >= 0.1 or best_containment >= 3:
        return True, best_section, best_jaccard, best_containment

    return False, None, best_jaccard, best_containment


def handle_log(json_str):
    """Handle --log mode: validate, dedup-check, append."""
    try:
        entry = json.loads(json_str)
    except json.JSONDecodeError as e:
        quarantine(json_str, f"JSON parse error: {e}")
        print(f"QUARANTINED: JSON parse error: {e}", file=sys.stderr)
        return 1

    errors = validate_entry(entry)
    if errors:
        reason = "; ".join(errors)
        quarantine(json_str, reason)
        print(f"QUARANTINED: {reason}", file=sys.stderr)
        return 1

    if VALID_RETRIEVAL_ONLY_AGENTS is not None and entry.get("source_agent") in VALID_RETRIEVAL_ONLY_AGENTS:
        quarantine(json_str, f"{entry['source_agent']} is retrieval-only (use --step mode)")
        print(f"QUARANTINED: {entry['source_agent']} is retrieval-only", file=sys.stderr)
        return 1

    entry = stamp_entry(entry)

    # AGENTS.md conflict detection (informational, non-blocking)
    conflict_detected, best_section, jaccard_score, containment_hits = check_agents_conflict(entry)
    if conflict_detected:
        print(f"WARNING: Learning may overlap with AGENTS.md section '{best_section}'")
        print(f"  Jaccard: {jaccard_score:.2f}, Containment hits: {containment_hits}")
        print(f"  Learning: {entry['trigger']}: {entry['action']}")
        print(f"  Consider: Updating existing section instead of adding new rule")

    existing_entries = read_learnings()
    similarity, best_match = find_best_match(entry, existing_entries)

    if similarity >= 0.7:
        best_match["access_count"] = best_match.get("access_count", 0) + 1
        best_match["reinforcement_count"] = best_match.get("reinforcement_count", 0) + 1
        write_learnings(existing_entries)
        print(f"DUPLICATE — existing entry matches (similarity: {similarity:.2f}):")
        print(f"  [step-{best_match['step']}, {best_match['domain']}, {best_match['source_agent']}] {best_match['trigger']}: {best_match['action']}")
        print(f"  access_count incremented to {best_match['access_count']}.")
        print(f"  reinforcement_count incremented to {best_match['reinforcement_count']}.")
        return 0

    if 0.4 <= similarity < 0.7:
        if actions_oppose(entry["action"], best_match["action"]):
            append_learning(entry)
            print(f"CONFLICT — potential contradiction detected (similarity: {similarity:.2f}):")
            print(f"  Existing: [step-{best_match['step']}, {best_match['domain']}, {best_match['source_agent']}] {best_match['trigger']}: {best_match['action']}")
            print(f"  Your entry proposes an opposing action for the same trigger.")
            print(f"  Follow the Challenge Protocol: re-submit with type 'architectural_pattern' and")
            print(f"  explain in the reason why the old rule no longer applies.")
            return 0
        else:
            append_learning(entry)
            print(f"ADDED [step-{entry['step']}, {entry['type']}, {entry['domain']}] {entry['trigger']}: {entry['action']}")
            return 0

    append_learning(entry)
    print(f"ADDED [step-{entry['step']}, {entry['type']}, {entry['domain']}] {entry['trigger']}: {entry['action']}")
    return 0


def handle_update(ts, json_str):
    """Handle --update mode: amend existing entry."""
    try:
        entry = json.loads(json_str)
    except json.JSONDecodeError as e:
        quarantine(json_str, f"JSON parse error: {e}")
        print(f"QUARANTINED: JSON parse error: {e}", file=sys.stderr)
        return 1

    errors = validate_entry(entry)
    if errors:
        reason = "; ".join(errors)
        quarantine(json_str, reason)
        print(f"QUARANTINED: {reason}", file=sys.stderr)
        return 1

    if VALID_RETRIEVAL_ONLY_AGENTS is not None and entry.get("source_agent") in VALID_RETRIEVAL_ONLY_AGENTS:
        quarantine(json_str, f"{entry['source_agent']} is retrieval-only (use --step mode)")
        print(f"QUARANTINED: {entry['source_agent']} is retrieval-only", file=sys.stderr)
        return 1

    original_fields = set(entry.keys())
    entry = stamp_entry(entry)

    existing_entries = read_learnings()
    found = False
    for i, existing in enumerate(existing_entries):
        if existing.get("ts") == ts:
            old_access_count = existing.get("access_count", 0)
            old_reinforcement_count = existing.get("reinforcement_count", 0)
            entry["access_count"] = old_access_count
            entry["reinforcement_count"] = old_reinforcement_count
            
            if "verified" not in original_fields:
                entry["verified"] = existing.get("verified", False)
            if "scope" not in original_fields:
                entry["scope"] = existing.get("scope", "file")
            if "symptoms" not in original_fields:
                entry["symptoms"] = existing.get("symptoms", "")
            if "debt_level" not in original_fields:
                entry["debt_level"] = existing.get("debt_level", "proper")
            
            existing_entries[i] = entry
            found = True
            break

    if not found:
        print(f"ERROR: No entry found with ts={ts}", file=sys.stderr)
        return 1

    write_learnings(existing_entries)
    print(f"UPDATED [step-{entry['step']}, {entry['type']}, {entry['domain']}] {entry['trigger']}: {entry['action']}")
    return 0


def handle_resolve(ts):
    """
    Handle --resolve mode: mark existing entry as resolved (partial update).
    
    Note: Uses read-modify-write pattern without file locking. Safe under
    current sequential execution model. If parallel agent execution is added,
    implement fcntl.flock() or equivalent per-platform locking.
    """
    if not TS_PATTERN.match(ts):
        print(f"ERROR: Invalid timestamp format: {ts}. Expected YYYY-MM-DDTHH:MM:SSZ", file=sys.stderr)
        return 1
    
    existing_entries = read_learnings()
    found = False
    resolved_entry = None

    for i, existing in enumerate(existing_entries):
        if existing.get("ts") == ts:
            existing["resolved"] = True
            resolved_entry = existing
            found = True
            break

    if not found:
        print(f"ERROR: No entry found with ts={ts}", file=sys.stderr)
        return 1

    write_learnings(existing_entries)
    print(f"RESOLVED [step-{resolved_entry['step']}, {resolved_entry['type']}, {resolved_entry['domain']}] {resolved_entry['trigger']}")
    return 0


# --- Retrieval mode ---

def score_entry(entry, current_step, task_components, task_files, task_domain):
    """Score an entry against the current task context."""
    step_diff = current_step - entry["step"]
    recency = DECAY_RATE ** step_diff
    importance = entry["importance"] / 10.0

    task_components_lower = {c.lower() for c in task_components}
    entry_components_lower = {c.lower() for c in entry["components"]}
    if task_components_lower & entry_components_lower:
        relevance = COMPONENT_WEIGHT
    elif task_files and set(task_files) & set(entry.get("files_touched", [])):
        relevance = FILE_WEIGHT
    elif task_domain and task_domain == entry.get("domain"):
        relevance = DOMAIN_WEIGHT
    else:
        relevance = NO_MATCH_WEIGHT

    return recency * importance * relevance


def is_in_retention(entry, current_step):
    """Check if an entry is within its retention window."""
    severity = entry["severity"]
    step_diff = current_step - entry["step"]
    access_count = entry.get("access_count", 0)

    if severity == "critical":
        return True
    elif severity == "major":
        return step_diff <= MAJOR_RETENTION
    elif severity == "minor":
        return step_diff <= MINOR_RETENTION or access_count > 3
    return False


def handle_retrieval(current_step, task_components, task_files, task_domain):
    """Handle retrieval mode: score, filter, and print relevant learnings."""
    entries = read_learnings()

    warnings = []
    patterns = []
    escalations = []

    for entry in entries:
        if entry.get("resolved", False):
            continue

        if not is_in_retention(entry, current_step):
            continue

        score = score_entry(entry, current_step, task_components, task_files, task_domain)

        if entry["severity"] == "critical" or score >= SCORE_THRESHOLD:
            if entry["severity"] == "critical":
                warnings.append((score, entry))
                step_diff = current_step - entry["step"]
                if step_diff >= ESCALATION_THRESHOLD:
                    escalations.append(entry)
            else:
                patterns.append((score, entry))

    warnings.sort(key=lambda x: x[0], reverse=True)
    patterns.sort(key=lambda x: x[0], reverse=True)

    warnings = warnings[:MAX_WARNINGS]
    patterns = patterns[:MAX_PATTERNS]

    for _, entry in warnings + patterns:
        entry["access_count"] = entry.get("access_count", 0) + 1

    if warnings or patterns:
        write_learnings(entries)

    print("## ⚠ WARNINGS — Read before starting")
    if warnings:
        for _, entry in warnings:
            print(f"- [step-{entry['step']}, {entry['domain']}, {entry['source_agent']}] {entry['trigger']}: {entry['action']} Reason: {entry['reason']}")
            verified_str = "verified" if entry.get("verified", False) else "unverified"
            scope_str = entry.get("scope", "file")
            debt_str = entry.get("debt_level", "proper")
            symptoms_str = entry.get("symptoms", "")
            print(f"  ({verified_str}, scope: {scope_str}, debt: {debt_str}, accessed {entry.get('access_count', 0)}x, reinforced {entry.get('reinforcement_count', 0)}x)")
            if symptoms_str:
                print(f"  Symptoms: {symptoms_str}")
    else:
        print("(none)")

    profile = load_profile()
    # Pass config-provided domain_mappings (may be None if not in config)
    profile_context = get_profile_context(profile, task_domain, domain_mappings=DOMAIN_MAPPINGS)
    print("\n## 🎯 DEVELOPER PREFERENCES")
    if profile_context:
        for pref in profile_context:
            print(f"- [{pref['source']}] {pref['trigger']}: {pref['action']}")
            print(f"  Reason: {pref['reason']}")
    else:
        print("(none)")

    print("\n## RELEVANT PATTERNS")
    if patterns:
        for _, entry in patterns:
            print(f"- [step-{entry['step']}, {entry['domain']}, {entry['source_agent']}] {entry['trigger']}: {entry['action']} Reason: {entry['reason']}")
            verified_str = "verified" if entry.get("verified", False) else "unverified"
            scope_str = entry.get("scope", "file")
            debt_str = entry.get("debt_level", "proper")
            symptoms_str = entry.get("symptoms", "")
            print(f"  ({verified_str}, scope: {scope_str}, debt: {debt_str}, accessed {entry.get('access_count', 0)}x, reinforced {entry.get('reinforcement_count', 0)}x)")
            if symptoms_str:
                print(f"  Symptoms: {symptoms_str}")
    else:
        print("(none)")

    if escalations:
        print("\n## ⚡ ESCALATION — Critical entries unresolved for 30+ steps")
        for entry in escalations:
            print(f"- [step-{entry['step']}, {entry['domain']}, {entry['source_agent']}] {entry['trigger']}: {entry['action']}")

    unresolved_count = sum(1 for e in entries if not e.get("resolved", False))
    
    print(f"\n## STATS: {len(entries)} total entries, {unresolved_count} unresolved")
    
    sleep_cycle_steps = {10, 20, MAX_STEP} if MAX_STEP is not None else {10, 20}
    if unresolved_count > 50 or current_step in sleep_cycle_steps:
        if unresolved_count > 50:
            print(f"\n## SLEEP CYCLE DUE — {unresolved_count} unresolved entries exceed threshold of 50")
        if current_step in sleep_cycle_steps:
            print(f"\n## SLEEP CYCLE DUE — Sprint boundary at step {current_step} ({unresolved_count} unresolved entries)")
        print("Run the Sleep Cycle per AGENTS.md ## Memory before starting new work.")

    return 0


def handle_stats():
    """Handle --stats mode: print summary statistics about the memory system."""
    entries = read_learnings()
    
    if not entries:
        print("## MEMORY STATS")
        print("No entries found.")
        return 0
    
    total = len(entries)
    unresolved = sum(1 for e in entries if not e.get("resolved", False))
    resolved = total - unresolved
    
    avg_access = sum(e.get("access_count", 0) for e in entries) / total
    avg_reinforcement = sum(e.get("reinforcement_count", 0) for e in entries) / total
    
    steps = [e.get("step", 0) for e in entries]
    min_step = min(steps)
    max_step = max(steps)
    
    severity_counts = {}
    for e in entries:
        sev = e.get("severity", "minor")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
    
    type_counts = {}
    for e in entries:
        t = e.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
    
    scope_counts = {}
    for e in entries:
        s = e.get("scope", "file")
        scope_counts[s] = scope_counts.get(s, 0) + 1
    
    debt_counts = {}
    for e in entries:
        d = e.get("debt_level", "proper")
        debt_counts[d] = debt_counts.get(d, 0) + 1
    
    verified_count = sum(1 for e in entries if e.get("verified", False))
    unverified_count = total - verified_count
    
    # Reinforcement pattern analysis
    proven = [e for e in entries if e.get("reinforcement_count", 0) >= 5]
    over_injected = [e for e in entries if e.get("access_count", 0) >= 10 and e.get("reinforcement_count", 0) <= 2]
    under_retrieved = [e for e in entries if e.get("access_count", 0) <= 2 and e.get("reinforcement_count", 0) >= 5]
    
    print("## MEMORY STATS")
    print(f"Total entries: {total}")
    print(f"Unresolved: {unresolved}")
    print(f"Resolved: {resolved}")
    print(f"Average access_count: {avg_access:.1f}")
    print(f"Average reinforcement_count: {avg_reinforcement:.1f}")
    print(f"Step range: {min_step}-{max_step}")
    print(f"\nSeverity breakdown:")
    for sev in ["critical", "major", "minor"]:
        count = severity_counts.get(sev, 0)
        print(f"  {sev}: {count}")
    print(f"\nType breakdown:")
    for t in sorted(type_counts.keys()):
        print(f"  {t}: {type_counts[t]}")
    print(f"\nScope breakdown:")
    for s in ["system", "module", "file"]:
        count = scope_counts.get(s, 0)
        print(f"  {s}: {count}")
    print(f"\nDebt level breakdown:")
    for d in ["proper", "workaround", "temporary"]:
        count = debt_counts.get(d, 0)
        print(f"  {d}: {count}")
    print(f"\nVerified: {verified_count} verified, {unverified_count} unverified")
    
    print(f"\nReinforcement patterns:")
    print(f"  Proven (reinforcement >= 5): {len(proven)}")
    print(f"  Over-injected (access >= 10, reinforcement <= 2): {len(over_injected)}")
    print(f"  Under-retrieved (access <= 2, reinforcement >= 5): {len(under_retrieved)}")
    
    if unresolved > 50:
        print(f"\n## SLEEP CYCLE DUE — {unresolved} unresolved entries exceed threshold of 50")
        print("Run the Sleep Cycle per AGENTS.md ## Memory before starting new work.")
    
    return 0


# --- Sleep Cycle (Consolidation) ---

def score_for_promotion(entry, current_step):
    """Score an entry for promotion to SYSTEM_INVARIANTS.md.

    Formula: 0.4 * access_score + 0.4 * severity_score + 0.2 * recency_score

    The promotion scoring differs from retrieval scoring intentionally:
    - Retrieval scoring answers "is this relevant to the current task?"
    - Promotion scoring answers "is this important enough to become a permanent invariant?"
    """
    access_count = entry.get("access_count", 0)
    severity = entry.get("severity", "minor")
    step_diff = current_step - entry.get("step", current_step)

    # Access count component (0.0 - 1.0, caps at 10 accesses)
    access_score = min(access_count / 10.0, 1.0)

    # Severity component (critical=1.0, major=0.6, minor=0.3)
    severity_map = {"critical": 1.0, "major": 0.6, "minor": 0.3}
    severity_score = severity_map.get(severity, 0.3)

    # Recency component (decays over 30 steps)
    recency_score = max(0.0, 1.0 - step_diff / 30.0)

    # Weighted sum
    promotion_score = (
        0.4 * access_score +
        0.4 * severity_score +
        0.2 * recency_score
    )

    return promotion_score


def is_promotion_candidate(entry, current_step):
    """Check if an entry qualifies for promotion.

    Three gates (any one triggers promotion):
    1. promotion_score >= 0.5 (common case)
    2. severity == "critical" (safety net for critical entries with low scores)
    3. access_count > 5 (safety net for frequently-accessed entries that narrowly miss threshold)
    """
    score = score_for_promotion(entry, current_step)
    severity = entry.get("severity", "minor")
    access_count = entry.get("access_count", 0)

    if score >= 0.5:
        return True, score
    if severity == "critical":
        return True, score
    if access_count > 5:
        return True, score

    return False, score


def detect_contradictions(entries):
    """Identify architectural_pattern entries proposing supersession.

    Detection heuristics:
    - type == "architectural_pattern"
    - reason contains keywords: supersede, outdated, no longer applies, conflicts with, replaces
    """
    contradiction_keywords = {"supersede", "outdated", "no longer applies", "conflicts with", "replaces"}
    contradictions = []

    for entry in entries:
        if entry.get("type") != "architectural_pattern":
            continue

        reason = entry.get("reason", "").lower()
        if any(kw in reason for kw in contradiction_keywords):
            contradictions.append(entry)

    return contradictions


def review_quarantine():
    """Read quarantine.jsonl and summarize entries."""
    if not os.path.exists(_get_paths().quarantine_path):
        return 0, {}, []

    entries = []
    with open(_get_paths().quarantine_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not entries:
        return 0, {}, []

    # Categorize by reason
    reason_counts = {}
    for entry in entries:
        reason = entry.get("reason", "unknown")
        # Heuristic categorization
        if "JSON parse error" in reason:
            category = "JSON parse errors"
        elif "Missing required field" in reason or "must be" in reason:
            category = "Schema validation errors"
        elif "retrieval-only" in reason:
            category = "Permission/agent errors"
        else:
            category = "Other errors"

        reason_counts[category] = reason_counts.get(category, 0) + 1

    # Sample recent failures (last 5)
    recent = entries[-5:] if len(entries) > 5 else entries

    return len(entries), reason_counts, recent


def check_staleness(entry):
    """Check if an entry is stale based on git diff.

    Returns (is_stale, lines_changed, error_message).
    error_message is None on success, or a string describing the failure.
    """
    if "commit" not in entry or not entry.get("files_touched"):
        return False, 0, None

    commit = entry["commit"]
    files = entry["files_touched"]

    try:
        # Use repo root as cwd so git can find files like src/entities/GroundScenery.ts
        result = subprocess.run(
            ["git", "diff", "--stat", f"{commit}..HEAD", "--"] + files,
            capture_output=True, text=True, cwd=_get_paths().repo_root
        )

        if result.returncode != 0:
            return False, 0, f"git diff failed: {result.stderr.strip()[:100]}"

        # Parse diff stat output to count lines changed
        lines_changed = 0
        for line in result.stdout.strip().split("\n"):
            if "|" in line:
                parts = line.split("|")
                if len(parts) >= 2:
                    changes_str = parts[1].strip().split()[0]
                    try:
                        lines_changed += int(changes_str)
                    except ValueError:
                        pass

        # Staleness threshold: >500 lines changed
        # Raised from plan's original >100 to reduce false positives from minor refactors.
        # Percentage-based churn (>60%) deferred to future version (requires original file sizes).
        is_stale = lines_changed > 500

        return is_stale, lines_changed, None

    except FileNotFoundError as e:
        return False, 0, f"git not found: {str(e)[:100]}"


def get_agents_md_suggestions(entries):
    """Find learnings that suggest AGENTS.md updates.

    Detection: files_touched contains "AGENTS.md" OR components contains "agents" (case-insensitive).
    """
    suggestions = []
    for entry in entries:
        files_touched = entry.get("files_touched", [])
        components = entry.get("components", [])

        has_agents_ref = (
            any("AGENTS.md" in f for f in files_touched) or
            any("agents" in c.lower() for c in components)
        )

        if has_agents_ref:
            suggestions.append(entry)

    return suggestions


def infer_sprint_number(entries):
    """Infer sprint number from max step in entries.

    Formula: ceil(max_step / 10)
    Examples: max_step=11 → sprint 2, max_step=20 → sprint 2, max_step=21 → sprint 3
    """
    if not entries:
        return 1

    max_step = max(e.get("step", 0) for e in entries)
    return math.ceil(max_step / 10)


def save_session(sprint_number):
    """Save session timestamp for --confirm-reset safety check."""
    session_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sprint": sprint_number
    }
    try:
        with open(_get_paths().session_file, "w", encoding="utf-8") as f:
            json.dump(session_data, f)
    except IOError as e:
        print(f"WARNING: Could not save session file: {e}", file=sys.stderr)


def load_session():
    """Load session timestamp. Returns (timestamp, sprint) or (None, None) if missing/invalid."""
    if not os.path.exists(_get_paths().session_file):
        return None, None

    try:
        with open(_get_paths().session_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        ts = datetime.fromisoformat(data["timestamp"])
        sprint = data.get("sprint", 1)

        # Check expiry
        now = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        if (now - ts) > timedelta(minutes=SESSION_EXPIRY_MINUTES):
            return None, None

        return ts, sprint

    except (json.JSONDecodeError, KeyError, ValueError, IOError):
        return None, None


def clear_session():
    """Remove session file after successful reset."""
    if os.path.exists(_get_paths().session_file):
        try:
            os.remove(_get_paths().session_file)
        except IOError:
            pass


def handle_consolidate(sprint_number=None, confirm_reset=False, force=False):
    """Handle --consolidate mode: Sleep Cycle for episodic memory.

    Without --confirm-reset:
    1. Validate preconditions
    2. Archive learnings.jsonl
    3. Analyze promotion candidates
    4. Generate consolidation report
    5. Save session for --confirm-reset

    With --confirm-reset:
    1. Check session validity
    2. Clear learnings.jsonl
    3. Remove session file
    """
    if confirm_reset:
        return handle_confirm_reset()

    # Read entries
    entries = read_learnings()
    unresolved = [e for e in entries if not e.get("resolved", False)]

    # Validate preconditions
    if not unresolved:
        print("⚠ No unresolved entries to consolidate. Archive not created.")
        return 0

    # Determine sprint number
    if sprint_number is None:
        sprint_number = infer_sprint_number(unresolved)

    archive_path = os.path.join(_get_paths().archive_dir, f"sprint-{sprint_number}.jsonl")

    # Check if archive already exists
    if os.path.exists(archive_path) and not force:
        print(f"⚠ Warning: {archive_path} already exists.")
        print("  Use --force to overwrite, or specify a different sprint number.")
        return 1

    # Create archive directory if needed
    os.makedirs(_get_paths().archive_dir, exist_ok=True)

    # Archive
    with open(archive_path, "w", encoding="utf-8") as f:
        for entry in unresolved:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"✓ Archived {len(unresolved)} entries to {archive_path}")

    # Current step for scoring (use max step in entries)
    current_step = max(e.get("step", 0) for e in unresolved)

    # Analyze promotion candidates
    candidates = []
    for entry in unresolved:
        is_candidate, score = is_promotion_candidate(entry, current_step)
        if is_candidate:
            candidates.append((score, entry))

    candidates.sort(key=lambda x: x[0], reverse=True)

    # Detect contradictions
    contradictions = detect_contradictions(unresolved)

    # Review quarantine
    quarantine_count, quarantine_breakdown, quarantine_recent = review_quarantine()

    # Staleness detection
    stale_entries = []
    for entry in unresolved:
        is_stale, lines_changed, error = check_staleness(entry)
        stale_entries.append((is_stale, lines_changed, error, entry))

    # AGENTS.md suggestions (Phase 2 preview)
    agents_suggestions = get_agents_md_suggestions(unresolved)

    # Generate report
    print("\n" + "=" * 60)
    print(f"## SLEEP CYCLE REPORT — Sprint {sprint_number}")
    print("=" * 60)

    # Promotion candidates
    print(f"\n## 🎯 PROMOTION CANDIDATES ({len(candidates)} entries)")
    print("The following entries are candidates for promotion to SYSTEM_INVARIANTS.md.")
    print("Review each entry, then apply approved entries to the invariants file.\n")

    if candidates:
        for i, (score, entry) in enumerate(candidates, 1):
            print(f"### Candidate {i}: [step-{entry.get('step', '?')}, {entry.get('domain', '?')}, {entry.get('source_agent', '?')}]")
            print(f"**Trigger:** {entry.get('trigger', '')}")
            print(f"**Action:** {entry.get('action', '')}")
            print(f"**Reason:** {entry.get('reason', '')}")
            print(f"**Promotion score:** {score:.2f} (access_count={entry.get('access_count', 0)}, severity={entry.get('severity', 'minor')}, step_diff={current_step - entry.get('step', 0)})")
            print()
    else:
        print("No promotion candidates found.\n")

    # Contradictions
    print(f"\n## ⚔️ CONTRADICTIONS ({len(contradictions)} entries)")
    print("The following entries propose superseding existing invariants via the Challenge Protocol.")
    print("Review carefully — these may indicate outdated rules or necessary architectural changes.\n")

    if contradictions:
        for i, entry in enumerate(contradictions, 1):
            print(f"### Contradiction {i}: [step-{entry.get('step', '?')}, {entry.get('domain', '?')}, {entry.get('source_agent', '?')}]")
            print(f"**Trigger:** {entry.get('trigger', '')}")
            print(f"**Action:** {entry.get('action', '')}")
            print(f"**Reason:** {entry.get('reason', '')}")
            print(f"**Action required:** Review and update SYSTEM_INVARIANTS.md if applicable")
            print()
    else:
        print("No contradictions detected.\n")

    # Quarantine review
    print(f"\n## 🗑️ QUARANTINE REVIEW")
    print(f"Total quarantined entries: {quarantine_count}\n")

    if quarantine_breakdown:
        print("### Breakdown by reason:")
        for category, count in sorted(quarantine_breakdown.items()):
            print(f"  - {category}: {count}")
        print()

    if quarantine_recent:
        print("### Recent failures:")
        for i, entry in enumerate(quarantine_recent, 1):
            print(f"{i}. [{entry.get('ts', '?')}] {entry.get('reason', 'unknown')}")
            print(f"   Raw: {entry.get('raw', '')[:80]}...")
        print()

    print("Interpretation guidance:")
    print("- Chronic quarantine from one agent → meta_learning signal (agent doesn't understand schema)")
    print("- Repeated validation errors → need for better documentation or examples")
    print("- Clear quarantine after review: echo \"\" > memory/quarantine.jsonl")
    print()

    # Stale entries
    print(f"\n## 🕰️ STALE ENTRIES")
    print("The following entries may be stale based on git history.")
    print("Verify against current code before promoting.\n")

    stale_count = sum(1 for is_stale, _, _, _ in stale_entries if is_stale)
    error_count = sum(1 for _, _, err, _ in stale_entries if err is not None)

    if stale_count > 0 or error_count > 0:
        idx = 1
        for is_stale, lines_changed, error, entry in stale_entries:
            if error:
                print(f"### Uncheckable {idx}: [step-{entry.get('step', '?')}, {entry.get('domain', '?')}, {entry.get('source_agent', '?')}]")
                print(f"**Status:**  Could not check staleness — {error}")
                print(f"**Entry:** {entry.get('trigger', '')}")
                print()
                idx += 1
            elif is_stale:
                print(f"### Stale {idx}: [step-{entry.get('step', '?')}, {entry.get('domain', '?')}, {entry.get('source_agent', '?')}]")
                print(f"**Commit:** {entry.get('commit', 'unknown')}")
                print(f"**Files touched:** {', '.join(entry.get('files_touched', []))}")
                print(f"**Lines changed since entry:** {lines_changed}")
                print(f"**Status:**  HIGH CHURN — verify trigger/action still hold")
                print(f"**Entry:** {entry.get('trigger', '')}")
                print()
                idx += 1
    else:
        print("No stale entries detected.\n")

    # AGENTS.md suggestions (Phase 2 preview)
    if agents_suggestions:
        print(f"\n## AGENTS.md Updates Suggested ({len(agents_suggestions)} entries)")
        print("The following learnings reference AGENTS.md and may suggest updates.\n")

        for entry in agents_suggestions:
            print(f"- [step-{entry.get('step', '?')}, {entry.get('domain', '?')}, {entry.get('source_agent', '?')}] {entry.get('trigger', '')}")
            print(f"  → Suggests reviewing AGENTS.md for potential updates")
        print()

    # Next steps
    print("=" * 60)
    print("## NEXT STEPS")
    print("=" * 60)
    print("1. Review promotion candidates above")
    print("2. Draft proposed diff to SYSTEM_INVARIANTS.md (output in chat or scratch file)")
    print("3. Human reviews and applies approved entries to SYSTEM_INVARIANTS.md")
    print("4. Human confirms with: python memory/filter.py --consolidate --confirm-reset")
    print()

    # Save session for --confirm-reset
    save_session(sprint_number)
    print(f"✓ Session saved. Run --confirm-reset within {SESSION_EXPIRY_MINUTES} minutes to clear learnings.jsonl.")

    return 0


def handle_confirm_reset():
    """Handle --consolidate --confirm-reset: clear learnings.jsonl after review."""
    # Check session validity
    ts, sprint = load_session()

    if ts is None:
        print("ERROR: No recent --consolidate session found.")
        print("  Run --consolidate first, then --confirm-reset within 10 minutes.")
        return 1

    print(f"✓ Session valid (sprint {sprint}, started {ts.strftime('%H:%M:%S')})")

    # Clear learnings.jsonl
    with open(_get_paths().learnings_path, "w", encoding="utf-8") as f:
        pass  # Empty file

    print("✓ learnings.jsonl reset. Sprint complete.")

    # Remove session file
    clear_session()

    return 0


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

    # Note: MAX_STEP help text uses default value (30) since config not loaded yet
    # After config load, MAX_STEP may be overridden, but --version check happens first
    if MAX_STEP is None:
        step_help = "Current plan step number (no upper bound)"
    elif MAX_STEP == 30:
        step_help = "Current plan step number (1-30)"
    else:
        step_help = f"Current plan step number (1-{MAX_STEP})"
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
    parser.add_argument("--threshold", type=int, default=MAJOR_RETENTION, help=f"Step window for --review-agents (default: {MAJOR_RETENTION})")
    parser.add_argument("--consolidate", action="store_true", help="Sleep Cycle: archive learnings, generate promotion report")
    parser.add_argument("--sprint", type=int, help="Sprint number for --consolidate (default: inferred from max step)")
    parser.add_argument("--confirm-reset", action="store_true", help="Clear learnings.jsonl after review (requires recent --consolidate)")
    parser.add_argument("--force", action="store_true", help="Force overwrite existing archive in --consolidate")
    parser.add_argument("--memory-dir", type=str, help="Path to memory directory (default: <cwd>/memory)")

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

    # Load project-specific config and apply to module globals
    try:
        config = load_config()
        if config:
            globals().update(config)
    except (TypeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Handle --log-file: read JSON from file
    if args.log and args.log_file:
        parser.error("--log and --log-file are mutually exclusive")

    log_json = args.log
    if args.log_file:
        try:
            with open(args.log_file, "r", encoding="utf-8-sig") as f:
                log_json = f.read()
        except (IOError, OSError) as e:
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
        return handle_retrieval(args.step, task_components, task_files, task_domain)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
