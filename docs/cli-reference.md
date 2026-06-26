# CLI Reference

Complete reference for all MnemoQ command-line tools.

---

## `mnemoq` — Main CLI

Log, retrieve, consolidate, and manage agent memories.

### Retrieval Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--step` | int | — | Current plan step number (config-dependent: `1`–`max_step` if set, otherwise no upper bound) |
| `--components` | str | — | Comma-separated component names |
| `--files` | str | — | Comma-separated file paths |
| `--domain` | str | — | Coarse domain tag |
| `--no-profile` | flag | — | Skip developer profile loading (for deterministic output) |

```bash
mnemoq --step 3 --components api,auth --files src/auth.py --domain backend
mnemoq --step 3 --components api,auth --domain backend --no-profile
```

### Logging Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--log` | str | — | JSON string to log |
| `--log-file` | str | — | Path to JSON file to log (PowerShell-safe alternative to `--log`) |
| `--update` | str | — | Timestamp of existing entry to amend (requires `--log` or `--log-file`) |
| `--resolve` | str | — | Timestamp of existing entry to mark as resolved |

```bash
mnemoq --log '{"step":3,"source_agent":"claude","type":"pattern",...}'
mnemoq --log-file learning.json
mnemoq --update 2026-06-11T22:00:00Z --log-file learning.json
mnemoq --resolve 2026-06-11T22:00:00Z
```

### Lifecycle Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--consolidate` | flag | — | Sleep Cycle: archive learnings, generate promotion report |
| `--sprint` | int | inferred | Sprint number for `--consolidate` (default: inferred from max step) |
| `--confirm-reset` | flag | — | Clear `learnings.jsonl` after review (requires `--consolidate`) |
| `--force` | flag | — | Force overwrite existing archive in `--consolidate` |

```bash
mnemoq --consolidate
mnemoq --consolidate --sprint 2 --force
mnemoq --consolidate --confirm-reset
```

### Diagnostics Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--stats` | flag | — | Print memory system statistics |
| `--review-agents` | flag | — | Diagnostic report on `AGENTS.md` section health (requires `--step`) |
| `--threshold` | int | config-dependent | Step window for `--review-agents` (default: `major_retention` from config) |
| `--verify` | flag | — | Validate every entry in `learnings.jsonl` against schema |

```bash
mnemoq --stats
mnemoq --review-agents --step 3
mnemoq --review-agents --step 3 --threshold 10
mnemoq --verify
```

### Metrics Flags

| Flag | Type | Default | Description | Requires `--metrics`? |
|------|------|---------|-------------|----------------------|
| `--metrics` | flag | — | Print metrics summary report | — |
| `--metrics-retrieval` | flag | — | Deep-dive on retrieval effectiveness | Yes |
| `--metrics-logging` | flag | — | Deep-dive on logging patterns | Yes |
| `--metrics-consolidation` | flag | — | Consolidation history | Yes |
| `--metrics-trend` | flag | — | Time-series trend (last 30 days) | Yes |
| `--metrics-all-projects` | flag | — | Cross-project comparison across all registered projects | Modifier |
| `--metrics-json` | flag | — | Output metrics as JSON (for piping to jq/scripts) | Modifier |
| `--metrics-since` | str | — | Only include events on or after this date (`YYYY-MM-DD`) | Modifier |
| `--metrics-export` | str | — | Export raw metrics events to a file (JSONL format) | Modifier |

> **Note:** `--metrics-retrieval`, `--metrics-logging`, `--metrics-consolidation`, and `--metrics-trend` are **enforced** — they error if `--metrics` is not set. The modifier flags (`--metrics-all-projects`, `--metrics-json`, `--metrics-since`, `--metrics-export`) are silently ignored without `--metrics` since the metrics handler is never called.

```bash
mnemoq --metrics
mnemoq --metrics --metrics-retrieval --metrics-json
mnemoq --metrics --metrics-trend --metrics-since 2026-01-01 --metrics-export events.jsonl
```

### Eval Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--eval` | flag | — | Run grading harness: test retrieval quality against `memory/eval/grading.jsonl` |

```bash
mnemoq --eval
```

