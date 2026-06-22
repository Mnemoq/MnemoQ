# Memory Engine GUI — Web Dashboard, Tauri Desktop, TUI

A progressive GUI strategy: build a FastAPI-backed web dashboard first (serves locally and becomes the cloud Pro tier later), wrap in Tauri for desktop feel, and add a Textual TUI for terminal users — all sharing one backend API.

---

## Architecture

```
                    ┌─────────────────────────────────┐
                    │     FastAPI Backend (one API)    │
                    │  src/dashboard/api.py            │
                    │  - REST endpoints (JSON)         │
                    │  - WebSocket for live updates    │
                    │  - Shared engine logic (no dup)  │
                    └──────────┬──────────┬────────────┘
                               │          │
                    ┌──────────┘          └──────────┐
                    │                                │
              ┌─────┴──────┐                  ┌──────┴───────┐
              │  Web UI    │                  │  TUI         │
              │  (browser) │                  │  (Textual)   │
              │  HTML+JS   │                  │  Terminal    │
              └─────┬──────┘                  └──────────────┘
                    │
              ┌─────┴──────┐
              │  Tauri     │
              │  (desktop) │
              │  webview   │
              └────────────┘
```

One API. Three frontends. Zero duplicated business logic.

---

## Phase 1: Web Dashboard (v1.20.2)

**Goal**: Full-featured browser-based dashboard served locally (free tier). This is the primary GUI and the foundation for the cloud Pro tier (roadmap 6.3). Ships as part of roadmap Phase 2 (v1.20), immediately after HTTP API (2.1) and MCP server (2.2).

### 1.1 Backend — FastAPI API Server

Implements roadmap 2.1 (HTTP API Server) using FastAPI instead of stdlib `http.server`. This gives us:
- Automatic request validation (Pydantic models)
- WebSocket support for live updates
- OpenAPI/Swagger docs at `/docs` (free API documentation)
- Async support for non-blocking operations

**Endpoints** (REST):
```
# Memory operations (shared with MCP server)
GET  /api/retrieve?step=N&components=A,B&domain=D
POST /api/log              { entry: {...} }
POST /api/resolve          { ts: "..." }
POST /api/update           { ts: "...", entry: {...} }
POST /api/consolidate      { sprint_number: N, force: false }
GET  /api/stats
GET  /api/learnings        ?resolved=false&domain=physics&type=bug_fix
GET  /api/learnings/{ts}
GET  /api/quarantine
GET  /api/archive          ?sprint=N

# Metrics & analytics (from advanced-metrics-system-686f76.md)
GET  /api/metrics          ?type=retrieval&since=2026-01-01
GET  /api/metrics/summary
GET  /api/metrics/health
GET  /api/metrics/alerts
GET  /api/metrics/retrieval-quality
GET  /api/metrics/lifecycle
GET  /api/metrics/agents
GET  /api/metrics/dedup
GET  /api/metrics/consolidation-quality
GET  /api/metrics/snapshot
GET  /api/metrics/quarantine
GET  /api/metrics/archive
GET  /api/metrics/config-tuning
GET  /api/metrics/recommendations
GET  /api/metrics/dashboard    (composite — all data for dashboard in one call)

# Cross-project
GET  /api/projects         (list all registered projects)
GET  /api/projects/{id}/metrics/summary
GET  /api/fleet            (fleet-wide report)

# Config
GET  /api/config
PUT  /api/config           (update config.json)

# System
GET  /api/health           (server health check)
WS   /ws/events            (WebSocket: live event stream)
```

**WebSocket events**: push real-time notifications on log, resolve, consolidate, alert triggered. Frontend subscribes and updates live without polling.

**Files**: `src/dashboard/api.py`, `src/dashboard/models.py` (Pydantic), `src/dashboard/__init__.py`

### 1.2 Frontend — Single-Page App

Lightweight SPA, no build step required. Vanilla JS + a few CDN libraries. Keeps the project dependency-free and easy to ship.

**Tech stack**:
- **HTML/CSS**: Hand-written, minimal. CSS Grid for layout. Dark theme default.
- **JavaScript**: Vanilla ES6+ (no React/Vue — too heavy for this scope)
- **Charts**: Chart.js via CDN (line charts for trends, doughnut for distributions, bar for comparisons)
- **Tables**: Sortable, filterable vanilla JS tables
- **Live updates**: WebSocket connection for real-time event feed
- **Icons**: Lucide icons via CDN

**Pages/Views** (tabbed navigation):

