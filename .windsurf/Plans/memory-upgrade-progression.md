# Memory Upgrade Progression — Sequential Implementation Guide

The recommended build order across all three plan files. Each step references which plan and section to follow.

**Plan files:**
- `memory-engine-roadmap-consolidated.md` — **Roadmap** (product phases, priority matrix, version milestones)
- `advanced-metrics-system-686f76.md` — **Metrics** (data harvesting, analysis, alerting, visualization)
- `memory-engine-gui-686f76.md` — **GUI** (web dashboard, Tauri desktop, Textual TUI)

---

## Tier 1 — Free Tier Quality Foundation (v1.17 – v1.19)

These improve the core product before any distribution work. No deps, high impact.

| # | Step | Plan | Section | Version | Deps |
|---|------|------|---------|---------|------|
| 1 | BM25 Lexical Retrieval | Roadmap | 1.1 | v1.17.0 | None |
| 2 | Schema versioning field + migration runner | Roadmap | 1.2 (prerequisite) | v1.18.0 | None |
| 3 | Embedding-Based Retrieval | Roadmap | 1.2 | v1.18.0 | Step 2 |
| 4 | Embedding-Based Dedup | Roadmap | 1.3 | v1.19.0 | Step 3 |
| 5 | Reranking Pass | Roadmap | 1.4 | v1.19.1 | Step 3 |
| 6 | Grading Harness | Roadmap | 1.5 | v1.19.2 | None |## Tier 1 — Free Tier Quality Foundation (v1.17 – v1.19)

These improve the core product before any distribution work. No deps, high impact.

| # | Step | Plan | Section | Version | Deps |
|---|------|------|---------|---------|------|
| 1 | BM25 Lexical Retrieval | Roadmap | 1.1 | v1.17.0 | None |
| 2 | Schema versioning field + migration runner | Roadmap | 1.2 (prerequisite) | v1.18.0 | None |
| 3 | Embedding-Based Retrieval | Roadmap | 1.2 | v1.18.0 | Step 2 |
| 4 | Embedding-Based Dedup | Roadmap | 1.3 | v1.19.0 | Step 3 |
| 5 | Reranking Pass | Roadmap | 1.4 | v1.19.1 | Step 3 |
| 6 | Grading Harness | Roadmap | 1.5 | v1.19.2 | None |

---

## Tier 2 — Distribution & Access (v1.20 – v1.21)

Make the engine accessible from any tool. This unlocks the GUI dashboard and SDK.

| # | Step | Plan | Section | Version | Deps |
|---|------|------|---------|---------|------|
| 7 | HTTP API Server (FastAPI) | Roadmap | 2.1 | v1.20.0 | None |
| 8 | MCP Server | Roadmap | 2.2 | v1.20.1 | Step 7 |
| 9 | Web Dashboard (local, free tier) | GUI | Phase 1 (1.1–1.4) | v1.20.2 | Step 7 |
| 10 | pip Packaging | Roadmap | 2.5 | v1.21.1 | None |
| 11 | Python SDK | Roadmap | 2.4 | v1.21.0 | Step 7 |

> **Step 9 detail**: Follow GUI plan Phase 1 in order — 1.1 FastAPI backend, 1.2 web frontend (SPA), 1.3 server integration (`--dashboard` flag), 1.4 WebSocket live event feed. The dashboard's Metrics Deep-Dive page will show basic metrics from existing `metrics.py` until Tier 5 (Advanced Metrics) is built.

> **Why here**: API + MCP + dashboard + pip = the product is installable and usable from any tool. This is the distribution milestone.

---

## Tier 3 — Scalability & Intelligence (v1.22 – v1.28)

Storage scaling and memory intelligence features that differentiate from competitors.

