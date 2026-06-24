# Tier 2 — Distribution & Access (v1.20 – v1.21)

Make the engine accessible from any tool. This unlocks the GUI dashboard and SDK.

---

## 2.1 HTTP API Server (v1.20.0)

**Problem**: CLI-only interface requires subprocess calls. No way to query from other tools.

- `--serve` mode: HTTP server using FastAPI (supports GUI dashboard integration)
- `--dashboard` mode: same FastAPI process + serves static dashboard files + auto-opens browser (see 2.3)
- Single process, single port (default 8765). `--dashboard` is `--serve` + static file mounting + `webbrowser.open()`. No second port needed — API and dashboard share the same origin, eliminating CORS concerns
- Lazy import: `--serve`/`--dashboard` import FastAPI at call time. If `agent-memory[api]` not installed, print: `pip install agent-memory[api] to use --serve`
- Endpoints (all under `/api/` prefix):
  - `GET /api/retrieve?step=N&components=A,B&domain=D` → JSON array of learnings
  - `POST /api/log` → validate + append
  - `POST /api/update` → amend existing entry (maps to CLI `--update`)
  - `POST /api/resolve` → mark resolved
  - `GET /api/stats` → summary statistics
  - `GET /api/metrics?type=retrieval&since=2026-01-01` → filtered metrics
  - `POST /api/consolidate` → trigger sleep cycle (body: `{sprint_number, force}`)
  - `GET /api/quarantine` → quarantined entries
  - `GET /api/archive?sprint=N` → archived entries
- JSON request/response, shared code path via handler `*_core` functions (see below)
- Configurable port (default 8765), localhost-only binding by default
- API key auth via `config.json: api_key` (optional, disabled by default for local use)
- FastAPI chosen over stdlib `http.server` for: automatic request validation (Pydantic models), WebSocket support for live updates, OpenAPI/Swagger docs at `/docs`, async support for non-blocking operations

### Dependencies

```toml
# pyproject.toml
[project.dependencies]
pydantic = ">=2.0"          # ~2MB, pure-python. Shared models for CLI, MCP, SDK, HTTP

[project.optional-dependencies]
api = ["fastapi>=0.100", "uvicorn>=0.23"]   # pydantic comes free via fastapi
embeddings = ["sentence-transformers"]
```

- `pip install agent-memory` → CLI + MCP server + SDK (pydantic only)
- `pip install agent-memory[api]` → adds HTTP server + web dashboard
- pydantic is the project's first required dependency. Justified: three of five Tier 2 features (HTTP API, MCP, SDK) use it for shared models. ~2MB pure-python, no compiled extensions

### Handler Refactoring — `*_core` Functions

Existing handlers print to stdout and return exit codes. HTTP server needs structured JSON responses. Split each handler into a thin CLI wrapper + a `*_core` function returning a dict:

```python
# handlers.py

def handle_log(json_str, paths, ctx):
    """CLI wrapper — prints result, returns exit code."""
    result = log_core(json_str, paths, ctx)
    print(result["message"])
    return result["exit_code"]

def log_core(json_str, paths, ctx):
    """Shared logic — returns dict, no printing."""
    # existing validation/dedup/append logic
    return {"exit_code": 0, "status": "logged", "entry": entry, "dedup": dedup_result}
```

- Server calls `log_core(...)` directly, returns result as JSON response
- CLI `handle_log` stays a thin print wrapper — no CLI behavior change
- Scope: 4 handlers need splitting: `handle_log` → `log_core`, `handle_update` → `update_core`, `handle_resolve` → `resolve_core`, `handle_stats` → `stats_core`
- `consolidation.run_consolidate` likely already returns a report dict — verify and adjust if needed

### Shared Models

New `src/engine/models.py` defines pydantic `BaseModel` classes for request/response:
- `LearningEntry` — full entry schema (fields match `validation.py` required fields)
- `LogRequest` / `LogResponse` — wrapper for `POST /api/log`
- `RetrieveRequest` / `RetrieveResponse` — wrapper for `GET /api/retrieve`
- `ResolveRequest` — `{ts: str}`
- `ConsolidateRequest` — `{sprint_number: int, force: bool}`
- `StatsResponse` — summary statistics shape
- `ErrorResponse` — `{code: str, message: str, suggested_action: str, entry_ref: str | None}`

These models are shared across HTTP server (2.1), MCP server (2.2), and SDK (2.4). Single source of truth for schema — `validation.py` can delegate to model validation over time.

