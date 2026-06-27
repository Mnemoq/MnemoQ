# MCP Integration Guide

The Model Context Protocol (MCP) is the primary integration path for AI agents to access MnemoQ's memory engine. This guide covers installation, client configuration, tool reference, and troubleshooting.

## Prerequisites

- Python 3.10+
- `pip install mnemoq` (or `pipx install mnemoq` for CLI-only users)
- A scaffolded project with a `memory/` directory:

```bash
mnemoq-scaffold ./my-project --defaults
```

## Quick Start

1. **Install** MnemoQ: `pip install mnemoq`
2. **Scaffold** a project: `mnemoq-scaffold ./my-project --defaults`
3. **Add** the server to your MCP client config (see [Client Configuration](#client-configuration) below)
4. **Restart** your MCP client so it picks up the new server

## Client Configuration

All clients use the same underlying command: `mnemoq-mcp`. The only difference is the config file location and JSON shape.

### Claude Desktop

Config file location:
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "mnemoq": {
      "command": "mnemoq-mcp",
      "args": ["--memory-dir", "/path/to/my-project/memory"]
    }
  }
}
```

### Cursor

Config file location:
- **Global (macOS/Linux):** `~/.cursor/mcp.json`
- **Global (Windows):** `%USERPROFILE%\.cursor\mcp.json`
- **Per-project:** `<project-root>/.cursor/mcp.json`

```json
{
  "mcpServers": {
    "mnemoq": {
      "command": "mnemoq-mcp",
      "args": ["--memory-dir", "/path/to/my-project/memory"]
    }
  }
}
```

### Windsurf

Config file location:
- **macOS/Linux:** `~/.codeium/windsurf/mcp_config.json`
- **Windows:** `%USERPROFILE%\.codeium\windsurf\mcp_config.json`
- **UI:** Settings → Cascade → MCP Servers

```json
{
  "mcpServers": {
    "mnemoq": {
      "command": "mnemoq-mcp",
      "args": ["--memory-dir", "/path/to/my-project/memory"]
    }
  }
}
```

### VS Code (Copilot)

Config file location:
- **Workspace:** `.vscode/mcp.json` (shareable via source control)
- **User profile:** Run `MCP: Open User Configuration` from the Command Palette

> **Note:** VS Code uses the `"servers"` key (not `"mcpServers"`) and requires a `"type"` field.

```json
{
  "servers": {
    "mnemoq": {
      "type": "stdio",
      "command": "mnemoq-mcp",
      "args": ["--memory-dir", "/path/to/my-project/memory"]
    }
  }
}
```

### Using `AGENT_MEMORY_DIR` instead of `--memory-dir`

All clients support passing environment variables. If you prefer not to hardcode the path in `args`:

```json
{
  "mcpServers": {
    "mnemoq": {
      "command": "mnemoq-mcp",
      "env": {
        "AGENT_MEMORY_DIR": "/path/to/my-project/memory"
      }
    }
  }
}
```

### Generic MCP Client (stdio)

Any MCP-compatible client that supports stdio transport can connect. The server config shape is:

```json
{
  "command": "mnemoq-mcp",
  "args": ["--memory-dir", "/path/to/my-project/memory"]
}
```

## Available Tools

| Tool | Description | Required Params | Optional Params |
|------|-------------|-----------------|-----------------|
| `retrieve_learnings` | Retrieve relevant learnings for the current task context. Returns warnings (critical issues) and patterns (architectural guidance), scored and ranked by relevance. | `step` (int, min 1) | `components` (string[]), `files` (string[]), `domain` (string) |
| `log_learning` | Log a new learning entry. Validates, checks for duplicates/semantic duplicates, and appends to memory. Returns status (added/duplicate/semantic_duplicate/conflict/quarantined). | `entry` (object — see below) | — |
| `resolve_learning` | Mark an existing learning entry as resolved by its timestamp. | `timestamp` (string, `YYYY-MM-DDTHH:MM:SSZ`) | — |
| `get_stats` | Get memory system statistics: total entries, unresolved/resolved counts, severity/type/scope breakdowns, reinforcement patterns, and sleep cycle status. | — | — |
| `consolidate` | Trigger a Sleep Cycle (consolidation): archives unresolved entries, generates promotion candidates, detects contradictions, and checks for stale entries. | — | `sprint_number` (int), `force` (bool) |

### `log_learning` entry fields

The `entry` object requires these fields:

| Field | Type | Constraint |
|-------|------|------------|
| `step` | int | See `max_step` in `memory/config.json` |
| `source_agent` | string | See `valid_source_agents` in `memory/config.json` |
| `type` | string | `bug_fix`, `optimization`, `architectural_pattern` |
| `domain` | string | See `valid_domains` in `memory/config.json` |
| `components` | string[] | Non-empty array of class/system names |
| `files_touched` | string[] | Non-empty array of file paths |
| `trigger` | string | Must start with "When" (case-insensitive) |
| `action` | string | Must contain "ALWAYS" or "NEVER" (case-insensitive) |
| `reason` | string | Mechanical explanation (non-empty) |
| `importance` | int | 1-10 |
| `severity` | string | `minor`, `major`, `critical` |

Optional fields: `scope` (`file`/`module`/`system`, default `file`), `debt_level` (`proper`/`workaround`/`temporary`, default `proper`), `verified` (bool, default `false`), `symptoms` (string, default `""`).

### Example: retrieve learnings

```json
{
  "step": 3,
  "components": ["UserCache", "AuthService"],
  "files": ["src/auth.py"],
  "domain": "backend"
}
```

### Example: log a learning

```json
{
  "entry": {
    "step": 3,
    "source_agent": "your-agent-name",
    "type": "bug_fix",
    "domain": "backend",
    "components": ["AuthService"],
    "files_touched": ["src/auth.py"],
    "trigger": "When JWT validation fails on expired tokens",
    "action": "ALWAYS check token expiry before signature verification",
    "reason": "PyJWT silently accepts expired tokens when verify_exp is not set",
    "importance": 8,
    "severity": "major"
  }
}
```

## Available Resources

| URI Template | Description |
|--------------|-------------|
| `learnings://project/{project_id}` | All learning entries for the project |
| `metrics://project/{project_id}` | Retrieval, logging, and consolidation metrics |