1. **Dashboard** (landing page)
   - Health score gauge (0-100, color-coded)
   - Key metrics cards: total entries, hit rate, quarantine rate, unresolved count
   - Sparkline trends (last 30 days) for retrievals, logs, consolidations
   - Active alerts panel (red/yellow/green)
   - Top recommendations panel

2. **Learnings Browser**
   - Filterable, sortable table of all entries
   - Filters: domain, type, severity, scope, resolved/unresolved, agent, step range
   - Search box (full-text on trigger/action/reason)
   - Click row → detail panel with full entry, access history, reinforcement count
   - Actions: resolve, edit, delete (with confirmation)
   - Export filtered set as JSONL/CSV

3. **Retrieval Explorer**
   - Input form: step, components, files, domain
   - Submit → shows scored results with match type, score breakdown
   - Score distribution histogram
   - "What was filtered out" panel (entries removed by retention/resolved)
   - Query history (last 20 queries with their results)

4. **Metrics Deep-Dive**
   - Sub-tabs: Retrieval Quality, Logging Patterns, Consolidation, Lifecycle, Agents, Dedup
   - Each sub-tab: charts + tables specific to that analysis
   - Time range selector (7d, 30d, 90d, all)
   - All data from the advanced-metrics plan's analysis functions

5. **Consolidation Console**
   - Current state: unresolved count, oldest entry, last consolidation date
   - "Run Sleep Cycle" button → shows consolidation report inline
   - Promotion candidates with approve/reject buttons
   - Contradictions list with detail
   - Stale entries with Git diff summary
   - Quarantine review queue
   - Archive history (sprint-by-sprint)

6. **Cross-Project Fleet**
   - Project selector (dropdown of all registered projects)
   - Side-by-side comparison table
   - Health score comparison bar chart
   - Domain overlap heatmap
   - Fleet-wide trends

7. **Settings**
   - Config editor (JSON with syntax highlighting)
   - Alert threshold configuration
   - Developer profile editor
   - Engine version info
   - Export/import data

**Files**: `src/dashboard/static/` (HTML, CSS, JS), `src/dashboard/templates/` (Jinja2 if needed)

### 1.3 Server Integration

- `agent-memory dashboard` or `python memory/filter.py --dashboard` starts the server
- Opens browser automatically (`webbrowser.open()`)
- Configurable port (default 8766, API on 8765)
- Graceful shutdown on Ctrl+C
- Serves static files from `src/dashboard/static/`
- API and dashboard on same port (FastAPI serves both)

**Files**: `filter.py` (new `--dashboard` flag), `src/dashboard/api.py`

### 1.4 Live Event Feed

WebSocket pushes events as they happen:
- New learning logged → toast notification + table refresh
- Learning resolved → status badge update
- Alert triggered → banner notification
- Consolidation completed → summary modal

Frontend maintains a rolling event log panel (collapsible, bottom of screen).

**Files**: `src/dashboard/api.py` (WebSocket handler), `src/dashboard/static/js/events.js`

---

## Phase 2: Tauri Desktop Wrapper (v2.2.1)

**Goal**: Wrap the web dashboard in a native desktop window. Same frontend, native shell. Implements roadmap 6.4 (Pro tier).

### 2.1 Tauri Setup

- Tauri 2.x (Rust backend, webview frontend)
- ~10MB binary, no Electron bloat
- Loads `http://localhost:8766` in a webview (or embeds static files directly)
- System tray icon with quick actions:
  - "Open Dashboard"
  - "Run Consolidation"
  - "View Alerts" (if any)
  - "Quit"

### 2.2 Native Enhancements

- **OS notifications**: alerts push as native notifications (not just in-browser toasts)
- **Auto-start option**: launch on system boot (configurable)
- **Menu bar**: File (export, import), View (theme toggle, refresh), Tools (consolidate, validate), Help
- **Dark/light theme**: follows system preference
- **Offline indicator**: shows when cloud sync is unavailable (Pro tier)

### 2.3 Packaging

- Windows: `.msi` installer + portable `.exe`
- macOS: `.dmg` (future)
- Linux: `.AppImage` (future)
- Auto-update via Tauri's updater plugin

**Files**: new `src/desktop/` (Tauri project), `src/desktop/src-tauri/` (Rust), `src/desktop/src/` (frontend symlinks or copies from dashboard/static)

---

## Phase 3: Terminal UI (v2.3.1)

**Goal**: Rich terminal dashboard for users who live in the terminal. Uses the same API backend. Implements roadmap 7.5.

