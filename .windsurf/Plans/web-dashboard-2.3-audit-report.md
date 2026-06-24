# Web Dashboard 2.3 — Audit Report

Date: 2026-06-24
Scope: Dashboard backend (`src/engine/server.py`, `dashboard_api.py`, `analysis.py`, `metrics.py`) and frontend (`src/dashboard/static/*`)
Test run: `python -m pytest tests/ -q` → 125 passed

## Summary

The 2.3 web dashboard implementation is complete and aligns with the phase plan and audit follow-up plan. All six follow-up tasks are implemented, the 7 dashboard gaps are closed, and the full test suite passes.

## Findings

### Backend

- **Lifespan / file watcher**: `server.py` uses an `asynccontextmanager` lifespan to start/stop `event_hub.watch_metrics_file()` instead of the deprecated `@router.on_event("startup")`. No deprecation warnings observed.
- **WebSocket deduplication**: `EventHub` maintains a rolling `_seen` set (`_SEEN_MAX = 500`) keyed by event type + status + timestamp/entry ts. API-hook events and file-watcher events share the same key space, preventing duplicates.
- **Alert broadcasting**: `_check_and_broadcast_alerts()` runs after `log`, `resolve`, and `consolidate` when dashboard is enabled.
- **Dynamic version**: `/api/health` reads the first line of `VERSION` (currently `1.20.5`).
- **Atomic config update**: `PUT /api/config` validates editable fields, writes to `config.json.tmp`, then `os.replace()` into `config.json`. Temp file is cleaned up on failure.
- **Metrics endpoints**: All planned endpoints exist (`/api/metrics/health`, `/alerts`, `/retrieval-quality`, `/lifecycle`, `/agents`, `/dedup`, `/consolidation-quality`, `/snapshot`, `/quarantine`, `/archive`, `/config-tuning`, `/recommendations`, `/dashboard`, `/learnings`, `/projects`, `/fleet`, `/config`).
- **`/api/learnings` filters**: server-side filters for `domain`, `type`, `severity`, `resolved`, `agent`, `step_min`, `step_max`, `q` are implemented.
- **`/api/metrics` type filter**: `type` query param filters to `retrieval`/`log`/`consolidate`.

### Frontend

- **Hash routing**: `app.js` uses `location.hash` and `hashchange` listener.
- **Settings extracted**: `settings.js` exists and is loaded separately.
- **WebSocket client**: `api.js` exposes `WS` object; `events.js` subscribes to it.
- **Phase 2/3 visualizations**:
  - Lifecycle: age/access distribution bar charts + zombie table (`metrics.js` / `index.html`).
  - Agents: 30-day multi-line trend chart + severity heatmap.
  - Dedup: 30-day trend line chart + conflicts list.
  - Consolidation: 30-day trend line chart.
- **Approve button removed**: `consolidation.js` only renders `reject-btn` for promotion candidates; a `ponytail:` comment documents the design decision.
- **All 7 tabs wired**: Dashboard, Learnings, Retrieval, Metrics, Consolidation, Fleet, Settings.

### Test Coverage

- `tests/test_server.py` covers: health/version, stats, log, retrieve, resolve, consolidate, learnings filters, quarantine, archive, metrics type filter, consolidation state, fleet, dashboard static files, API key, alert broadcast, WebSocket deduplication, lifecycle/dedup/consolidation/agents fields, no-approve-button regression, and config update (invalid, valid, preserve unknown, atomic failure).
- Full suite: `125 passed`.

## Issues

No blocking issues found. The implementation satisfies the `web-dashboard-2.3.md` spec and the `web-dashboard-2.3-audit-follow-up.md` requirements.

## Recommendations

1. **Manual smoke test**: Run `python src/filter.py --dashboard --memory-dir memory` and verify all tabs render in a browser.
2. **WebSocket manual verification**: Trigger a log via curl and confirm only one toast/event appears.
3. **Version bump**: Already at `1.20.5`; no further bump needed unless additional dashboard work is planned.