> **Note:** `{project_id}` is a URI placeholder required by the MCP protocol. The server reads from the memory directory configured via `--memory-dir` or `AGENT_MEMORY_DIR`, regardless of the value substituted in the URI.

## How It Works

- **Transport:** JSON-RPC 2.0 over stdio (no HTTP server required)
- **Protocol version:** `2024-11-05`
- **Dependencies:** pydantic only (no FastAPI/uvicorn needed for MCP)
- **Memory directory resolution** (in precedence order):
  1. `--memory-dir` CLI argument (highest priority)
  2. `AGENT_MEMORY_DIR` environment variable
  3. `./memory/` in the current working directory (lowest priority)

## Environment Variables

| Variable | Description |
|----------|-------------|
| `AGENT_MEMORY_DIR` | Path to the `memory/` directory containing `config.json` and `learnings.jsonl`. Used when `--memory-dir` is not passed. |

## Multiple Projects (Advanced)

To connect multiple projects, add separate entries in your client's `mcpServers` (or `servers`) object:

```json
{
  "mcpServers": {
    "mnemoq-work": {
      "command": "mnemoq-mcp",
      "args": ["--memory-dir", "/projects/work/memory"]
    },
    "mnemoq-personal": {
      "command": "mnemoq-mcp",
      "args": ["--memory-dir", "/projects/personal/memory"]
    }
  }
}
```

Each server instance operates independently on its own `memory/` directory.

## Troubleshooting

### "No memory directory found"

The server can't locate a `memory/` directory. Fix by either:
- Passing `--memory-dir /path/to/project/memory` in the client config
- Setting the `AGENT_MEMORY_DIR` environment variable to the memory directory path
- Running from a directory that contains a `memory/` subdirectory

### Server not appearing in client

- Verify the JSON config syntax is valid (no trailing commas, correct quotes)
- **Restart the client** after saving config changes
- Check that `mnemoq-mcp` is on your PATH: run `which mnemoq-mcp` (macOS/Linux) or `where mnemoq-mcp` (Windows) in a terminal
- On Windows, use forward slashes (`/`) or double backslashes (`\\`) in JSON paths

### Python not found

If `mnemoq-mcp` isn't on your PATH, use the full path to the executable or the module form:

```json
{
  "mcpServers": {
    "mnemoq": {
      "command": "python",
      "args": ["-m", "agent_memory.mcp_main", "--memory-dir", "/path/to/project/memory"]
    }
  }
}
```

### Server starts but tools don't work

- Verify the `memory/` directory contains `config.json` and `learnings.jsonl`
- Check that the path in `--memory-dir` points to the `memory/` directory itself, not the project root
- Run `mnemoq --stats` from the project root to confirm the memory system is functional

## Verification

### Without an MCP client (raw JSON-RPC)

Test the server directly via stdin/stdout. Run from your project root:

**bash (macOS/Linux):**

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | mnemoq-mcp --memory-dir ./memory
echo '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | mnemoq-mcp --memory-dir ./memory
```

**PowerShell (Windows):**

```powershell
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | mnemoq-mcp --memory-dir ./memory
'{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | mnemoq-mcp --memory-dir ./memory
```

The `initialize` call should return a JSON response with `protocolVersion`, `capabilities`, and `serverInfo`. The `tools/list` call should return all 5 tools.

### With an MCP client

After adding the config and restarting your client:
1. Look for `mnemoq` in the client's MCP server list
2. Verify that 5 tools are available: `retrieve_learnings`, `log_learning`, `resolve_learning`, `get_stats`, `consolidate`
3. Call `get_stats` — it should return JSON with memory statistics