### Server Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--serve` | flag | — | Start HTTP API server (requires `mnemoq[api]`) |
| `--dashboard` | flag | — | Start HTTP API server with web dashboard UI |
| `--port` | int | `8765` | Port for `--serve`/`--dashboard` |
| `--mcp` | flag | — | Start MCP server (JSON-RPC over stdio) |

```bash
mnemoq --serve --port 9000
mnemoq --dashboard
mnemoq --mcp
```

### Migration Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--migrate-schema` | flag | — | Run schema migration on `learnings.jsonl` and write updated file |

```bash
mnemoq --migrate-schema
```

### Utility Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--version` | flag | — | Show engine version and exit |
| `--memory-dir` | str | `<cwd>/memory` | Path to memory directory |

```bash
mnemoq --version
mnemoq --memory-dir /path/to/memory --stats
```

### Config-Dependent Defaults

Some flags have defaults that change based on `memory/config.json`:

| Flag | Config key | Behavior |
|------|------------|----------|
| `--step` | `max_step` | Help text shows `1`–`max_step` range if set; "no upper bound" if `null` |
| `--threshold` | `tuning.major_retention` | Default step window for `--review-agents` |

### Mutual-Exclusion Rules

Most operational flags cannot be combined with each other. The full rules:

| Flag | Cannot combine with |
|------|---------------------|
| `--version` | `--step`, `--log`, `--log-file`, `--stats`, `--consolidate`, `--review-agents` |
| `--log` | `--log-file` (mutually exclusive) |
| `--log-file` | `--log` (mutually exclusive) |
| `--update` | Requires `--log` or `--log-file` |
| `--stats` | `--step`, `--log`, `--log-file`, `--resolve`, `--update`, `--review-agents`, `--consolidate` |
| `--review-agents` | Requires `--step`; cannot combine with `--log`, `--log-file`, `--resolve`, `--update`, `--consolidate` |
| `--consolidate` | `--step`, `--log`, `--log-file`, `--resolve`, `--update`, `--review-agents`, `--stats` |
| `--confirm-reset` | Requires `--consolidate` |
| `--metrics` | `--step`, `--log`, `--log-file`, `--resolve`, `--update`, `--review-agents`, `--consolidate`, `--stats` |
| `--migrate-schema` | `--step`, `--log`, `--log-file`, `--resolve`, `--update`, `--review-agents`, `--consolidate`, `--stats`, `--metrics` |
| `--eval` | `--step`, `--log`, `--log-file`, `--resolve`, `--update`, `--review-agents`, `--consolidate`, `--stats`, `--metrics`, `--migrate-schema` |
| `--serve` / `--dashboard` | `--step`, `--log`, `--log-file`, `--resolve`, `--update`, `--review-agents`, `--consolidate`, `--stats`, `--metrics`, `--migrate-schema`, `--eval` |
| `--mcp` | `--step`, `--log`, `--log-file`, `--resolve`, `--update`, `--review-agents`, `--consolidate`, `--stats`, `--metrics`, `--migrate-schema`, `--eval`, `--serve`, `--dashboard` |
| `--verify` | `--step`, `--log`, `--log-file`, `--resolve`, `--update`, `--review-agents`, `--consolidate`, `--stats`, `--metrics`, `--migrate-schema`, `--eval`, `--serve`, `--dashboard`, `--mcp` |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `AGENT_MEMORY_DIR` | Path to memory directory. Used as fallback when `--memory-dir` is not passed. Must be an existing directory. |

**Resolution priority:** `--memory-dir` flag → `AGENT_MEMORY_DIR` env var → `<cwd>/memory/` (if it exists) → error.

---

## `mnemoq-scaffold` — Project Scaffolding

Initialize a new project with a `memory/` directory, `config.json`, and `learnings.jsonl`.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `target` (positional) | str | current directory | Target project path |
| `--defaults` | flag | — | Skip prompts, use all defaults |
| `--force` | flag | — | Overwrite engine files only (preserves `learnings.jsonl`) |
| `--ide` | str | — | Wire memory into IDE/agent platform(s): `opencode`, `windsurf`, `cursor`, `claude-code`, `copilot`, `all` (comma-separated). Use `--ide ?` to list platforms. |
| `--version` | flag | — | Show version and exit |