| # | Step | Plan | Section | Version | Deps |
|---|------|------|---------|---------|------|
| 12 | SQLite Storage Backend | Roadmap | 3.1 | v1.22.0 | None |
| 13 | Incremental Index Updates | Roadmap | 3.2 | v1.22.1 | Step 12 |
| 14 | Auto-Archive | Roadmap | 3.3 | v1.23.0 | None |
| 15 | Spaced Repetition Decay | Roadmap | 4.1 | v1.24.0 | None |
| 16 | Bi-Temporal Timestamps | Roadmap | 4.2 | v1.24.1 | None |
| 17 | Entity Extraction & Linking | Roadmap | 4.3 | v1.25.0 | None |
| 18 | Memory Link Graph | Roadmap | 4.4 | v1.25.1 | Step 17 |
| 19 | Cue-Tag-Content Graph | Roadmap | 4.5 | v1.26.0 | Step 18 |
| 20 | Graph Consolidation | Roadmap | 4.6 | v1.26.1 | Step 18 |
| 21 | Procedural Memory Type | Roadmap | 5.1 | v1.27.0 | None |
| 22 | Actor-Aware Attribution | Roadmap | 5.2 | v1.27.1 | None |
| 23 | Auto Conflict Resolution | Roadmap | 5.3 | v1.28.0 | Step 16 |
| 24 | Cross-Project Learning Transfer | Roadmap | 5.4 | v1.28.1 | Step 4 |

> **Why this order**: SQLite (12) before incremental index (13) since index needs a DB. Entity extraction (17) before link graph (18) before cue-tag graph (19) — each builds on the previous graph layer. Cross-project transfer (24) needs embedding dedup (step 4) for import validation.

---

## Tier 4 — Pro Tier Launch (v2.0 – v2.2)

Cloud sync, team sharing, and Pro-tier UI. This is the revenue milestone.

| # | Step | Plan | Section | Version | Deps |
|---|------|------|---------|---------|------|
| 25 | Cloud Sync Architecture | Roadmap | 6.1 | v2.0.0 | Step 12 |
| 26 | Team Shared Learnings | Roadmap | 6.2 | v2.1.0 | Steps 24, 25 |
| 27 | Cloud-Hosted Dashboard | Roadmap | 6.3 | v2.2.0 | Steps 9, 25 |
| 28 | Tauri Desktop Wrapper | GUI | Phase 2 (2.1–2.3) | v2.2.1 | Step 9 |

> **Step 27 detail**: Deploy the same FastAPI dashboard app from step 9 to cloud. Add OAuth login, PostgreSQL-backed historical metrics, multi-project fleet view. See GUI plan Phase 1 (same UI, cloud-hosted).

> **Step 28 detail**: Follow GUI plan Phase 2 — Tauri 2.x setup, native enhancements (OS notifications, system tray, auto-start), packaging (.msi/.dmg/.AppImage).

---

## Tier 5 — Advanced Memory & Observability (v2.3 – v2.7)

Research-grade features and the full metrics system.

| # | Step | Plan | Section | Version | Deps |
|---|------|------|---------|---------|------|
| 29 | Hierarchical Memory Levels | Roadmap | 7.1 | v2.3.0 | Step 12 |
| 30 | Textual TUI Dashboard | GUI | Phase 3 (3.1–3.3) | v2.3.1 | Step 7 |
| 31 | Adaptive Retrieval | Roadmap | 7.2 | v2.4.0 | Steps 12, 19 |
| 32 | Background Consolidation ("Dreaming") | Roadmap | 7.3 | v2.5.0 | Steps 15, 23 |
| 33 | Multi-Modal Memory | Roadmap | 7.4 | v2.5.1 | None |
| 34 | File Locking & Concurrency | Roadmap | 8.1 | v2.6.0 | None |
| 35 | Schema Versioning & Migrations | Roadmap | 8.2 | v2.6.1 | None |
| 36 | Backup & Recovery | Roadmap | 8.3 | v2.6.2 | None |
| 37 | Plugin Architecture | Roadmap | 8.4 | v2.7.0 | None |
| 38 | Advanced Metrics & Analytics | Metrics | Phases 1–5 | v2.7.1 | None |

> **Step 30 detail**: Follow GUI plan Phase 3 — Textual app, TUI views mirroring web dashboard tabs, entry point (`--tui` flag). Connects to FastAPI API server if running, otherwise starts it in background.

