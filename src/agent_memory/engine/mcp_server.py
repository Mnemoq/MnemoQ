# Copyright (C) 2026 Mnemoq
# SPDX-License-Identifier: AGPL-3.0-or-later

"""MCP server for the Agent Memory Engine.

Implements the Model Context Protocol over stdio using stdlib JSON-RPC.
No FastAPI/uvicorn dependency — only pydantic (already required) for validation.

Tools exposed:
  - retrieve_learnings(step, components, files, domain)
  - log_learning(entry_json)
  - resolve_learning(timestamp)
  - get_stats()
  - consolidate(sprint_number)
  - evaluate_prompt(summary)
  - review_agents(step, threshold)

Resources exposed:
  - learnings://project/<id>
  - metrics://project/<id>
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from agent_memory.engine.agents_review import review_agents_core
from agent_memory.engine.consolidation import consolidate_core
from agent_memory.engine.constants import DEFAULTS as _CONST_DEFAULTS
from agent_memory.engine.evaluate import evaluate_core
from agent_memory.engine.handlers import log_core, resolve_core, stats_core
from agent_memory.engine.io import read_learnings
from agent_memory.engine.metrics import _consolidation_stats, _logging_stats, _retrieval_stats, read_metrics
from agent_memory.engine.retrieval import retrieve_core

# ---------------------------------------------------------------------------
# Path / ctx setup (mirrors filter.py but self-contained)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Paths:
    memory_dir: str
    repo_root: str
    config_path: str
    learnings_path: str
    quarantine_path: str
    archive_dir: str
    session_file: str
    agents_md_path: str


def _resolve_memory_dir(memory_dir_arg: str | None) -> str:
    if memory_dir_arg is not None:
        raw = memory_dir_arg.strip()
        if raw and os.path.isdir(raw):
            return os.path.abspath(raw)
        raise ValueError(f"memory-dir path does not exist: {raw}")
    env_dir = os.environ.get("AGENT_MEMORY_DIR")
    if env_dir:
        raw = env_dir.strip()
        if raw and os.path.isdir(raw):
            return os.path.abspath(raw)
        raise ValueError(f"AGENT_MEMORY_DIR path does not exist: {raw}")
    cwd_memory = os.path.join(os.getcwd(), "memory")
    if os.path.isdir(cwd_memory):
        return os.path.abspath(cwd_memory)
    raise ValueError("No memory directory found. Set AGENT_MEMORY_DIR or run from a project root.")


def _setup_paths(memory_dir_arg: str | None) -> _Paths:
    memory_dir = _resolve_memory_dir(memory_dir_arg)
    repo_root = os.path.dirname(memory_dir)
    return _Paths(
        memory_dir=memory_dir,
        repo_root=repo_root,
        config_path=os.path.join(memory_dir, "config.json"),
        learnings_path=os.path.join(memory_dir, "learnings.jsonl"),
        quarantine_path=os.path.join(memory_dir, "quarantine.jsonl"),
        archive_dir=os.path.join(memory_dir, "archive"),
        session_file=os.path.join(memory_dir, ".consolidate_session.json"),
        agents_md_path=os.path.join(repo_root, "AGENTS.md"),
    )


def _load_config(paths: _Paths) -> dict:
    config_path = Path(paths.config_path)
    if not config_path.exists():
        return {}
    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    result = {}
    tuning = config.get("tuning", {})
    if isinstance(tuning, dict):
        for k, v in tuning.items():
            result[k.upper()] = v
    # Array params
    for ck, pk in [("valid_domains", "VALID_DOMAINS"),
                   ("valid_source_agents", "VALID_SOURCE_AGENTS"),
                   ("retrieval_only_agents", "VALID_RETRIEVAL_ONLY_AGENTS")]:
        if ck in config:
            val = config[ck]
            result[pk] = set(val) if isinstance(val, list) else val
    if "domain_mappings" in config:
        result["DOMAIN_MAPPINGS"] = config["domain_mappings"] or None
    if "max_step" in config:
        result["MAX_STEP"] = config["max_step"]
    for ck, pk in [("embedding_model", "EMBEDDING_MODEL"),
                   ("embedding_cache_dir", "EMBEDDING_CACHE_DIR")]:
        if ck in config:
            result[pk] = config[ck]
    # Reranker config (top-level)
    for ck, pk in [("reranker", "RERANKER"), ("reranker_model", "RERANKER_MODEL")]:
        if ck in config:
            result[pk] = config[ck]
    if "reranker_top_n" in config:
        result["RERANKER_TOP_N"] = config["reranker_top_n"]
    for ck, pk in [("reranker_llm_endpoint", "RERANKER_LLM_ENDPOINT"),
                   ("reranker_llm_model", "RERANKER_LLM_MODEL")]:
        if ck in config:
            result[pk] = config[ck]
    if "api_key" in config:
        result["API_KEY"] = config["api_key"]
    return result


def _build_ctx(paths: _Paths) -> dict:
    ctx = {k.lower(): v for k, v in _CONST_DEFAULTS.items()}
    config = _load_config(paths)
    if config:
        ctx.update({k.lower(): v for k, v in config.items()})
    return ctx


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "retrieve_learnings",
        "description": ("Retrieve relevant learnings for the current task context. "
                       "Returns warnings (critical issues) and patterns (architectural guidance), "
                       "scored and ranked by relevance."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "step": {"type": "integer", "minimum": 1, "description": "Current plan step number"},
                "components": {"type": "array", "items": {"type": "string"},
                               "description": "Component names relevant to the task"},
                "files": {"type": "array", "items": {"type": "string"},
                          "description": "File paths being worked on"},
                "domain": {"type": "string", "description": "Coarse domain tag (e.g. 'ui', 'data', 'tooling')"},
            },
            "required": ["step"],
        },
    },
    {
        "name": "log_learning",
        "description": ("Log a new learning entry. Validates, checks for duplicates/semantic duplicates, "
                       "and appends to memory. Returns status (added/duplicate/semantic_duplicate/"
                       "conflict/quarantined) and entry details."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry": {
                    "type": "object",
                    "description": ("Learning entry object with fields: step, source_agent, type, "
                                    "domain, components, files_touched, trigger, action, reason, "
                                    "importance, severity, scope, debt_level, etc."),
                    "properties": {
                        "step": {"type": "integer", "minimum": 1},
                        "source_agent": {"type": "string"},
                        "type": {"type": "string", "enum": ["bug_fix", "optimization", "architectural_pattern"]},
                        "domain": {"type": "string"},
                        "components": {"type": "array", "items": {"type": "string"}},
                        "files_touched": {"type": "array", "items": {"type": "string"}},
                        "trigger": {"type": "string"},
                        "action": {"type": "string"},
                        "reason": {"type": "string"},
                        "importance": {"type": "integer", "minimum": 1, "maximum": 10},
                        "severity": {"type": "string", "enum": ["minor", "major", "critical"]},
                        "scope": {"type": "string", "enum": ["file", "module", "system"]},
                        "debt_level": {"type": "string", "enum": ["proper", "workaround", "temporary"]},
                    },
                    "required": ["step", "source_agent", "type", "domain", "components", "files_touched",
                                 "trigger", "action", "reason", "importance", "severity"],
                },
            },
            "required": ["entry"],
        },
    },
    {
        "name": "resolve_learning",
        "description": "Mark an existing learning entry as resolved by its timestamp.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timestamp": {"type": "string", "description": "Entry timestamp (YYYY-MM-DDTHH:MM:SSZ format)"},
            },
            "required": ["timestamp"],
        },
    },
    {
        "name": "get_stats",
        "description": ("Get memory system statistics: total entries, unresolved/resolved counts, "
                       "severity/type/scope breakdowns, reinforcement patterns, and sleep cycle status."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "consolidate",
        "description": ("Trigger a Sleep Cycle (consolidation): archives unresolved entries, "
                       "generates promotion candidates, detects contradictions, "
                       "and checks for stale entries."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sprint_number": {"type": "integer",
                                  "description": "Sprint number for archive file naming. Auto-inferred if omitted."},
                "force": {"type": "boolean",
                          "description": "Overwrite existing archive if one exists.",
                          "default": False},
            },
        },
    },
    {
        "name": "evaluate_prompt",
        "description": ("Evaluate a structured prompt summary for learnable moments. "
                       "Runs heuristic detectors on the summary, auto-logs high-confidence signals, "
                       "and returns suggestions for medium-confidence ones."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "step": {"type": "integer", "minimum": 1, "description": "Current plan step number"},
                "prompt_type": {"type": "string", "enum": ["human", "agent"],
                                "description": "Who issued the prompt being evaluated"},
                "outcome": {"type": "string",
                            "enum": ["correction", "preference", "bug_fixed", "decision", "workaround", "none"],
                            "description": "Outcome category of the prompt/response cycle"},
                "text": {"type": "string", "description": "Salient gist of the interaction"},
                "corrected_action": {"type": "string", "description": "What the human said to do instead"},
                "rejected_action": {"type": "string", "description": "What the human said not to do"},
                "components": {"type": "array", "items": {"type": "string"},
                               "description": "Components involved in the interaction"},
                "files_touched": {"type": "array", "items": {"type": "string"},
                                  "description": "Files modified or discussed"},
                "error_text": {"type": "string", "description": "Error message if outcome is bug_fixed (optional)"},
            },
            "required": ["step", "prompt_type", "outcome", "components", "files_touched"],
        },
    },
    {
        "name": "review_agents",
        "description": ("Diagnostic report on AGENTS.md section health. "
                       "Cross-references recent learnings with AGENTS.md sections, "
                       "categorizing sections as active (referenced by learnings), cold (no references), "
                       "and identifying unmatched learnings."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "step": {"type": "integer", "minimum": 1, "description": "Current plan step number"},
                "threshold": {"type": "integer", "minimum": 1, "default": 10,
                              "description": "Step window for considering learnings recent"},
            },
            "required": ["step"],
        },
    },
]


RESOURCE_TEMPLATES = [
    {
        "uriTemplate": "learnings://project/{project_id}",
        "name": "All learnings for a project",
        "description": "Returns all learning entries for the specified project.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "metrics://project/{project_id}",
        "name": "Metrics summary for a project",
        "description": "Returns retrieval, logging, and consolidation metrics for the specified project.",
        "mimeType": "application/json",
    },
]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

def _call_tool(name: str, arguments: dict, paths: _Paths, ctx: dict) -> dict:
    if name == "retrieve_learnings":
        step = arguments.get("step", 1)
        components = arguments.get("components", []) or []
        files = arguments.get("files", []) or []
        domain = arguments.get("domain", "") or ""
        result = retrieve_core(step, components, files, domain, ctx, paths)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]}

    elif name == "log_learning":
        entry = arguments.get("entry", {})
        json_str = json.dumps(entry)
        result = log_core(json_str, paths, ctx)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]}

    elif name == "resolve_learning":
        ts = arguments.get("timestamp", "")
        result = resolve_core(ts, paths)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]}

    elif name == "get_stats":
        result = stats_core(paths, ctx=ctx)
        result.pop("exit_code", None)
        result.pop("status", None)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]}

    elif name == "consolidate":
        sprint_number = arguments.get("sprint_number")
        force = arguments.get("force", False)
        result = consolidate_core(sprint_number, False, force, paths, ctx)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]}

    elif name == "evaluate_prompt":
        summary = dict(arguments)
        result = evaluate_core(summary, paths, ctx)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]}

    elif name == "review_agents":
        step = arguments.get("step", 1)
        threshold = arguments.get("threshold", 10)
        result = review_agents_core(step, threshold, paths)
        result.pop("exit_code", None)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]}

    else:
        return {"isError": True, "content": [{"type": "text", "text": f"Unknown tool: {name}"}]}


# ---------------------------------------------------------------------------
# Resource dispatch
# ---------------------------------------------------------------------------

def _read_resource(uri: str, paths: _Paths) -> dict:
    if uri.startswith("learnings://project/"):
        entries = read_learnings(paths)
        return {"contents": [{"uri": uri, "mimeType": "application/json",
                              "text": json.dumps(entries, ensure_ascii=False, default=str)}]}

    elif uri.startswith("metrics://project/"):
        events = read_metrics(paths)
        r = _retrieval_stats([e for e in events if e.get("event_type") == "retrieval"])
        log_stats = _logging_stats([e for e in events if e.get("event_type") == "log"])
        c = _consolidation_stats([e for e in events if e.get("event_type") == "consolidate"])
        summary = {"total_events": len(events), "retrieval": r, "logging": log_stats, "consolidation": c}
        return {"contents": [{"uri": uri, "mimeType": "application/json",
                              "text": json.dumps(summary, ensure_ascii=False, default=str)}]}

    else:
        raise ValueError(f"Unknown resource URI: {uri}")


# ---------------------------------------------------------------------------
# JSON-RPC protocol over stdio
# ---------------------------------------------------------------------------

from agent_memory.engine_version import get_engine_version

_PROTOCOL_VERSION = "2024-11-05"
_SERVER_INFO = {"name": "agent-memory-engine", "version": get_engine_version()}
_CAPABILITIES = {
    "tools": {"listChanged": False},
    "resources": {"listChanged": False, "subscribe": False},
}


def _make_response(msg_id, result):
    return json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _make_error(msg_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return json.dumps({"jsonrpc": "2.0", "id": msg_id, "error": err})


# JSON-RPC error codes
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


def _handle_request(msg: dict, paths: _Paths, ctx: dict) -> str | None:
    msg_id = msg.get("id")
    method = msg.get("method", "")

    if method == "initialize":
        return _make_response(msg_id, {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": _CAPABILITIES,
            "serverInfo": _SERVER_INFO,
        })

    if method == "ping":
        return _make_response(msg_id, {})

    if method == "tools/list":
        return _make_response(msg_id, {"tools": TOOLS})

    if method == "tools/call":
        params = msg.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        try:
            result = _call_tool(tool_name, arguments, paths, ctx)
            return _make_response(msg_id, result)
        except Exception as e:
            return _make_response(msg_id, {"isError": True, "content": [{"type": "text", "text": str(e)}]})

    if method == "resources/list":
        return _make_response(msg_id, {"resources": []})

    if method == "resources/templates/list":
        return _make_response(msg_id, {"resourceTemplates": RESOURCE_TEMPLATES})

    if method == "resources/read":
        params = msg.get("params", {})
        uri = params.get("uri", "")
        try:
            result = _read_resource(uri, paths)
            return _make_response(msg_id, result)
        except Exception as e:
            return _make_error(msg_id, _INVALID_PARAMS, str(e))

    # Notifications (no id) — just acknowledge silently
    if msg_id is None:
        return None

    return _make_error(msg_id, _METHOD_NOT_FOUND, f"Method not found: {method}")


def run_server(memory_dir_arg: str | None = None) -> None:
    """Run the MCP server over stdio. Blocks until stdin is closed."""
    paths = _setup_paths(memory_dir_arg)
    ctx = _build_ctx(paths)

    if sys.stdin.encoding != "utf-8":
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(_make_error(None, _PARSE_ERROR, "Parse error") + "\n")
            sys.stdout.flush()
            continue

        if not isinstance(msg, dict) or "jsonrpc" not in msg:
            sys.stdout.write(_make_error(msg.get("id") if isinstance(msg, dict) else None,
                                         _INVALID_REQUEST, "Invalid Request") + "\n")
            sys.stdout.flush()
            continue

        response = _handle_request(msg, paths, ctx)
        if response is not None:
            sys.stdout.write(response + "\n")
            sys.stdout.flush()
