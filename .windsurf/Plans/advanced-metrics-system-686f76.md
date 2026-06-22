# Advanced Metrics & Analytics System

A comprehensive upgrade to the Agent Memory Engine's metrics system that harvests every possible signal from learnings, retrieval, logging, consolidation, quarantine, and cross-project data, then analyzes it to judge how well the memory system is working.

**Roadmap version**: Implements roadmap 8.5 (v2.7.1). The GUI dashboard's `/api/metrics/*` endpoints (roadmap 2.3, `memory-engine-gui-686f76.md` Phase 1) wrap the analysis functions defined here.

---

## Current State (from code analysis)

### What Exists
- **Event logging**: `log_event()` in `@/c:/AgentMemoryEngine/src/engine/metrics.py:65-81` captures 7 event types (retrieval, log, update, resolve, stats, consolidate, review_agents) to `metrics.jsonl`
- **Instrumentation points**: retrieval logs query context + results + scores; log events capture outcome + similarity; consolidation logs promotion/contradiction/stale counts; stats captures entry breakdowns
- **Analysis functions**: `_retrieval_stats`, `_logging_stats`, `_consolidation_stats`, `_trend_stats` — basic aggregations
- **Reports**: summary, retrieval deep-dive, logging deep-dive, consolidation history, trend (daily buckets), cross-project comparison table
- **CLI**: `--metrics`, `--metrics-retrieval`, `--metrics-logging`, `--metrics-consolidation`, `--metrics-trend`, `--metrics-all-projects`, `--metrics-json`, `--metrics-since`, `--metrics-export`

### What's Missing (gaps this plan fills)
1. **No learnings.jsonl harvesting** — metrics only come from event logs, not from the actual learning entries themselves
2. **No health scoring** — no composite "memory system health" score
3. **No retrieval quality analysis** — hit rate is counted but not analyzed by component/domain/step range
4. **No entry lifecycle tracking** — no tracking of entry aging, access patterns, or reinforcement trajectories
5. **No quarantine analysis** — quarantine events logged but no systematic analysis of quarantine patterns
6. **No agent quality scoring** — per-agent contribution tracked but not analyzed for quality
7. **No staleness trending** — staleness checked during consolidation but not trended over time
8. **No config health** — no validation that config values are producing good outcomes
9. **No alerting** — no thresholds, no warnings when metrics degrade
10. **No correlation analysis** — no cross-referencing between retrieval patterns and logging patterns
11. **No time-windowed analysis** — trend is daily buckets only, no rolling windows or sprint-based analysis
12. **No export to external tools** — JSON export exists but no CSV/Grafana-compatible format
13. **No entry-level metrics** — individual entries aren't scored for effectiveness
14. **No retention analysis** — no analysis of how retention windows affect retrieval quality
15. **No dedup effectiveness** — duplicate rate tracked but not analyzed for quality of dedup decisions

---

## Phase 1: Data Harvesting (v2.7.1a — expand what's captured)

### 1.1 Learnings Snapshot Metrics
**Problem**: Current metrics only capture events, not the state of `learnings.jsonl` itself.

