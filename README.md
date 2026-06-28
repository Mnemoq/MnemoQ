# MnemoQ

Local-first memory engine for AI agents — MCP-native, graph-linked, spaced repetition.

```
Agent ──log──▶ MnemoQ Engine ──store──▶ learnings.jsonl
Agent ◀──retrieve── MnemoQ Engine ◀──read── learnings.jsonl
Agent ──MCP──▶ mnemoq-mcp ──read/write──▶ learnings.jsonl
```

[![PyPI version](https://img.shields.io/pypi/v/mnemoq.svg)](https://pypi.org/project/mnemoq/)
[![Python versions](https://img.shields.io/pypi/pyversions/mnemoq.svg)](https://pypi.org/project/mnemoq/)
[![CI](https://img.shields.io/github/actions/workflow/status/Mnemoq/MnemoQ/ci.yml?branch=main)](https://github.com/Mnemoq/MnemoQ/actions/workflows/ci.yml)
[![License: AGPL-3.0-or-later](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue.svg)](https://spdx.org/licenses/AGPL-3.0-or-later.html)

## Install

```bash
pip install mnemoq
```

CLI-only users (no Python project needed):

```bash
pipx install mnemoq
```

## Quick Start

### 1. Scaffold a project

```bash
mnemoq-scaffold ./my-project --defaults
```

This creates a `memory/` directory with `config.json` and `learnings.jsonl` in your project.

Wire memory into your IDE/agent platform:

```bash
mnemoq-scaffold ./my-project --defaults --ide windsurf
mnemoq-scaffold ./my-project --defaults --ide windsurf,cursor,claude-code
mnemoq-scaffold ./my-project --defaults --ide all
mnemoq-scaffold --ide ?
```

Supported platforms: `opencode`, `windsurf`, `cursor`, `claude-code`, `copilot`, `all`.

### 2. Log a learning

```bash
mnemoq --log '{"step":3,"source_agent":"claude","type":"pattern","domain":"backend","components":["api","auth"],"files_touched":["src/auth.py"],"trigger":"JWT validation failed on expired tokens","action":"Added explicit expiry check before signature verification","reason":"PyJWT silently accepts expired tokens when verify_exp is not set","importance":8,"severity":"major"}'
```

PowerShell-safe alternative (avoids JSON quoting issues):

```bash
mnemoq --log-file learning.json
```

### 3. Retrieve relevant learnings

```bash
mnemoq --step 3 --components api,auth --domain backend
```

### 4. Other commands

```bash
mnemoq --stats                          # Memory statistics
mnemoq --resolve 2025-06-25T10:30:00    # Mark a learning resolved
mnemoq --review-agents --step 3         # AGENTS.md section health report
mnemoq --consolidate                    # Archive + promote (sleep cycle)
mnemoq --install-hooks                  # Install git post-commit auto-learn hook
```

For the full **retrieve → work → log → evaluate → auto-learn** loop and how to
wire it into any IDE or agent, see the [Integration Guide](docs/integration-guide.md).

### 5. MCP server

MCP is the primary integration path for AI agents. The server runs over stdio (JSON-RPC 2.0) with no HTTP dependency.

```bash
mnemoq-mcp                                # auto-discovers memory/ in cwd
mnemoq-mcp --memory-dir /path/to/memory   # explicit path
```

Or via environment variable: `AGENT_MEMORY_DIR=/path/to/memory mnemoq-mcp`

**Tools exposed:** `retrieve_learnings`, `log_learning`, `resolve_learning`, `get_stats`, `consolidate`

Works with Claude Desktop, Cursor, Windsurf, VS Code, and any MCP-compatible client. See the [full MCP integration guide](docs/mcp-integration.md) for client configuration snippets, tool reference, and troubleshooting.

## CLI Reference

| Command | Description |
|---------|-------------|
| `mnemoq` | Log, retrieve, consolidate, and manage agent memories |
| `mnemoq-scaffold` | Initialize a new project with memory directory and config |
| `mnemoq-update` | Update engine files in existing projects |
| `mnemoq-mcp` | Start MCP server (JSON-RPC over stdio) |
| `scripts/generate_fakes.py` | Generate synthetic memory entries for testing |

See [docs/cli-reference.md](docs/cli-reference.md) for all flags, examples, and mutual-exclusion rules.

## Configuration

`memory/config.json` tunes retrieval scoring, retention, embeddings, reranking, and access control for your project. Below is a summary of all parameters — see the [full Config Tuning Guide](docs/config-tuning.md) for ranges, defaults, and tuning recipes.

| Parameter | Default | What it controls |
|-----------|---------|-------------------|
| `project_name` | `"<PROJECT_NAME>"` | Project identifier |
| `engine_min_version` | `"1.15.0"` | Minimum engine version |
| `schema_version` | `1` | Config schema version |
| `max_step` | `null` | Cap on step values (`null` = no cap) |
| `valid_domains` | `null` | Accepted domain whitelist |
| `valid_source_agents` | `null` | Accepted agent whitelist |
| `retrieval_only_agents` | `null` | Agents that can retrieve but not log |
| `domain_mappings` | `null` | Custom domain → canonical tag mappings |
| `api_key` | `null` | HTTP API auth key (`null` = no auth) |
| `embedding_model` | `"all-MiniLM-L6-v2"` | sentence-transformers model name |
| `embedding_cache_dir` | `"~/.agent-memory/models/"` | Model file cache path |
| `reranker` | `"none"` | Reranker mode: `none`, `cross-encoder`, `llm-local` |
| `reranker_top_n` | `20` | Number of top results to rerank |
| `reranker_model` | `"cross-encoder/ms-marco-MiniLM-L-12-v2"` | Cross-encoder model name |
| `reranker_llm_endpoint` | `null` | LLM endpoint URL for `llm-local` mode |
| `reranker_llm_model` | `null` | LLM model name for `llm-local` mode |
| `tuning.decay_rate` | `0.995` | Exponential decay per step (recency) |
| `tuning.score_threshold` | `0.15` | Minimum score for non-critical candidates |
| `tuning.component_weight` | `1.0` | Weight when task components match |
| `tuning.file_weight` | `0.7` | Weight when task files match |
| `tuning.domain_weight` | `0.4` | Weight when domain matches |
| `tuning.no_match_weight` | `0.1` | Weight when nothing matches |
| `tuning.max_warnings` | `5` | Max critical entries per retrieval |
| `tuning.max_patterns` | `15` | Max non-critical entries per retrieval |
| `tuning.minor_retention` | `5` | Step window for minor entries |
| `tuning.major_retention` | `20` | Step window for major entries |
| `tuning.escalation_threshold` | `30` | Step age for escalation flagging |
| `tuning.bm25_k1` | `1.5` | BM25 term frequency saturation |
| `tuning.bm25_b` | `0.75` | BM25 document length normalization |
| `tuning.rrf_k` | `60` | Reciprocal rank fusion constant |
| `tuning.embedding_alpha` | `0.5` | Blend weight: `alpha * rrf + (1-alpha) * cosine` |
| `tuning.semantic_dedup_threshold` | `0.85` | Cosine similarity for duplicate detection |
| `tuning.sleep_cycle_days` | `1` | Days between consolidation triggers |
| `tuning.sleep_cycle_quarantine_threshold` | `20` | Quarantine count that triggers consolidation |

## Data Schema

Each entry in `learnings.jsonl` is a JSON object with these required fields:

| Field | Type | Constraint |
|-------|------|-----------|
| `step` | `int` | ≥ 1 |
| `source_agent` | `str` | must be a valid agent name |
| `type` | `str` | `bug_fix`, `optimization`, or `architectural_pattern` |
| `domain` | `str` | e.g. `backend`, `testing`, `security` |
| `components` | `list[str]` | non-empty |
| `files_touched` | `list[str]` | non-empty |
| `trigger` | `str` | must start with `When` |
| `action` | `str` | must contain `ALWAYS` or `NEVER` |
| `reason` | `str` | non-empty |
| `importance` | `int` | 1–10 |
| `severity` | `str` | `minor`, `major`, or `critical` |

The engine auto-stamps `ts`, `commit`, `access_count`, `reinforcement_count`, `embedding`, `schema_version`, and provenance fields at log time. See [docs/data-schema.md](docs/data-schema.md) for the full reference including optional fields, enum values, schema versioning, and sample entries.

## Development

```bash
git clone https://github.com/Mnemoq/MnemoQ.git
cd MnemoQ
pip install -e ".[dev]"
pytest
```

## Structure

- `src/agent_memory/` — Engine source (CLI, retrieval, validation, consolidation, MCP server, dashboard, SDK)
- `src/agent_memory/engine/` — Core modules (retrieval, scoring, reranking, consolidation, validation, server)
- `tests/` — Test suite
- `templates/` — Config templates, prompts, eval data
- `docs/` — Architecture documentation ([index](docs/README.md))
- `scripts/` — Deploy scripts

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for current status and planned features.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE) for details.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Submitting a PR constitutes acceptance of the [CLA](CLA.md).

## Security

Report vulnerabilities privately via [GitHub Security Advisories](https://github.com/Mnemoq/MnemoQ/security/advisories/new). See [SECURITY.md](SECURITY.md) for details.
