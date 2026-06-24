# Web Dashboard 2.3 — Audit Follow-Up Plan

Addresses the findings from the Phase 1-3 audit. Goal: harden the dashboard backend, finish the Phase 2/3 visualization fidelity, and close the remaining gaps versus the `web-dashboard-2.3.md` specification.

## Scope

- Backend: `src/engine/server.py`, `src/engine/dashboard_api.py`, `src/engine/analysis.py`, `src/engine/metrics.py`
- Frontend: `src/dashboard/static/js/*.js`, `src/dashboard/static/index.html`, `src/dashboard/static/css/style.css`
- Tests: `tests/test_server.py`, `tests/test_memory.py`

## Scope boundaries

- This plan is strictly Phase 2.3 dashboard hardening. Do not add new retrieval algorithms, embedding features, MCP tools, SDK methods, or packaging work.
- Do not add new fields to the learning entry schema. The approve button is removed rather than persisted to avoid schema drift.
- Visualization work is limited to the existing Metrics Deep-Dive sub-tabs using the current Chart.js setup. No new tabs, no new backend storage, no new analysis modules.

## Outcomes

1. No FastAPI deprecation warnings in the test suite.
2. WebSocket events are not duplicated between API post-call hooks and the file watcher.
3. Phase 2/3 visualizations match the original plan: lifecycle histograms, zombie table, agent trend line chart/severity heatmap, dedup trend/conflict list, consolidation line chart.
4. Consolidation Console removes the UI-only approve button; reject still resolves the entry.
5. Config updates are atomic and validate the fields the dashboard actually edits.
6. `/api/health` returns the real engine version from the `VERSION` file.

---

## Task 1: Replace `@router.on_event("startup")` with a lifespan handler

### Why

FastAPI deprecates `@router.on_event`. The current code emits 11 warnings during test runs.

### What

Move the file watcher startup into a lifespan handler in `server.py` and pass it into the dashboard router.

### Files

- `src/engine/server.py`
- `src/engine/dashboard_api.py`

### Details

1. In `server.py`, add `import asyncio` and `from contextlib import asynccontextmanager`.
2. Define a lifespan inside `create_app` so it closes over `paths` and `event_hub`:

   ```python
   @asynccontextmanager
   async def lifespan(app: FastAPI):
       task = asyncio.create_task(event_hub.watch_metrics_file(paths))
       yield
       task.cancel()
       try:
           await task
       except asyncio.CancelledError:
           pass
   ```

3. Pass `lifespan=lifespan` to `FastAPI(...)` in `create_app`.
4. Remove the `@router.on_event("startup")` decorator and `_start_watcher` from `dashboard_api.py`.
5. Move `_watch_metrics_file` from `dashboard_api.py` to a method `EventHub.watch_metrics_file(self, paths)` in `server.py`. The method must read `_metrics_path(paths)` and broadcast new lines through `self.broadcast()` so the deduplication logic in Task 2 is centralized.

### Verification

- `python -m pytest tests/test_server.py -x -q` shows no `DeprecationWarning`.
- A new test asserts that the file watcher task is created during app lifespan (e.g., by patching `asyncio.create_task` and checking it is called with a coroutine named `watch_metrics_file`).

---

## Task 2: Deduplicate WebSocket events between API hook and file watcher

### Why

The file watcher broadcasts every new line in `metrics.jsonl`, including events that were already pushed instantly by the API post-call hook. Clients see duplicate events.

### What

Add a rolling set of recently broadcast event IDs/timestamps to `EventHub`. The file watcher skips events already seen.

### Files

- `src/engine/server.py`

### Details

1. Extend `EventHub`:

   ```python
   class EventHub:
       def __init__(self):
           self._clients: list[WebSocket] = []
           self.last_alert_count: int = 0
           self._seen: set[str] = set()
           self._seen_max: int = 500
   ```

2. Add a `_seen_key(event: dict) -> str` helper that normalizes both API-hook events and parsed `metrics.jsonl` lines into a single key space. Use `|` as the delimiter and fall back to `""` for missing fields:

   ```python
   def _seen_key(event: dict) -> str:
       et = event.get("event") or event.get("event_type", "unknown")
       if et in ("log",):
           detail = event.get("entry", {}).get("ts") or event.get("entry_ts", "")
           status = event.get("status") or event.get("outcome", "")
           return f"log|{detail}|{status}"
       if et in ("resolve",):
           ts = event.get("ts", "")
           status = event.get("status", "")
           return f"resolve|{ts}|{status}"
       if et in ("consolidate",):
           sprint = event.get("sprint") or event.get("sprint_number", "")
           status = event.get("status", "")
           return f"consolidate|{sprint}|{status}"
       if et in ("config_update",):
           return f"config_update|{event.get('ts', '')}"
       if et in ("alert",):
           return f"alert|{event.get('message', '')}"
       return f"{et}|{event.get('ts', '')}"
   ```

