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
```

### 5. MCP server

```bash
mnemoq-mcp
mnemoq-mcp --memory-dir /path/to/memory
```

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
- `docs/` — Architecture documentation
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