> **Step 38 detail**: This is the big one. Follow Metrics plan in order:
> - **Phase 1** (v2.7.1a): Data harvesting — snapshot, enriched retrieval/logging instrumentation, quarantine harvest, archive harvest
> - **Phase 2** (v2.7.1b): Analysis engine — health score, retrieval quality, lifecycle, agent quality, dedup, consolidation effectiveness, cross-project comparative
> - **Phase 3** (v2.7.1c): Alerting — threshold alerts, smart recommendations, config tuning
> - **Phase 4** (v2.7.1d): Output formats — CSV/HTML/markdown export, ASCII visualization, CLI dashboard mode
> - **Phase 5** (v2.7.1e): Cross-project hub — global aggregation, project interaction, fleet report
>
> Once complete, the web dashboard's Metrics Deep-Dive page (step 9) populates with full analysis data via `/api/metrics/*` endpoints. The TUI (step 30) gets the same data.

---

## Tier 6 — Production Scale (v2.8 – v3.0)

| # | Step | Plan | Section | Version | Deps |
|---|------|------|---------|---------|------|
| 39 | Structured Error Handling | Roadmap | 8.6 | v2.8.0 | None |
| 40 | Multi-Tenant Architecture | Roadmap | 8.7 | v3.0.0 | Step 25 |

---

## Parallel Work Tracks

Some steps have no dependencies and can be developed in parallel. Key parallelization opportunities:

```
Track A (retrieval):  1 → 2 → 3 → 4 → 5
Track B (API):        7 → {8, 9 in parallel}
Track C (packaging):  10, 11 (both depend only on 7)
Track D (storage):    12 → 13
Track E (graph):      17 → 18 → {19, 20 in parallel}
Track F (metrics):    38 (can start anytime, but most useful after Tier 1-2)
Track G (standalone): 6, 14, 15, 16, 21, 22, 33, 34, 35, 36, 37, 39
```

**Recommended**: Run Track A and Track B in parallel during v1.17–v1.20. Track A improves the engine; Track B builds the distribution layer. They converge at step 9 (web dashboard needs FastAPI from step 7). Track G items have no deps and can be slotted in anytime — pick them up between higher-priority tracks to keep multiple developers busy.

---

## Dependency Graph (simplified)

```
1.1 BM25 (no deps — build first but nothing depends on it)

1.2 Schema → 1.2 Embeddings → 1.3 Dedup → 5.4 Cross-Project ──┐
                       ↓                                       │
                 1.4 Reranking                                 ↓
                                                              ↓
2.1 HTTP API → 2.2 MCP Server                          6.2 Team Shared
     ↓
2.3 Web Dashboard → 6.3 Cloud Dashboard
     ↓                    ↑
6.4 Tauri Desktop      6.1 Cloud Sync ──────┬────────────┘
                          ↑                  ↓
3.1 SQLite ───────────────┘             8.7 Multi-Tenant
     ↓
3.2 Index

3.1 SQLite → 7.1 Hierarchical → 7.2 Adaptive
                                    ↑
4.3 Entity → 4.4 Graph → 4.5 Cue-Tag ─┘

4.1 Spaced Rep ──→ 7.3 Dreaming ←── 5.3 Auto Conflict ←── 4.2 Bi-Temporal

7.5 TUI ←── 2.1 HTTP API

8.5 Advanced Metrics (standalone, but GUI integration via 2.3)
```

---

## Quick Reference: Plan → Roadmap Mapping

| Plan file | Plan phase | Roadmap section | Version |
|-----------|-----------|-----------------|---------|
| GUI Phase 1 | Web Dashboard | 2.3 | v1.20.2 |
| GUI Phase 2 | Tauri Desktop | 6.4 | v2.2.1 |
| GUI Phase 3 | Textual TUI | 7.5 | v2.3.1 |
| Metrics Phase 1 | Data Harvesting | 8.5 (part 1) | v2.7.1a |
| Metrics Phase 2 | Analysis Engine | 8.5 (part 2) | v2.7.1b |
| Metrics Phase 3 | Alerting | 8.5 (part 3) | v2.7.1c |
| Metrics Phase 3.5 | GUI Integration | 2.3 (dashboard) | v1.20.2 |
| Metrics Phase 4 | Output Formats | 8.5 (part 4) | v2.7.1d |
| Metrics Phase 5 | Cross-Project Hub | 8.5 (part 5) | v2.7.1e |
