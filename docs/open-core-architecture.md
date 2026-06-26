# Open-Core Architecture

MnemoQ uses an open-core model: the AGPL-3.0-or-later core lives in this public repo, while a proprietary Pro tier runs from a separate private repo.

## AGPL Core (This Repo)

Everything needed for local-first agent memory — free to use under AGPL terms:

- **Engine**: retrieval, validation, consolidation, scoring, reranking
- **CLI**: `mnemoq` command-line interface
- **Local storage**: JSONL-based episodic memory
- **MCP server**: Model Context Protocol integration (`mnemoq-mcp`)
- **Local dashboard**: built-in web UI for browsing memories
- **SDK**: programmatic access via `agent_memory.sdk`
- **Embeddings**: local sentence-transformers integration
- **Scaffold/update**: project initialization and migration tooling

## Proprietary Pro (Private Repo — Not Yet Created)

Closed-source commercial extensions:

- **Cloud sync server**: remote storage and synchronization
- **Multi-tenant layer**: team/org management
- **Hosted dashboard backend**: server-side dashboard API
- **Billing**: subscription and usage metering

## The Sync Client Boundary

When built, `src/agent_memory/sync.py` will be **AGPL** — it's the client that talks to the proprietary server. The server itself stays closed.

**Why this works**: AGPL moat. Competitors can use the sync client, but AGPL requires them to open-source their server if they distribute or host it as a network service. The proprietary server code is never in the AGPL repo.

## Module Classification

| Module | Tier | Status |
|--------|------|--------|
| `agent_memory.cli` | Core | Active |
| `agent_memory.engine.retrieval` | Core | Active |
| `agent_memory.engine.validation` | Core | Active |
| `agent_memory.engine.consolidation` | Core | Active |
| `agent_memory.engine.metrics` | Core | Active |
| `agent_memory.engine.reranker` | Core | Active |
| `agent_memory.engine.server` | Core | Active |
| `agent_memory.engine.mcp_server` | Core | Active |
| `agent_memory.engine.dashboard_api` | Core | Active |
| `agent_memory.dashboard` | Core | Active |
| `agent_memory.sdk` | Core | Active |
| `agent_memory.scaffold` | Core | Active |
| `agent_memory.update` | Core | Active |
| `agent_memory.engine.eval` | Core | Active |
| `agent_memory.engine.profile` | Core | Active |
| `agent_memory.engine.agents_review` | Core | Active |
| `agent_memory.engine.migrate` | Core | Active |
| `agent_memory.engine.storage` | Core | Planned (v1.22) |
| `agent_memory.engine.entities` | Core | Planned (v1.25) |
| `agent_memory.engine.graph` | Core | Planned (v1.25.1) |
| `agent_memory.engine.transfer` | Core | Planned (v1.28.1) |
| `agent_memory.sync` | Core (client) | Planned (v2.0) |
| Cloud sync server | Pro | Planned (v2.0) |
| Multi-tenant layer | Pro | Planned (v3.0) |
| Hosted dashboard backend | Pro | Planned (v2.2) |
| Billing service | Pro | Planned |