### HTTP Status Codes

| Status | When |
|--------|------|
| 200 | Success (GET, successful POST) |
| 201 | Created (successful `POST /api/log`) |
| 400 | Bad request (malformed JSON, missing body) |
| 404 | Entry not found (`/api/learnings/{ts}`, `POST /api/resolve` with unknown ts) |
| 409 | Conflict (duplicate/quarantined entry, conflict detected) |
| 401 | Unauthorized (API key required but missing/invalid) |
| 422 | Validation error (pydantic field validation failed) |
| 500 | Internal server error |

Error response body (all non-2xx): `ErrorResponse` model with `code`, `message`, `suggested_action`, `entry_ref`

### CLI Integration

`filter.py` argparse changes:
- Add `--serve` flag (mutually exclusive with `--step`, `--log`, `--resolve`, etc.)
- Add `--dashboard` flag (implies `--serve` + static files)
- Add `--port` flag (default 8765)
- Lazy import FastAPI inside the `--serve`/`--dashboard` branch, not at module top

**Files**: new `src/engine/server.py`, new `src/engine/models.py`, `filter.py`, `handlers.py` (split into `*_core`), `config.json`, `pyproject.toml`

---

## 2.2 MCP Server (v1.20.1)

**Problem**: No standard protocol for agents to access memory.

- Implement MCP server interface (JSON-RPC over stdio)
- Tools exposed:
  - `retrieve_learnings(step, components, files, domain)` → relevant learnings
  - `log_learning(entry_json)` → validation result + dedup status
  - `resolve_learning(timestamp)` → success/failure
  - `get_stats()` → memory summary
  - `consolidate(sprint_number)` → consolidation report
- Resources exposed:
  - `learnings://project/<id>` → all learnings
  - `metrics://project/<id>` → metrics summary
- Works with Claude Desktop, Cursor, Windsurf, VS Code, and any MCP client
- Entry point: `python -m agent_memory.mcp` or `agent-memory mcp` (after packaging)
- Auto-discovery: reads `AGENT_MEMORY_DIR` env or discovers `memory/` in cwd
- Shares same pydantic models (`src/engine/models.py`) and `*_core` handler functions as FastAPI backend (2.1)
- No FastAPI/uvicorn dependency — MCP server uses stdlib JSON-RPC only. pydantic (already a required dep) handles request/response validation

**Files**: new `src/engine/mcp_server.py`, new `src/mcp_main.py`

---

## 2.3 Web Dashboard (v1.20.2)

**Problem**: Stats and consolidation are CLI-only. No visual exploration of memory state.

- FastAPI-backed web dashboard served locally (free tier)
- 7 tabbed views: Dashboard, Learnings Browser, Retrieval Explorer, Metrics Deep-Dive, Consolidation Console, Cross-Project Fleet, Settings
- Vanilla JS SPA + Chart.js via CDN (no build step, no npm)
- WebSocket live event feed: log/resolve/consolidate/alert notifications
- `agent-memory dashboard` starts server on localhost:8765 (same port as `--serve`), auto-opens browser
- Cloud-hosted version for Pro tier (same UI, cloud-backed data)

### 2.3.1 Backend — FastAPI API Server

Implements roadmap 2.1 using FastAPI. Full endpoint list:

- **Memory operations**: `GET /api/retrieve`, `POST /api/log`, `POST /api/resolve`, `POST /api/update`, `POST /api/consolidate`, `GET /api/stats`, `GET /api/learnings`, `GET /api/learnings/{ts}`, `GET /api/quarantine`, `GET /api/archive`
- **Metrics & analytics**: `GET /api/metrics` (with type/since filters), `/api/metrics/summary`, `/api/metrics/health`, `/api/metrics/alerts`, `/api/metrics/retrieval-quality`, `/api/metrics/lifecycle`, `/api/metrics/agents`, `/api/metrics/dedup`, `/api/metrics/consolidation-quality`, `/api/metrics/snapshot`, `/api/metrics/quarantine`, `/api/metrics/archive`, `/api/metrics/config-tuning`, `/api/metrics/recommendations`, `/api/metrics/dashboard` (composite)
- **Cross-project**: `GET /api/projects`, `GET /api/projects/{id}/metrics/summary`, `GET /api/fleet`
- **Config**: `GET /api/config`, `PUT /api/config`
- **System**: `GET /api/health`, `WS /ws/events` (WebSocket: live event stream)
- WebSocket events: push real-time notifications on log, resolve, consolidate, alert triggered