3. In `broadcast()`:
   - Compute the key and add it to `_seen` before sending.
   - If `len(_seen) > _seen_max`, rebuild the set with the most recent `_seen_max` keys. Store the last `_seen_max` keys in a separate list or use a `dict` insertion-order trick to make eviction deterministic.

4. In `EventHub.watch_metrics_file()`, parse each new line into a dict and call `self.broadcast(parsed)` directly. `broadcast()` will skip duplicates because the key is already in `_seen` from the API hook.

### Verification

- Add a regression test in `tests/test_server.py`:
  - Log an entry via `/api/log`.
  - Assert only one `log` event is received by a mocked WebSocket client.
  - Simulate a file watcher tick that re-reads the same `metrics.jsonl` line and assert no second broadcast.
  - Test that two different log entries produce two different keys.

---

## Task 3: Implement missing Phase 2/3 visualizations

### 3A. Lifecycle — histograms and zombie table

#### Backend

- Add `zombie_entries` to `_lifecycle_stats` in `src/engine/metrics.py`:
  - Zombie = `not resolved` AND `access_count == 0` AND entry age >= 7 days.
  - Entry age is computed from `entry.ts` to `datetime.now(timezone.utc)`.
- Add `age_distribution` buckets to `_lifecycle_stats`: `[0, 7, 14, 30, 60, 90, 180+]` days.
- Add `access_distribution` buckets to `_lifecycle_stats`: `[0, 1, 2, 5, 10, 25, 50+]`.
- Limit `zombie_entries` to the first 50 (sorted by age descending) to keep payloads small.

#### Frontend

- In `src/dashboard/static/index.html`, add inside the Lifecycle sub-tab container (after the metrics cards):
  - `<canvas id="chart-lifecycle-age"></canvas>`
  - `<canvas id="chart-lifecycle-access"></canvas>`
  - `<div id="zombie-table"></div>`
- In `src/dashboard/static/js/metrics.js` `renderLifecycleMetrics()`:
  - Render `age_distribution` as a bar chart on `#chart-lifecycle-age`.
  - Render `access_distribution` as a bar chart on `#chart-lifecycle-access`.
  - Render `zombie_entries` as a table in `#zombie-table` when non-empty.

### 3B. Agents — sparklines and severity heatmap

#### Backend

- Extend `_agent_stats` in `src/engine/metrics.py` to return:
  - Per-agent per-day log/retrieval counts for the last 30 days.
  - Per-agent severity counts (`critical`, `major`, `minor`).

#### Frontend

- In `src/dashboard/static/index.html`, add inside the Agents sub-tab container:
  - `<canvas id="chart-agents-trends"></canvas>` (single multi-line chart: one line per agent for 30-day log count)
  - `<div id="agents-severity-heatmap"></div>`
- In `src/dashboard/static/js/metrics.js` `renderAgentMetrics()`:
  - Render the 30-day per-agent log trend as a multi-line chart on `#chart-agents-trends`.
  - Render the severity heatmap as an HTML table (agents × severities) in `#agents-severity-heatmap`.

### 3C. Dedup — trend line chart and conflict list

#### Backend

- `_dedup_stats` already computes rates; extend it to return `daily` duplicate/conflict counts over the last 30 days.
- Add a `conflicts` list with the most recent conflict events (timestamp, source agents, trigger similarity). Limit to 20.

#### Frontend

- In `src/dashboard/static/index.html`, add inside the Dedup sub-tab container:
  - `<canvas id="chart-dedup-trend"></canvas>`
  - `<div id="dedup-conflicts"></div>`
- In `renderDedupMetrics()`:
  - Render the 30-day duplicate/conflict trend as a line chart on `#chart-dedup-trend`.
  - Render `conflicts` as a table in `#dedup-conflicts` when non-empty.

### 3D. Consolidation — line chart

#### Backend

- `_consolidation_stats` already returns per-sprint counts; extend it to return `daily` time series of `promotion_candidates`, `contradictions`, `stale_entries` over the last 30 days.

#### Frontend

- In `src/dashboard/static/index.html`, add inside the Consolidation sub-tab container:
  - `<canvas id="chart-consolidation-trend"></canvas>`
- In `renderConsolidationMetrics()`:
  - Render the consolidation trend as a multi-line chart on `#chart-consolidation-trend`.

### Verification

- Each new endpoint field must be covered in `tests/test_server.py`:
  - `/api/metrics/lifecycle` returns `zombie_entries` and distribution buckets.
  - `/api/metrics/agents` returns `severity_counts` and `trend`.
  - `/api/metrics/dedup` returns `daily` and `conflicts`.
  - `/api/metrics/consolidation-quality` returns `daily`.
- Manual: open the dashboard, navigate to Metrics, and confirm all sub-tabs render charts without `No data` placeholders when data exists.

---

## Task 4: Remove the UI-only approve button

### Why

