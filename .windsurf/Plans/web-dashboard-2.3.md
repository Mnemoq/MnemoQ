# Web Dashboard 2.3 — Three-Phase Implementation Plan

Implements the web dashboard (section 2.3 of tier-2-expanded.md) in three phases: backend + core frontend tabs, deep-dive tabs, and operational tabs, using a thin analysis layer on existing stats (Option C), a dashboard router in `src/engine/` with static files in `src/dashboard/` (Option C), and WebSocket events via post-call hook + file watcher for CLI events (Option D).

## Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Analysis functions | Thin layer on existing `_retrieval_stats`, `_logging_stats`, `_consolidation_stats` + `learnings.jsonl` harvesting | YAGNI — v2.7.1 will replace with richer analysis |
| Endpoint placement | `src/engine/dashboard_api.py` (router) + `src/dashboard/static/` (frontend) | AGENTS.md compliant — logic in `engine/`, UI in `dashboard/` |
| WebSocket events | Post-call hook in API layer (instant) + background `metrics.jsonl` mtime poller (CLI events, 2s interval) | API calls are instant; CLI events are a bonus |

## File Layout

```
src/
  engine/
    dashboard_api.py     # FastAPI APIRouter with all 15 metrics endpoints + WS handler
    analysis.py          # Thin analysis functions (health score, snapshot, recommendations, etc.)
    server.py            # Modified: create_app() mounts dashboard router + static files when --dashboard
  dashboard/
    __init__.py
    static/
      index.html         # SPA shell with tabbed navigation
      css/
        style.css        # Dark theme, CSS Grid layout
      js/
        app.js           # Router, tab switching, shared state
        api.js           # Fetch wrapper, WebSocket client
        dashboard.js     # Dashboard tab
        learnings.js     # Learnings Browser tab
        settings.js      # Settings tab
        retrieval.js     # Retrieval Explorer tab (Phase 2)
        metrics.js       # Metrics Deep-Dive tab (Phase 2)
        consolidation.js # Consolidation Console tab (Phase 3)
        fleet.js         # Cross-Project Fleet tab (Phase 3)
        events.js        # WebSocket event handler, toast notifications, event log panel
```

## Phase 1: Backend + Core Frontend (Dashboard, Learnings Browser, Settings)

### 1A. Backend — `src/engine/analysis.py`

Thin analysis functions using existing stats + direct `learnings.jsonl` harvesting. No new instrumentation.

**Functions:**

- `health_score(paths, ctx) -> dict` — Composite 0-100 score from existing stats:
  - Retrieval effectiveness (30%): hit rate, avg top score from `_retrieval_stats`
  - Entry quality (25%): verified ratio, proven ratio, debt distribution from `stats_core`
  - Logging discipline (20%): quarantine rate, duplicate rate, conflict rate from `_logging_stats`
  - Consolidation hygiene (15%): unresolved count, sleep cycle status from `stats_core`
  - Coverage (10%): component count, domain count from `read_learnings`
  - Returns: `{score, components: {retrieval: N, quality: N, logging: N, consolidation: N, coverage: N}}`

- `snapshot(paths) -> dict` — Harvest `learnings.jsonl` directly:
  - Entry age distribution (by step_diff buckets)
  - Component/domain/file coverage maps
  - Orphan entries (access_count=0, reinforcement_count=0)
  - Hot entries (access_count >= 5)
  - Proven entries (reinforcement_count >= 5)
  - Zombie entries (unresolved + access_count=0 + old)
  - Debt distribution, verified ratio, severity × type cross-tab

- `retrieval_quality(paths) -> dict` — From `_retrieval_stats` + event data:
  - Hit rate by component (group events by query_components)
  - Hit rate by domain (group by query_domain)
  - Score trend (avg top_score over time, daily buckets)
  - Empty query analysis (component/domain combos returning nothing)
  - Result diversity (unique entry timestamps returned)

- `lifecycle(paths) -> dict` — From `read_learnings`:
  - Entry aging curve (step_diff distribution)
  - Access distribution (access_count histogram)
  - Reinforcement distribution
  - Zombie detection (unresolved + old + never accessed)
  - Promotion velocity (steps from log to reinforcement_count >= 5)

- `agent_quality(paths) -> dict` — From `_logging_stats` + `read_learnings`:
  - Per-agent: added rate, quarantine rate, conflict rate, retrieval relevance, verification rate
  - Severity distribution per agent
  - Domain expertise per agent
  - Composite agent score