**Upgrade**: New `--metrics-snapshot` mode that harvests all data from `learnings.jsonl` directly:
- Entry age distribution (steps since creation, grouped by severity)
- Component coverage map (which components have learnings, which don't)
- Domain coverage map
- File coverage map (which files have learnings)
- Entry overlap analysis (entries sharing components/files — potential graph clusters)
- Orphan entries (entries with access_count=0 and reinforcement_count=0)
- Hot entries (access_count >= 5, sorted by access)
- Proven entries (reinforcement_count >= 5)
- Zombie entries (unresolved + access_count=0 + step_diff > retention window — should have been archived)
- Debt distribution (proper vs workaround vs temporary, with trend)
- Verified vs unverified ratio
- Severity × type cross-tabulation
- Scope distribution (file vs module vs system)

**Files**: `metrics.py`, new `src/engine/snapshot.py`

### 1.2 Enriched Retrieval Instrumentation
**Problem**: Current retrieval events at `@/c:/AgentMemoryEngine/src/engine/retrieval.py:164-179` log aggregate counts but not per-entry scores or which entries were returned.

**Upgrade**: Add per-entry detail to retrieval events:
- `returned_entries`: list of `{ts, score, match_type, severity, domain, components}` for each returned entry
- `match_types`: count of each match type (component, file, domain, no_match)
- `filtered_by_retention`: count of entries filtered out by retention window
- `filtered_by_resolved`: count of resolved entries skipped
- `score_distribution`: histogram buckets (0-0.1, 0.1-0.2, ..., 0.9-1.0)
- `query_hash`: hash of (step, components, domain) for deduplicating identical queries in analysis

**Files**: `retrieval.py`

### 1.3 Enriched Logging Instrumentation
**Problem**: Current log events capture outcome + similarity but not the full entry context.

**Upgrade**: Add to log events:
- `entry_components`: list
- `entry_files_touched`: list
- `entry_importance`: int
- `entry_scope`: string
- `entry_debt_level`: string
- `entry_verified`: bool
- `entry_symptoms`: bool (has symptoms text)
- `matched_entry_ts`: timestamp of the entry matched against (for dup/conflict)
- `agents_md_overlap`: bool (was AGENTS.md overlap detected)

**Files**: `handlers.py`

### 1.4 Quarantine Deep Harvest
**Problem**: Quarantine events log reason text but not structured categories.

**Upgrade**: New `--metrics-quarantine` mode that reads `quarantine.jsonl` directly:
- Total quarantined entries
- Quarantine reason categories (structured, not string matching):
  - `json_parse`, `schema_validation`, `agent_permission`, `semantic_duplicate`, `other`
- Quarantine rate over time (from entry timestamps)
- Quarantine by agent (which agents produce most quarantines)
- Quarantine by domain/type
- Recovery rate (entries that were quarantined then later successfully logged)
- Quarantine age distribution

**Files**: `metrics.py`, `quarantine` reading from `io.py`

### 1.5 Archive Harvest
**Problem**: Archived entries in `archive/sprint-N.jsonl` are never analyzed after archiving.

**Upgrade**: New `--metrics-archive` mode:
- Total archived entries across all sprints
- Sprint-by-sprint breakdown (entries archived, promotion candidates, contradictions)
- Archive entry analysis: what types/severities/domains get archived most
- Promotion success rate (candidates that actually made it to SYSTEM_INVARIANTS.md)
- Archive age (how old were entries when archived, by step diff)

**Files**: `metrics.py`, reads `archive/` directory

---

## Phase 2: Analysis Engine (v2.7.1b — turn data into insights)

### 2.1 Memory Health Score
**Problem**: No single number that says "your memory system is healthy" or "needs attention."

**Upgrade**: Composite health score (0-100) combining:
- **Retrieval effectiveness** (30%): hit rate, avg top score, result diversity
- **Entry quality** (25%): verified ratio, proven ratio, debt distribution, zombie count
- **Logging discipline** (20%): quarantine rate, duplicate rate, conflict rate
- **Consolidation hygiene** (15%): unresolved count, staleness, last consolidation age
- **Coverage** (10%): component coverage, domain coverage, file coverage

Each sub-score is 0-100. Weighted sum = overall health. Report includes per-component breakdown so user knows what to fix.

**Files**: new `src/engine/health.py`, `metrics.py`

### 2.2 Retrieval Quality Analysis
**Problem**: Hit rate tells you *if* results were returned, not *whether they were useful*.

**Upgrade**: New `--metrics-retrieval-quality` analysis:
- **Hit rate by component**: which components have good retrieval vs poor
- **Hit rate by domain**: which domains are well-covered vs sparse
- **Hit rate by step range**: is retrieval getting better or worse over time?
- **Score trend**: is avg top_score trending up or down over time?
- **Empty query analysis**: what (component, domain) combinations always return nothing?
- **Result diversity**: are the same entries always returned? (entropy of returned entry timestamps)
- **Retrieval-to-action correlation**: do entries returned in retrieval get accessed again soon? (access_count increase after retrieval)
- **Retention window analysis**: how many entries are filtered by retention? Is the threshold too aggressive?
- **Cold start detection**: queries where no entries exist yet (component/domain has zero learnings)

**Files**: `metrics.py`, new `src/engine/analysis.py`

### 2.3 Entry Lifecycle Analysis
**Problem**: No tracking of how entries evolve over their lifetime.

**Upgrade**: New `--metrics-lifecycle` analysis:
- **Entry aging curve**: distribution of step_diff for all entries (are entries mostly fresh or stale?)
- **Access trajectory**: for each entry, plot access_count over time (retrieval events referencing it)
- **Reinforcement trajectory**: reinforcement_count over time
- **Entry "half-life"**: at what step_diff do entries stop being retrieved? (access_count drops to 0)
- **Promotion velocity**: how quickly do entries accumulate reinforcement (steps from log to reinforcement_count >= 5)
- **Decay effectiveness**: are entries with low scores actually being forgotten, or do they keep getting retrieved?
- **Zombie detection**: entries that are unresolved, old, never accessed, never reinforced — candidates for manual resolution or archival

**Files**: `analysis.py`, `metrics.py`

### 2.4 Agent Quality Scoring
**Problem**: Agent contributions are counted but not quality-scored.

**Upgrade**: Per-agent quality report:
- **Added rate**: % of logs that result in ADDED (vs QUARANTINED/DUPLICATE/CONFLICT)
- **Quarantine rate**: % of logs quarantined
- **Conflict rate**: % of logs that conflict with existing entries
- **Promotion rate**: % of agent's entries that become promotion candidates
- **Retrieval relevance**: % of agent's entries that get retrieved (access_count > 0)
- **Verification rate**: % of agent's entries that are verified
- **Severity distribution**: does the agent log mostly minor/major/critical?
- **Domain expertise**: which domains does this agent contribute to most
- **Composite agent score**: weighted combination of above

**Files**: `analysis.py`, `metrics.py`

### 2.5 Dedup & Conflict Analysis
**Problem**: Dup/conflict rates are tracked but not analyzed for quality.

**Upgrade**: New `--metrics-dedup` analysis:
- **Dedup accuracy**: are duplicated entries actually similar? (sample and check)
- **Dedup by domain**: which domains produce most duplicates
- **Dedup by agent**: which agents produce most duplicates
- **Conflict resolution rate**: % of conflicts that get resolved via Challenge Protocol
- **Conflict age**: how long do conflicts remain unresolved?
- **Conflict pairs**: which entry pairs are in conflict (list with details)
- **Dedup threshold analysis**: would lowering/raising the 0.7 threshold catch more/less?
- **Similarity score distribution**: histogram of similarity scores at log time

**Files**: `analysis.py`, `metrics.py`

### 2.6 Consolidation Effectiveness
**Problem**: Consolidation events are logged but not analyzed for effectiveness.

**Upgrade**: New `--metrics-consolidation-quality` analysis:
- **Consolidation frequency**: how often is Sleep Cycle run (days between runs)
- **Archive growth rate**: entries archived per sprint (trending up or down?)
- **Promotion conversion rate**: promotion candidates → actually promoted to SYSTEM_INVARIANTS.md
- **Stale entry rate**: % of entries stale at consolidation time (trending — is staleness getting worse?)
- **Pre-consolidation unresolved count**: is the backlog growing or shrinking?
- **Post-consolidation clean slate duration**: how long after reset until entries accumulate again
- **Quarantine review rate**: % of quarantined entries reviewed during consolidation

**Files**: `analysis.py`, `metrics.py`

### 2.7 Cross-Project Comparative Analysis
**Problem**: Current cross-project report (`@/c:/AgentMemoryEngine/src/engine/metrics.py:550-584`) is a flat table with 6 columns.

**Upgrade**: Rich cross-project analysis:
- **Health score comparison**: side-by-side health scores for all projects
- **Retrieval effectiveness comparison**: which projects have best/worst retrieval
- **Logging discipline comparison**: which projects have cleanest logging
- **Domain overlap**: which domains are shared across projects (cross-project learning candidates)
- **Component overlap**: similar components across projects
- **Best practices transfer**: which project's patterns could benefit others
- **Project maturity ranking**: by entry count, retrieval hit rate, consolidation frequency
- **Cross-project trends**: are all projects improving together or diverging?

**Files**: `metrics.py`, `analysis.py`

---

## Phase 3: Alerting & Recommendations (v2.7.1c)

### 3.1 Threshold-Based Alerts
**Problem**: No automated warnings when metrics degrade.

**Upgrade**: Alert system with configurable thresholds in `config.json`:
```json
"alerts": {
  "quarantine_rate_warn": 0.15,
  "quarantine_rate_critical": 0.25,
  "duplicate_rate_warn": 0.20,
  "conflict_rate_warn": 0.10,
  "empty_retrieval_rate_warn": 0.50,
  "unresolved_count_warn": 50,
  "stale_entry_rate_warn": 0.30,
  "zombie_count_warn": 10,
  "health_score_warn": 60,
  "health_score_critical": 40,
  "consolidation_overdue_days": 14
}
```

Alerts printed on every `--metrics` invocation and on `--step` retrieval (stderr, one-line summary). Full alert detail in `--metrics-alerts`.

**Files**: `metrics.py`, `constants.py`, `config.json`

### 3.2 Smart Recommendations
**Problem**: Metrics show *what* is happening but not *what to do about it*.

**Upgrade**: Recommendation engine that analyzes metrics and generates actionable advice:
- "Quarantine rate is 28% — review schema validation errors. Most common: missing 'trigger' field. Consider updating agent prompts."
- "15 zombie entries detected — run --consolidate to archive stale learnings."
- "Retrieval hit rate for domain 'physics' is 23% (below 50% average) — log more learnings for this domain."
- "Agent 'code-reviewer' has 40% quarantine rate — review its logging format."
- "12 unresolved conflicts — review Challenge Protocol entries."
- "No consolidation in 18 days — Sleep Cycle overdue."
- "Component 'Enemy' has 0 learnings but is queried frequently — knowledge gap."
- "Duplicate rate rising: 12% → 19% over last 30 days — consider lowering dedup threshold or improving agent logging discipline."

Each recommendation includes: severity (info/warn/critical), category, description, suggested action.

**Files**: new `src/engine/recommendations.py`, `metrics.py`

### 3.3 Config Tuning Suggestions
**Problem**: No feedback loop between metrics and config values.

**Upgrade**: Analyze whether current config values are optimal:
- **Decay rate**: if entries with high access_count are being filtered by decay, rate may be too aggressive
- **Score threshold**: if hit rate is low but entries exist, threshold may be too high
- **Retention windows**: if many entries filtered by retention but would have been relevant, windows too short
- **Max warnings/patterns**: if results are being truncated, limits too low
- **Dedup threshold (0.7)**: if duplicate rate is high, threshold may be too high; if false positives, too low

Report: `--metrics-config-tuning` suggests specific config value changes with rationale.

**Files**: `analysis.py`, `metrics.py`

---

## Phase 3.5: GUI Dashboard Integration (roadmap 2.3, v1.20.2)

**Goal**: The GUI dashboard wraps all Phase 1-3 functions via `/api/metrics/*` REST endpoints. The dashboard ships earlier (v1.20.2) but only has data to display once Phase 1-3 are implemented (v2.7.1). Before that, the dashboard's Metrics Deep-Dive page shows basic metrics from the existing `metrics.py` functions.

**Plan file**: See `memory-engine-gui-686f76.md` for the full GUI architecture.

### What the GUI Consumes from Phases 1-3

| GUI View | Source Phase | Functions Wrapped |
|----------|-------------|-------------------|
| **Dashboard** (landing) | 2.1, 3.1, 3.2 | Health score, alerts, recommendations |
| **Learnings Browser** | 1.1 | Snapshot data (entry ages, coverage, orphans, zombies, hot, proven) |
| **Retrieval Explorer** | 1.2, 2.2 | Enriched retrieval events, retrieval quality analysis |
| **Metrics Deep-Dive** | 2.2-2.6 | Retrieval quality, lifecycle, agent quality, dedup, consolidation effectiveness |
| **Consolidation Console** | 1.5, 2.6 | Archive harvest, consolidation effectiveness |
| **Settings** | 3.1, 3.3 | Alert threshold config, config tuning suggestions |

### Build Steps

1. **FastAPI backend** (`src/dashboard/api.py`) — REST endpoints wrapping all Phase 1-3 analysis functions + WebSocket for live events
2. **Web frontend** (`src/dashboard/static/`) — vanilla JS SPA with Chart.js visualizations, 7 tabbed views
3. **Server integration** — `--dashboard` flag in `filter.py`, auto-opens browser
4. **Live event feed** — WebSocket pushes log/resolve/consolidate/alert events in real-time

### Why Here (Not Earlier)

- Phase 1 provides the **data** (snapshot, enriched events, quarantine/archive harvest)
- Phase 2 provides the **analysis** (health score, retrieval quality, lifecycle, agent quality, dedup, consolidation)
- Phase 3 provides the **intelligence** (alerts, recommendations, config tuning)
- Phase 3.5 is the **presentation layer** — it wraps all of the above in a visual interface
- Building earlier means writing UI for functions that don't exist yet — high rework risk

### Why Here (Not Later)

- Phase 4 (Output Formats) includes ASCII visualization and dashboard mode — the GUI **supersedes** 4.2 and 4.3 for GUI users
- Phase 5 (Cross-Project Hub) outputs feed directly into the GUI's **Fleet** page
- Having the GUI before Phase 4-5 means those phases can be developed with visual feedback

**Files**: See GUI plan for full file structure. Key additions: `src/dashboard/` (FastAPI app + static frontend)

---

## Phase 4: Output Formats & Visualization (v2.7.1d)

> **Note**: Phase 4 is the **CLI fallback** for users who don't use the GUI dashboard (Phase 3.5). The GUI replaces ASCII charts (4.2) and dashboard mode (4.3) with Chart.js visuals and a full web interface. Phase 4.1 (Multi-Format Export) is still useful — the GUI's export buttons call these same functions.

### 4.1 Multi-Format Export
**Problem**: Only JSONL export exists.

**Upgrade**: `--metrics-export --format <jsonl|csv|html|markdown>`:
- **CSV**: flat tabular format for Excel/pandas analysis
- **HTML**: self-contained HTML report with embedded CSS, tables, and ASCII-art charts
- **Markdown**: full report in markdown for git-committed analysis
- **JSONL**: raw events (existing, unchanged)

**Files**: `metrics.py`, new `src/engine/export.py`

### 4.2 ASCII Visualization
**Problem**: Trend data is printed as `2026-01-15: 3R 2L 0C 1O` — hard to spot patterns.

**Upgrade**: ASCII charts for all trend data:
- **Sparklines**: `▁▂▃▄▅▆▇█▇▆▅▄▃▂▁` for hit rate, entry count, quarantine rate over time
- **Bar charts**: horizontal bars for agent contributions, domain distribution
- **Heat map**: component × domain grid showing entry counts (█▓▒░·)
- **Timeline**: sprint-based timeline showing consolidation events
- **Gantt-style**: entry lifecycle visualization (creation → access → reinforcement → resolution/archive)

**Files**: new `src/engine/visualize.py`, `metrics.py`

### 4.3 Dashboard Mode (CLI)
**Problem**: Must run separate commands for each metric view. (GUI users: superseded by Phase 3.5 web dashboard)

**Upgrade**: `--metrics-dashboard` — single command that prints a comprehensive dashboard:
```
╔══════════════════════════════════════════════════════════════╗
║  AGENT MEMORY DASHBOARD — ProjectName — 2026-06-22         ║
║  Health Score: 73/100 ▓▓▓▓▓▓▓░░░                            ║
╠══════════════════════════════════════════════════════════════╣
║  RETRIEVAL          │  LOGGING            │  CONSOLIDATION  ║
║  Hit rate: 67% ▓▓▓▓░│  Added: 45          │  Sprints: 3     ║
║  Avg score: 0.34    │  Dup: 8 (15%) ░░    │  Promoted: 5    ║
║  Empty: 33% ▓▓░░░   │  Quar: 3 (6%) ░     │  Stale: 2       ║
╠══════════════════════════════════════════════════════════════╣
║  ALERTS: 2 warnings, 0 critical                           ║
║  ⚠ Quarantine rate rising (6% → 12% over 14 days)         ║
║  ⚠ 8 zombie entries — run --consolidate                    ║
╠══════════════════════════════════════════════════════════════╣
║  RECOMMENDATIONS: 3 actionable                             ║
║  → Log more learnings for domain 'audio' (0 entries)       ║
║  → Review 3 unresolved conflicts from agent 'gm'           ║
║  → Lower score_threshold from 0.15 to 0.10 (hit rate low)  ║
╚══════════════════════════════════════════════════════════════╝
```

**Files**: `visualize.py`, `metrics.py`

---

## Phase 5: Cross-Project Analytics Hub (v2.7.1e)

### 5.1 Global Metrics Aggregation
**Problem**: Cross-project reading exists but only produces a flat comparison table.

**Upgrade**: `--metrics-all-projects --dashboard` — global dashboard across all projects:
- Aggregate health scores (which projects are healthiest)
- Aggregate retrieval effectiveness (which projects benefit most from memory)
- Cross-project knowledge gaps (domains/components no project covers)
- Cross-project pattern sharing candidates (entries that could be promoted to global)
- Aggregate alert summary (which projects need attention)
- Trend comparison (are all projects improving or is one degrading?)

> **GUI**: These functions power the GUI's **Cross-Project Fleet** page via `/api/fleet` and `/api/projects/{id}/metrics/*` endpoints.

**Files**: `metrics.py`, `analysis.py`, `visualize.py`

### 5.2 Project Interaction Analysis
**Problem**: No analysis of how projects influence each other.

**Upgrade**:
- **Shared domain analysis**: projects sharing the same domain — do they have conflicting learnings?
- **Component naming overlap**: similar component names across projects (potential for shared learnings)
- **Agent overlap**: same agents working across projects — do they perform consistently?
- **Consolidation synchronization**: do projects consolidate at similar rates?

> **GUI**: Adds a **Project Interactions** sub-tab in the Fleet page.

**Files**: `analysis.py`, `metrics.py`

### 5.3 Fleet Report
**Problem**: No executive summary across all projects.

**Upgrade**: `--metrics-fleet` — one-page report for all projects:
- Total entries across all projects
- Aggregate health score
- Top 3 issues across fleet
- Top 3 improvements across fleet
- Projects needing immediate attention (sorted by health score ascending)
- Fleet-wide trends (retrieval improving? quarantine decreasing?)

> **GUI**: The Fleet page's executive summary view. One API call (`/api/fleet`) returns everything.

**Files**: `metrics.py`, `visualize.py`

---

## Implementation Priority

| Item | Effort | Impact | Order |
|------|--------|--------|-------|
| 1.1 Learnings Snapshot | Medium | High | 1st |
| 2.1 Memory Health Score | Medium | Critical | 2nd |
| 3.1 Threshold Alerts | Low | High | 3rd |
| 1.2 Enriched Retrieval | Low | High | 4th |
| 1.3 Enriched Logging | Low | Medium | 5th |
| 2.2 Retrieval Quality | Medium | High | 6th |
| 3.2 Smart Recommendations | Medium | High | 7th |
| 2.4 Agent Quality | Medium | Medium | 8th |
| 1.4 Quarantine Harvest | Low | Medium | 9th |
| 2.3 Entry Lifecycle | Medium | Medium | 10th |
| 2.5 Dedup Analysis | Medium | Medium | 11th |
| 2.6 Consolidation Effectiveness | Medium | Medium | 12th |
| 1.5 Archive Harvest | Low | Medium | 13th |
| 2.7 Cross-Project Comparative | Medium | High | 14th |
| 3.3 Config Tuning | Medium | Medium | 15th |
| **3.5 GUI Dashboard** | **Medium** | **Critical** | **16th** |
| 4.2 ASCII Visualization | Medium | Medium | 17th |
| 4.3 Dashboard Mode (CLI) | Low | Low | 18th |
| 5.1 Global Aggregation | Medium | High | 19th |
| 5.3 Fleet Report | Low | High | 20th |
| 4.1 Multi-Format Export | Low | Medium | 21st |
| 5.2 Project Interaction | High | Low | 22nd |

---

## New CLI Flags

```bash
# Existing (unchanged)
--metrics                          # Summary report
--metrics-retrieval                # Retrieval deep-dive
--metrics-logging                  # Logging deep-dive
--metrics-consolidation            # Consolidation history
--metrics-trend                    # Time-series trend
--metrics-all-projects             # Cross-project comparison
--metrics-json                     # JSON output
--metrics-since YYYY-MM-DD         # Date filter
--metrics-export PATH              # Export raw events

# New
--metrics-snapshot                 # Learnings.jsonl state analysis
--metrics-health                   # Composite health score
--metrics-alerts                   # Active alerts
--metrics-retrieval-quality        # Retrieval quality analysis
--metrics-lifecycle                # Entry lifecycle analysis
--metrics-quarantine               # Quarantine deep analysis
--metrics-archive                  # Archive analysis
--metrics-dedup                    # Dedup & conflict analysis
--metrics-consolidation-quality    # Consolidation effectiveness
--metrics-agents                   # Per-agent quality scoring
--metrics-config-tuning            # Config optimization suggestions
--metrics-recommendations          # Actionable recommendations
--metrics-dashboard                # Full visual dashboard
--metrics-fleet                    # Fleet-wide executive summary
--metrics-export --format csv|html|markdown  # Multi-format export
```

---

## New Files

| File | Purpose |
|------|---------|
| `src/engine/snapshot.py` | Harvest data from learnings.jsonl, quarantine.jsonl, archive/ |
| `src/engine/health.py` | Composite health score calculation |
| `src/engine/analysis.py` | Deep analysis functions (retrieval quality, lifecycle, agent quality, dedup, consolidation, config tuning) |
| `src/engine/recommendations.py` | Smart recommendation engine |
| `src/engine/visualize.py` | ASCII charts, CLI dashboard rendering |
| `src/engine/export.py` | Multi-format export (CSV, HTML, markdown) |
| `src/dashboard/` | GUI: FastAPI backend + web frontend (see `memory-engine-gui-686f76.md`, roadmap 2.3) |

---

## Files Modified

| File | Changes |
|------|---------|
| `metrics.py` | New report handlers, new CLI dispatch, enriched analysis functions |
| `retrieval.py` | Enriched `log_event` call with per-entry detail |
| `handlers.py` | Enriched `log_event` calls with full entry context |
| `constants.py` | Default alert thresholds |
| `config.json` | Alert threshold config, metrics settings |
| `filter.py` | New CLI argument flags, dispatch to new metrics modes |