### 3.1 Textual App

[Textual](https://textual.textualize.io/) — modern Python TUI framework. Supports:
- CSS-like styling
- Rich text rendering (colors, styles, tables)
- Mouse support (clickable tabs, scrollable tables)
- Responsive layout (works in any terminal size)

### 3.2 TUI Views

Mirrors the web dashboard's tab structure:
1. **Dashboard**: health score, key metrics, alert panel
2. **Learnings**: scrollable, filterable table
3. **Retrieval**: query form + results panel
4. **Metrics**: tabbed analysis views with ASCII charts
5. **Consolidation**: interactive review queue
6. **Fleet**: cross-project comparison table

### 3.3 Entry Point

- `agent-memory tui` or `python memory/filter.py --tui`
- Connects to the API server if running, otherwise starts it in background
- Full keyboard navigation (vim-style: j/k to scroll, enter to select, q to quit)

**Files**: new `src/tui/app.py`, `src/tui/views/` (one file per view)

---

## Implementation Priority

| Phase | Effort | Impact | When |
|-------|--------|--------|------|
| 1.1 FastAPI Backend | Medium | Critical | After HTTP API server (roadmap 2.1) |
| 1.2 Web Frontend | Medium | Critical | Immediately after 1.1 |
| 1.3 Server Integration | Low | High | With 1.1 |
| 1.4 Live Event Feed | Medium | High | After 1.2 |
| 2.1-2.3 Tauri Wrapper | Medium | Medium | After web dashboard is stable (Pro tier) |
| 3.1-3.3 TUI | Medium | Low | Post-Pro tier, community contribution |

---

## Dependencies

| Package | Purpose | Required? |
|---------|---------|-----------|
| `fastapi` | API server | Yes (replaces stdlib http.server) |
| `uvicorn` | ASGI server for FastAPI | Yes |
| `pydantic` | Request/response models | Yes (FastAPI dependency) |
| `chart.js` | Frontend charts | CDN, no pip install |
| `lucide` | Frontend icons | CDN, no pip install |
| `tauri` | Desktop wrapper | Only for Phase 2 |
| `textual` | Terminal UI | Only for Phase 3 |

**Note**: FastAPI + uvicorn + pydantic are the only new Python dependencies. Everything else is CDN-loaded or deferred to later phases.

---

## File Structure

```
src/
├── dashboard/
│   ├── __init__.py
│   ├── api.py              # FastAPI app, all endpoints
│   ├── models.py           # Pydantic request/response models
│   ├── static/
│   │   ├── index.html      # SPA entry point
│   │   ├── css/
│   │   │   └── dashboard.css
│   │   └── js/
│   │       ├── app.js      # Main app logic
│   │       ├── charts.js   # Chart.js wrappers
│   │       ├── events.js   # WebSocket event handling
│   │       └── components.js  # Reusable UI components
│   └── templates/          # Jinja2 (if needed for SSR)
├── desktop/                # Phase 2 (Tauri)
│   ├── src-tauri/
│   │   ├── Cargo.toml
│   │   ├── tauri.conf.json
│   │   └── src/main.rs
│   └── src/
│       └── main.js         # Tauri frontend entry
├── tui/                    # Phase 3 (Textual)
│   ├── __init__.py
│   ├── app.py
│   └── views/
│       ├── dashboard.py
│       ├── learnings.py
│       ├── retrieval.py
│       ├── metrics.py
│       ├── consolidation.py
│       └── fleet.py
└── engine/                 # Existing (unchanged)
```

---

## Relationship to Other Plans

- **Consolidated Roadmap** (`memory-engine-roadmap-consolidated.md`): This plan implements roadmap 2.1 (HTTP API → FastAPI), 2.3 (Web Dashboard — local free tier), 6.3 (Cloud-Hosted Dashboard — Pro tier), 6.4 (Tauri Desktop Wrapper), and 7.5 (Textual TUI). The MCP server (2.2) shares the same FastAPI backend and Pydantic models.
- **Advanced Metrics System** (`advanced-metrics-system-686f76.md`): The dashboard's Metrics Deep-Dive page is the visual frontend for all analysis functions in that plan. The `/api/metrics/*` endpoints wrap those analysis functions (roadmap 8.5).
- **The FastAPI backend replaces the stdlib `http.server`** planned in roadmap Phase 2.1 — same endpoints, better framework. MCP server (2.2) can reuse the same Pydantic models and handler logic.