- `dedup_analysis(paths) -> dict` — From `_logging_stats`:
  - Duplicate rate, semantic duplicate rate, conflict rate
  - Duplicate trends over time
  - Agents producing most duplicates

- `consolidation_quality(paths) -> dict` — From `_consolidation_stats`:
  - Promotion candidate rate, contradiction rate, stale rate
  - Consolidation frequency
  - Archive growth trend

- `alerts(paths, ctx) -> dict` — Rule-based on existing stats:
  - Unresolved count > threshold → warning
  - Quarantine rate > 20% → warning
  - Hit rate < 30% → warning
  - Sleep cycle overdue → warning
  - No consolidation in 30+ days → warning
  - Returns: `{alerts: [{severity, message, metric, threshold, actual}], count}`

- `recommendations(paths, ctx) -> dict` — Actionable suggestions:
  - "Run consolidation" if unresolved > 50
  - "Review quarantine" if quarantine count > 5
  - "Log more learnings for component X" if coverage gap detected
  - "Check retrieval threshold" if hit rate < 30%
  - Returns: `{recommendations: [{priority, action, reason}], count}`

- `config_tuning(paths, ctx) -> dict` — Config health analysis:
  - Current tuning values vs defaults
  - Score threshold effectiveness (hit rate at current threshold)
  - Retention window analysis (entries filtered by retention)
  - Returns: `{current, defaults, recommendations}`

- `dashboard_composite(paths, ctx) -> dict` — Single call for Dashboard tab:
  - Health score + component breakdown
  - Key metrics cards (total entries, unresolved, hit rate, last consolidation)
  - 30-day sparkline data (from `_trend_stats`)
  - Active alerts
  - Top 3 recommendations

**Key design rule:** Each function takes `paths` (and `ctx` where needed), returns a plain dict. No pydantic models — consistent with `*_core` functions per AGENTS.md.

### 1B. Backend — `src/engine/dashboard_api.py`

FastAPI `APIRouter` with all 15 metrics endpoints + WebSocket handler.

**Endpoints (all under `/api/`):**

| Method | Path | Source function |
|--------|------|----------------|
| GET | `/api/metrics/summary` | `_retrieval_stats` + `_logging_stats` + `_consolidation_stats` |
| GET | `/api/metrics/health` | `analysis.health_score()` |
| GET | `/api/metrics/alerts` | `analysis.alerts()` |
| GET | `/api/metrics/retrieval-quality` | `analysis.retrieval_quality()` |
| GET | `/api/metrics/lifecycle` | `analysis.lifecycle()` |
| GET | `/api/metrics/agents` | `analysis.agent_quality()` |
| GET | `/api/metrics/dedup` | `analysis.dedup_analysis()` |
| GET | `/api/metrics/consolidation-quality` | `analysis.consolidation_quality()` |
| GET | `/api/metrics/snapshot` | `analysis.snapshot()` |
| GET | `/api/metrics/quarantine` | `_read_raw_jsonl(paths.quarantine_path)` + categorization |
| GET | `/api/metrics/archive` | Read `archive/` directory + per-sprint analysis |
| GET | `/api/metrics/config-tuning` | `analysis.config_tuning()` |
| GET | `/api/metrics/recommendations` | `analysis.recommendations()` |
| GET | `/api/metrics/dashboard` | `analysis.dashboard_composite()` |
| GET | `/api/learnings` | `read_learnings(paths)` with optional filters (domain, type, severity, resolved, agent, step_min, step_max, q) |
| GET | `/api/learnings/{ts}` | Single entry lookup from `read_learnings` |
| GET | `/api/projects` | `_load_project_paths()` → project list with IDs |
| GET | `/api/projects/{id}/metrics/summary` | Cross-project metrics for one project |
| GET | `/api/fleet` | `_read_all_project_metrics()` → fleet comparison |
| GET | `/api/config` | Read `config.json` |
| PUT | `/api/config` | Validate + atomic write `config.json` (using `write_learnings` pattern) |
| WS | `/ws/events` | WebSocket handler (see 1D below) |

**Also:** `GET /api/metrics` (existing) gets `type` filter param added (retrieval/log/consolidate/stats/review_agents).

**Router factory:**
```python
def create_dashboard_router(paths, ctx) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["dashboard"])
    # ... all endpoints ...
    return router
```

### 1C. Backend — `src/engine/server.py` modifications

`create_app()` gains a `dashboard: bool = False` param:

```python
def create_app(paths, ctx, api_key=None, dashboard=False):
    app = FastAPI(...)
    # ... existing endpoints (unchanged) ...

    if dashboard:
        from engine.dashboard_api import create_dashboard_router
        from fastapi.staticfiles import StaticFiles
        router = create_dashboard_router(paths, ctx)
        app.include_router(router)
        static_dir = os.path.join(os.path.dirname(__file__), "..", "dashboard", "static")
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app
```

### 1D. Backend — WebSocket event system

**Post-call hook (API events, instant):**

In `dashboard_api.py`, a connection manager:
```python
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []
    async def connect(self, ws): ...
    def disconnect(self, ws): ...
    async def broadcast(self, event: dict): ...
```

In `server.py`, wrap the existing `log_entry`, `resolve_entry`, `consolidate` endpoints to broadcast after successful `*_core` calls. The `create_app` function passes the connection manager to both the core endpoints and the dashboard router.

**File watcher (CLI events, 2s poll):**

Background `asyncio.Task` in the dashboard router that:
1. Records `metrics.jsonl` mtime + size on startup
2. Every 2s, checks mtime — if changed, reads new lines since last offset
3. Parses each new event, broadcasts as WS message
4. Deduplicates by event timestamp (API hook may have already sent it)

Events pushed to clients:
```json
{"event": "log", "data": {"status": "added", "ts": "..."}}
{"event": "resolve", "data": {"ts": "...", "status": "resolved"}}
{"event": "consolidate", "data": {"sprint": 3, "archived": 12}}
{"event": "alert", "data": {"severity": "warning", "message": "..."}}
```

### 1E. Frontend — `src/dashboard/static/`

**`index.html`** — SPA shell:
- Tab bar: Dashboard, Learnings Browser, Retrieval Explorer (disabled), Metrics Deep-Dive (disabled), Consolidation Console (disabled), Cross-Project Fleet (disabled), Settings
- Dark theme, CSS Grid layout
- Collapsible event log panel at bottom
- CDN: Chart.js, Lucide icons
- No build step — all JS loaded via `<script>` tags

**`css/style.css`** — Dark theme default:
- CSS custom properties for colors
- Grid-based layout
- Responsive (works on 1280px+)
- Card, table, badge, toast, modal components

**`js/app.js`** — Tab router:
- Hash-based routing (`#dashboard`, `#learnings`, `#settings`)
- Lazy-loads tab modules (calls init function when tab first activated)
- Shared state (current project, config)

**`js/api.js`** — API client:
- `fetchJSON(path)` wrapper with error handling
- `fetchJSON(path, {method: 'POST', body})` for writes
- WebSocket client: connects to `ws://localhost:PORT/ws/events`, auto-reconnect, dispatches to `events.js`
- Export helpers: `downloadJSONL(data)`, `downloadCSV(data)`

**`js/dashboard.js`** — Dashboard tab:
- Health score gauge (semi-circle, 0-100, color: green/yellow/red)
- 4 metric cards: Total Entries, Unresolved, Hit Rate, Last Consolidation
- 30-day sparkline (retrievals + logs per day, dual-line Chart.js)
- Active alerts panel (sorted by severity)
- Top 3 recommendations
- Calls `GET /api/metrics/dashboard` (single composite call)

**`js/learnings.js`** — Learnings Browser tab:
- Filter bar: domain (dropdown), type (dropdown), severity (dropdown), resolved (toggle), agent (dropdown), step range (min/max), full-text search
- Sortable table: timestamp, step, agent, type, domain, severity, scope, resolved, access_count
- Click row → detail panel (right side or modal): full entry JSON, trigger/action/reason
- Actions: Resolve (calls `POST /api/resolve`), Export as JSONL, Export as CSV
- Pagination (client-side, 50 per page)
- Calls `GET /api/learnings` with filter params

**`js/settings.js`** — Settings tab:
- Config editor: JSON textarea with syntax highlighting (basic — monospace + validation)
- Save button → `PUT /api/config` (validates before sending)
- Alert threshold config (derived from config.json fields)
- Engine version info (`GET /api/health`)
- Export/import data buttons

**`js/events.js`** — Event system:
- Toast notifications (bottom-right, auto-dismiss after 5s)
- Event log panel (collapsible, bottom of screen, rolling 100 events)
- Table refresh on `log`/`resolve` events (if Learnings tab active)
- Health score refresh on `alert` events (if Dashboard tab active)

### 1F. `filter.py` — `--dashboard` flag

Already wired in `filter.py:665`. Modify to pass `dashboard=True`:

```python
if args.serve or args.dashboard:
    ...
    app = create_app(_get_paths(), _build_ctx(), api_key=api_key, dashboard=args.dashboard)
    if args.dashboard:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{args.port}")
    ...
```

### Phase 1 Verification

- `python src/filter.py --dashboard --memory-dir memory` starts server, opens browser
- Dashboard tab renders health gauge, metric cards, sparkline, alerts, recommendations
- Learnings Browser tab renders table, filters work, detail panel shows entry
- Settings tab shows config, save works, validation catches bad JSON
- WebSocket connects, toast appears when `POST /api/log` is called via curl
- All 15 metrics endpoints return JSON (verified with curl)
- Existing `--serve` mode still works (no dashboard, no static files)

---

## Phase 2: Retrieval Explorer + Metrics Deep-Dive

### 2A. Frontend — `js/retrieval.js`

- Input form: step (number), components (comma-separated), files (comma-separated), domain (dropdown)
- Submit → `GET /api/retrieve?step=N&components=...&files=...&domain=...`
- Results panel: scored entries with match type + score breakdown (sorted by score)
- Score distribution histogram (Chart.js bar chart, 0.0-1.0 buckets)
- Filtered-out panel: entries that didn't make the cut (below threshold or filtered by retention)
- Query history: last 20 queries (stored in localStorage), click to re-run

### 2B. Frontend — `js/metrics.js`

- Sub-tab navigation: Retrieval Quality, Logging Patterns, Consolidation, Lifecycle, Agents, Dedup
- Time range selector: 7d / 30d / 90d / all (passed as `?since=` param)
- **Retrieval Quality**: hit rate by component (bar chart), hit rate by domain (bar chart), score trend (line chart), empty query table
- **Logging Patterns**: outcome distribution (pie chart), agent contributions (bar chart), domain distribution (bar chart), quarantine reasons (bar chart)
- **Consolidation**: sprint history (table), promotion/contradiction/stale counts over time (line chart)
- **Lifecycle**: entry aging curve (histogram), access distribution (histogram), zombie entries table
- **Agents**: per-agent quality scores (table with sparkline), severity distribution heatmap
- **Dedup**: duplicate rate trend (line chart), duplicate by agent (bar chart), conflict list
- Each sub-tab calls its respective `/api/metrics/*` endpoint

### Phase 2 Verification

- Retrieval Explorer: submit query, see scored results + histogram
- Metrics Deep-Dive: all 6 sub-tabs render with data from endpoints
- Time range selector changes data on all sub-tabs
- Query history persists across page reloads

---

## Phase 3: Consolidation Console + Cross-Project Fleet

### 3A. Frontend — `js/consolidation.js`

- Current state card: unresolved count, last consolidation sprint, sleep cycle status
- "Run Sleep Cycle" button → `POST /api/consolidate` (with sprint number input + force toggle)
- Promotion candidates: table with approve/reject actions (approve = keep in learnings, reject = resolve)
- Contradictions list: side-by-side entry comparison
- Stale entries: table with Git diff link (if commit hash available)
- Quarantine review queue: table with "Re-log" action (opens Learnings Browser pre-filled)
- Archive history: list of sprint archives with entry counts (from `GET /api/archive`)

### 3B. Frontend — `js/fleet.js`

- Project selector dropdown (from `GET /api/projects`)
- Side-by-side comparison table: health score, total entries, hit rate, unresolved, last consolidation
- Health score bar chart (one bar per project)
- Domain overlap heatmap (shared domains across projects)
- Fleet-wide trends (retrievals + logs across all projects, 30-day line chart)
- Calls `GET /api/fleet` for composite data

### Phase 3 Verification

- Consolidation Console: run sleep cycle, see results, approve/reject promotion candidates
- Cross-Project Fleet: project selector works, comparison table renders, bar chart shows health scores
- All 7 tabs functional, no disabled tabs

---

## Dependency Summary

| Phase | New files | Modified files | Depends on |
|-------|-----------|----------------|------------|
| 1 | `analysis.py`, `dashboard_api.py`, `dashboard/static/*` | `server.py`, `filter.py` | Existing `metrics.py`, `io.py`, `handlers.py` |
| 2 | `retrieval.js`, `metrics.js` | `index.html` (enable tabs) | Phase 1 endpoints |
| 3 | `consolidation.js`, `fleet.js` | `index.html` (enable tabs) | Phase 1 endpoints |

## Version Bump

- Phase 1 complete → bump VERSION to `1.20.2`
- Phase 2 complete → bump VERSION to `1.20.3`
- Phase 3 complete → bump VERSION to `1.20.4`