**Files**: `src/dashboard/api.py`, `src/engine/models.py` (shared pydantic models, defined in 2.1), `src/dashboard/__init__.py`

### 2.3.2 Frontend — Single-Page App

Lightweight SPA, no build step. Vanilla JS + CDN libraries.

**Tech stack**: HTML/CSS (CSS Grid, dark theme default), Vanilla ES6+ (no React/Vue), Chart.js via CDN, Lucide icons via CDN, WebSocket for live updates

**Pages/Views** (tabbed navigation):

1. **Dashboard**: Health score gauge (0-100), key metrics cards, sparkline trends (30d), active alerts panel, top recommendations
2. **Learnings Browser**: Filterable/sortable table (domain, type, severity, scope, resolved, agent, step range), full-text search, detail panel, actions (resolve/edit/delete), export as JSONL/CSV
3. **Retrieval Explorer**: Input form (step, components, files, domain), scored results with match type + score breakdown, score distribution histogram, filtered-out panel, query history (last 20)
4. **Metrics Deep-Dive**: Sub-tabs (Retrieval Quality, Logging Patterns, Consolidation, Lifecycle, Agents, Dedup), time range selector (7d/30d/90d/all), data from advanced-metrics plan analysis functions
5. **Consolidation Console**: Current state, "Run Sleep Cycle" button, promotion candidates with approve/reject, contradictions list, stale entries with Git diff, quarantine review queue, archive history
6. **Cross-Project Fleet**: Project selector, side-by-side comparison table, health score bar chart, domain overlap heatmap, fleet-wide trends
7. **Settings**: Config editor (JSON with syntax highlighting), alert threshold config, developer profile editor, engine version info, export/import data

**Files**: `src/dashboard/static/` (HTML, CSS, JS), `src/dashboard/templates/` (Jinja2 if needed)

### 2.3.3 Server Integration

- `agent-memory dashboard` or `python memory/filter.py --dashboard` starts the server
- `--dashboard` is `--serve` + static file mounting + `webbrowser.open()` — same FastAPI process, same port (default 8765)
- No second port. No CORS (same origin). API endpoints at `/api/*`, dashboard at `/`
- Opens browser automatically (`webbrowser.open()`)
- Graceful shutdown on Ctrl+C
- Serves static files from `src/dashboard/static/`

**Files**: `filter.py` (new `--dashboard` flag, reuses `--serve` infrastructure from 2.1), `src/dashboard/api.py`

### 2.3.4 Live Event Feed

WebSocket pushes events as they happen:
- New learning logged → toast notification + table refresh
- Learning resolved → status badge update
- Alert triggered → banner notification
- Consolidation completed → summary modal
- Frontend maintains a rolling event log panel (collapsible, bottom of screen)

**Files**: `src/dashboard/api.py` (WebSocket handler), `src/dashboard/static/js/events.js`

---

## 2.4 Python SDK (v1.21.0)

**Problem**: Developers integrating into custom agents need a programmatic API, not subprocess calls.

- `pip install agent-memory` → `from agent_memory import MemoryClient`
- `MemoryClient(memory_dir=...)` with methods: `.retrieve(...)`, `.log(...)`, `.resolve(...)`, `.stats()`, `.consolidate()`
- Thin wrapper over the HTTP API (when server running) or direct file access (when local)
- Async support: `AsyncMemoryClient` with `aiohttp` for non-blocking retrieval
- Type hints + shared pydantic models from `src/engine/models.py` (same models as HTTP server and MCP server)
- Client-side validation: pydantic catches type errors before network call (when using HTTP mode) or before file write (when using local mode)

**Files**: new `src/sdk/`, `pyproject.toml` (packaging)

---

## 2.5 pip Packaging (v1.21.1)

**Problem**: Currently requires manual file copying. No version management.

- Proper `pyproject.toml` with `console_scripts` entry point: `agent-memory`
- `agent-memory --step N` replaces `python memory/filter.py --step N`
- Shim mode preserved: existing projects keep working via `python memory/filter.py`
- `pip install agent-memory` for new users; `agent-memory scaffold <project>` for setup
- Published to PyPI (free tier)
- Optional: `brew install agent-memory` (Homebrew formula)

**Files**: `pyproject.toml`, new `src/cli.py` (thin entry point)