```bash
mnemoq-scaffold ./my-project --defaults
mnemoq-scaffold ./my-project --defaults --ide windsurf
mnemoq-scaffold ./my-project --defaults --ide windsurf,cursor,claude-code
mnemoq-scaffold ./my-project --defaults --ide all
mnemoq-scaffold --ide ?
mnemoq-scaffold . --force
```

Supported platforms:

| Platform | What gets wired |
|----------|----------------|
| `opencode` | Merges `opencode.json`, copies prompts to `.opencode/prompts/`, appends `AGENTS.md` |
| `windsurf` | Copies workflows to `.windsurf/workflows/`, creates `.windsurf/Plans/`, appends `AGENTS.md` |
| `cursor` | Copies `.mdc` rule files to `.cursor/rules/`, appends `AGENTS.md` |
| `claude-code` | Creates/appends `CLAUDE.md` with memory protocol |
| `copilot` | Creates/appends `.github/copilot-instructions.md`, appends `AGENTS.md` |

`--opencode` is kept as a hidden backward-compat alias for `--ide opencode`.

---

## `mnemoq-update` — Engine Update Tool

Update engine files in existing projects to the latest installed version.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--project` | str | all from `projects.txt` | Update specific project |
| `--dry-run` | flag | — | Show what would be updated without making changes |
| `--force` | flag | — | Force update even if versions match |
| `--no-backup` | flag | — | Skip backup creation (default: create backup) |
| `--update-config` | flag | — | Update `config.json` with new schema |
| `--migrate-to-shim` | flag | — | Replace full engine copies with shims |
| `--yes` / `-y` | flag | — | Skip confirmation prompt for multi-project updates |
| `--version` | flag | — | Show update tool version |

```bash
mnemoq-update --dry-run
mnemoq-update --project ./my-project --force
mnemoq-update -y --update-config
```

---

## `mnemoq-mcp` — MCP Server Launcher

Start the MCP server (JSON-RPC over stdio) for integration with AI agents.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--memory-dir` | str | `<cwd>/memory` | Path to memory directory |

> **Note:** `mnemoq-mcp` parses `sys.argv` manually (not argparse), so `--help` is not available. Falls back to `AGENT_MEMORY_DIR` environment variable if `--memory-dir` is not passed.

```bash
mnemoq-mcp
mnemoq-mcp --memory-dir /path/to/memory
```

---

## Developer Tools

### `scripts/generate_fakes.py` — Synthetic Memory Generator

Generate synthetic memory entries for stress-testing the retrieval pipeline and consolidation logic.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--count` | int | required | Number of entries to generate |
| `--pipeline` | flag | — | Route each entry through `mnemoq --log-file` |
| `--stop-on-error` | flag | — | Halt on first pipeline failure |
| `--type` | str | — | Restrict to one type |
| `--domain` | str | — | Restrict to one domain |
| `--source-agent` | str | — | Restrict to one source agent |
| `--target` | str | — | Output file (direct mode only) |
| `--clean` | flag | — | Delete target file before generating |
| `--embed` | flag | — | Compute embeddings in direct mode |
| `--step-mode` | str | `sequential` | `sequential` \| `random` \| `clustered` |
| `--max-step` | int | config `max_step` or `30` | Max step cap |
| `--days-back` | int | `30` | Spread timestamps over N days (direct mode only) |
| `--duplicates` | float | `0` | Percentage of entries that are near-duplicates |
| `--resolved` | float | `5` | Percentage of entries marked resolved |
| `--seed` | int | — | Random seed for reproducible generation |
| `--dry-run` | flag | — | Validate and print summary without writing anything |
| `--confirm` | flag | — | Required to use `--pipeline` without `--dry-run` (safety guard) |
| `--memory-dir` | str | — | Memory directory (passed to `mnemoq`) |

```bash
python scripts/generate_fakes.py --count 100 --clean
python scripts/generate_fakes.py --count 50 --pipeline --dry-run
python scripts/generate_fakes.py --count 200 --duplicates 10 --resolved 15 --seed 42
```
