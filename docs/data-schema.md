# Data Schema Reference

Canonical reference for the `learnings.jsonl` entry schema. Each line is a JSON object conforming to the fields below.

**Source of truth:** [`validation.py`](../src/agent_memory/engine/validation.py) (`validate_entry`), [`constants.py`](../src/agent_memory/engine/constants.py) (enum values), [`git_utils.py`](../src/agent_memory/engine/git_utils.py) (`stamp_entry`), [`handlers.py`](../src/agent_memory/engine/handlers.py) (`log_core`), [`migrate.py`](../src/agent_memory/engine/migrate.py) (schema versioning).

---

## Required Fields

Fields that must be present in every entry. Validated by `validate_entry()` — missing or invalid values cause quarantine.

| Field | Type | Constraint |
|-------|------|-----------|
| `step` | `int` | ≥ 1; ≤ `max_step` if configured |
| `source_agent` | `str` | must be in `VALID_SOURCE_AGENTS` (configurable via `config.json`) |
| `type` | `str` | must be in `VALID_TYPES` |
| `domain` | `str` | must be in `VALID_DOMAINS` (configurable via `config.json`) |
| `components` | `list[str]` | non-empty |
| `files_touched` | `list[str]` | non-empty |
| `trigger` | `str` | non-empty; must start with `When` (case-insensitive) |
| `action` | `str` | non-empty; must contain `ALWAYS` or `NEVER` (case-insensitive) |
| `reason` | `str` | non-empty |
| `importance` | `int` | 1–10 |
| `severity` | `str` | must be in `VALID_SEVERITIES` |

## Optional Fields

Validated if present, but not required at log time.

| Field | Type | Constraint |
|-------|------|-----------|
| `verified` | `bool` | — |
| `scope` | `str` | must be in `VALID_SCOPES` |
| `symptoms` | `str` | — |
| `debt_level` | `str` | must be in `VALID_DEBT_LEVELS` |
| `schema_version` | `int` | not `bool` |

## Auto-Stamped Fields

Injected by `stamp_entry()` and `log_core()` at log time. You don't need to provide these — the engine sets them automatically.

| Field | Source | Notes |
|-------|--------|-------|
| `ts` | `stamp_entry` | UTC ISO 8601: `YYYY-MM-DDTHH:MM:SSZ` |
| `commit` | `stamp_entry` | git short hash of current HEAD |
| `access_count` | `stamp_entry` | defaults to `0` |
| `reinforcement_count` | `stamp_entry` | defaults to `0`; incremented on semantic-duplicate merge. Validated as non-negative int if explicitly provided. |
| `schema_version` | `log_core` | set to `CURRENT_SCHEMA_VERSION` (currently `1`) |
| `embedding` | `log_core` | base64-encoded embedding vector |
| `project_id` | `log_core` | from `config.json` `project_name` or repo dirname |
| `origin_project` | `log_core` | same as `project_id` at first log time |
| `contributing_projects` | `log_core` | defaults to `[]`; grows via cross-project merge |
| `contributors` | `log_core` | defaults to `[source_agent]`; grows on dedup merge |

## Runtime Fields

Not set at log time, but modified by other CLI operations.

| Field | Type | Source | Notes |
|-------|------|--------|-------|
| `resolved` | `bool` | `--resolve` / `resolve_core()` | Set to `true` when an entry is resolved. Defaults to absent (treated as `false`). Read by retrieval, dedup, consolidation, and metrics. |

## Legacy Fields

| Field | Status | Notes |
|-------|--------|-------|
| `timestamp` | **Deprecated** | Older entries may contain `timestamp` alongside `ts`. `ts` is canonical. `timestamp` is not validated or used by the engine. |

## Allowed Enum Values

From `constants.py`. Types, severities, scopes, and debt levels are **universal** (not configurable) to preserve cross-project learning sharing. Source agents and domains are configurable via `config.json`.

| Enum | Values | Configurable? |
|------|--------|---------------|
| `VALID_TYPES` | `bug_fix`, `optimization`, `architectural_pattern` | No |
| `VALID_DOMAINS` | `ui`, `data`, `tooling`, `performance`, `testing`, `security`, `api`, `backend`, `frontend`, `database`, `deployment`, `documentation` | Yes |
| `VALID_SEVERITIES` | `minor`, `major`, `critical` | No |
| `VALID_SCOPES` | `file`, `module`, `system` | No |
| `VALID_DEBT_LEVELS` | `proper`, `workaround`, `temporary` | No |
| `VALID_SOURCE_AGENTS` | `gm`, `code-reviewer`, `test-writer`, `scout`, `plan-reviewer`, `basic-reviewer`, `pro-reviewer`, `meta-agent`, `fuzzer` | Yes |

## Schema Versioning

- **Current version:** `1`
- **Migrate-on-read:** `io.read_learnings()` auto-migrates entries on every load — old entries get new fields backfilled transparently.
- **Explicit migration:** `mnemoq --migrate-schema` reads raw `learnings.jsonl`, migrates all entries, and writes the updated file.
- **v0 → v1 migration** adds: `schema_version`, `embedding`, `project_id`, `origin_project`, `contributing_projects`

See [`migrate.py`](../src/agent_memory/engine/migrate.py) for migration logic.

## Sample Entries

### Minimal valid entry (what you log)

```json
{
  "step": 1,
  "source_agent": "gm",
  "type": "bug_fix",
  "domain": "tooling",
  "components": ["Test"],
  "files_touched": ["test.py"],
  "trigger": "When running baseline tests",
  "action": "ALWAYS verify output matches expected",
  "reason": "Baseline validation",
  "importance": 5,
  "severity": "minor"
}
```

### Full entry (after auto-stamping)

```json
{
  "step": 5,
  "source_agent": "gm",
  "type": "bug_fix",
  "domain": "tooling",
  "components": ["CollisionSystem", "Rigidbody"],
  "files_touched": ["src/physics/collision.py"],
  "trigger": "When two rigidbodies overlap",
  "action": "ALWAYS check penetration depth before resolving",
  "reason": "Tunneling causes objects to pass through walls",
  "importance": 4,
  "severity": "minor",
  "scope": "module",
  "debt_level": "proper",
  "resolved": false,
  "access_count": 0,
  "reinforcement_count": 0,
  "verified": true,
  "symptoms": "objects pass through walls; collision missed",
  "ts": "2026-01-15T10:00:00Z",
  "commit": "abc1234",
  "schema_version": 1,
  "embedding": null,
  "project_id": "my-project",
  "origin_project": "my-project",
  "contributing_projects": [],
  "contributors": ["gm"]
}
```

## Validation

Run `mnemoq --verify` to check every entry in `learnings.jsonl` against the schema. Invalid entries are reported with line number, timestamp, and error details.