The current Consolidation Console renders an `Approve` button for promotion candidates that only changes its own text and does not persist anything. Adding a real `/api/approve` endpoint would require a new entry field (`promotion_approved`), which risks schema-validation drift and is outside the current Phase 2.3 dashboard hardening scope. The sleep cycle already determines promotion candidates.

### What

- Remove the `Approve` button from `renderCandidateTable()` in `src/dashboard/static/js/consolidation.js`.
- Keep the `Reject` button, which calls `resolveEntry(ts)` and removes the candidate from active learnings.
- Add a `ponytail:` comment above the candidate table noting that promotion approval is handled by the sleep cycle logic.

### Files

- `src/dashboard/static/js/consolidation.js`

### Verification

- Confirm no `approve-btn` exists in the rendered candidate table HTML.
- Add/update a test that loads the consolidation state and asserts that candidate rows contain only a reject button (or no approve button).

---

## Task 5: Atomic config update and broader validation

### Why

The current `PUT /api/config` writes directly to `config.json` and only checks `valid_domains` and `valid_source_agents`.

### What

1. Validate only the fields the dashboard actually edits, plus the lists it displays. Use `templates/config.json` as a reference shape; unknown top-level fields are pass-through (do not block future additions).
   - `tuning.score_threshold`: float in `[0, 1]`
   - `tuning.decay_rate`: float in `[0, 1]`
   - `tuning.component_weight`, `tuning.file_weight`, `tuning.domain_weight`, `tuning.no_match_weight`: non-negative floats
   - `tuning.retention_*`: positive integers
   - `tuning.max_warnings`, `tuning.max_patterns`: positive integers
   - `valid_domains`: list of strings or `null`
   - `valid_source_agents`: list of strings or `null`
   - `retrieval_only_agents`: list of strings or `null`
   - `api_key`: string or `null`
   - `project_name`: non-empty string
2. Preserve any fields not listed above (e.g., `embedding_model`, `reranker`, `domain_mappings`) as pass-through.
3. Write atomically:
   - Serialize to `config.json.tmp` in the same directory as `config.json`.
   - Call `os.replace(tmp, paths.config_path)` so the swap is atomic on the same filesystem.

### Files

- `src/engine/dashboard_api.py`

### Verification

- Add tests in `tests/test_server.py`:
  - `PUT /api/config` with invalid tuning (`score_threshold = 1.5`) returns 400.
  - `PUT /api/config` with valid config succeeds and a subsequent `GET /api/config` returns the updated values.
  - `PUT /api/config` with unknown top-level fields preserves them.
  - Verify the temp file is removed after a successful write.
  - Verify that a failed write leaves the original `config.json` unchanged (force a failure by writing to a read-only temp directory or by raising inside the function before `os.replace`).

---

## Task 6: Read `VERSION` dynamically in `/api/health`

### What

In `src/engine/server.py`, change the health endpoint:

```python
@server.py:122
@app.get("/api/health")
async def health():
    version = _read_version()
    return {"status": "ok", "version": version}
```

Add a helper `_read_version()` that reads `VERSION` from the repo root. `src/engine/server.py` is two directories below the repo root, so use:

```python
def _read_version() -> str:
    version_file = Path(__file__).resolve().parents[2] / "VERSION"
    try:
        return version_file.read_text().strip().splitlines()[0]
    except Exception:
        return "unknown"
```

`server.py` already imports `os`, so no extra imports are needed beyond `from pathlib import Path`.

### Verification

- `test_health` checks that the returned version matches the first line of `VERSION`.
- `test_health` also checks that the response still contains `"status": "ok"`.

---

## Testing strategy

- Run `python -m pytest tests/test_server.py -x -q` and `python -m pytest tests/test_memory.py -x -q`.
- Target: no warnings, all existing tests still pass, and new tests cover:
  - lifespan startup (watcher starts)
  - WebSocket deduplication
  - new metric fields
  - atomic config write
  - dynamic version
  - approve button removal

## Version bump

- When this plan is complete, bump `VERSION` from `1.20.4` to `1.20.5`.

## Dependency summary

| Task | Backend files | Frontend files | Tests |
|---|---|---|---|
| 1. Lifespan handler | `server.py`, `dashboard_api.py` | — | existing suite + lifespan test |
| 2. WS deduplication | `server.py` | — | new WS regression test |
| 3. Visualizations | `metrics.py` | `metrics.js`, `index.html`, `style.css` | new endpoint assertions |
| 4. Remove approve button | — | `consolidation.js` | UI test / DOM assertion |
| 5. Atomic config | `dashboard_api.py` | — | new config validation tests |
| 6. Dynamic version | `server.py` | — | update `test_health` |

---

## Estimated order

1. Task 1 + Task 2 (both touch `EventHub` / watcher, do them together)
2. Task 6 (one-line change, quick win)
3. Task 5 (backend-only)
4. Task 4 (frontend-only)
5. Task 3 (largest change, frontend-heavy)
