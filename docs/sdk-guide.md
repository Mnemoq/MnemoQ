# Python SDK Guide

Programmatic access to the MnemoQ memory engine — log, retrieve, update, resolve, consolidate, and read metrics from Python code.

**Source of truth:** [`sdk/client.py`](../src/agent_memory/sdk/client.py), [`sdk/exceptions.py`](../src/agent_memory/sdk/exceptions.py)

---

## Installation

```bash
pip install mnemoq
```

`httpx` is included as a core dependency of `mnemoq` — no separate install needed.

---

## Quick Start

### Local file access

```python
from agent_memory.sdk import MemoryClient

client = MemoryClient(memory_dir="/path/to/project/memory")

# Log a learning
client.log({
    "step": 3,
    "source_agent": "gm",
    "type": "bug_fix",
    "domain": "backend",
    "components": ["API", "Auth"],
    "files_touched": ["src/auth.py"],
    "trigger": "When JWT validation fails on expired tokens",
    "action": "ALWAYS check token expiry before signature verification",
    "reason": "PyJWT silently accepts expired tokens when verify_exp is not set",
    "importance": 8,
    "severity": "major",
})

# Retrieve relevant learnings
results = client.retrieve(step=3, components=["API"], domain="backend")
print(results["total_entries"], "matching entries")
```

### HTTP API access

```python
from agent_memory.sdk import MemoryClient

client = MemoryClient(
    base_url="http://localhost:8765",
    api_key="your-secret-key",  # or set AGENT_MEMORY_API_KEY env var
)

results = client.retrieve(step=3, components=["API"])
client.close()  # close the underlying HTTP connection pool
```

### Async usage

```python
import asyncio
from agent_memory.sdk import AsyncMemoryClient

async def main():
    async with AsyncMemoryClient(base_url="http://localhost:8765") as client:
        await client.log({...})
        results = await client.retrieve(step=3, components=["API"])
        print(results)

asyncio.run(main())
```

Local file operations in `AsyncMemoryClient` run via `asyncio.to_thread`, so they won't block the event loop.

---

## Transports

Both `MemoryClient` and `AsyncMemoryClient` support two transports, selected at construction time:

| Transport | Constructor arg | Description |
|-----------|----------------|-------------|
| **Local** | `memory_dir` | Direct file access via engine `*_core` functions. No server needed. |
| **HTTP** | `base_url` | REST API calls via `httpx`. Requires a running MnemoQ server. |

You must provide exactly one of `memory_dir` or `base_url`. Providing both (or neither) raises `ValueError`.

### Authentication

For HTTP transport, pass `api_key` explicitly or set the `AGENT_MEMORY_API_KEY` environment variable. The key is sent as the `X-API-Key` header.

---

## Clients

### `MemoryClient`

Synchronous client. Supports both local and HTTP transports.

```python
MemoryClient(
    *,
    memory_dir: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
)
```

Context manager supported — closes the HTTP connection pool on exit:

```python
with MemoryClient(base_url="http://localhost:8765") as client:
    client.log({...})
```

### `AsyncMemoryClient`

Asynchronous client. HTTP operations use `httpx.AsyncClient`; local file operations run in a thread.

```python
AsyncMemoryClient(
    *,
    memory_dir: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
)
```

Async context manager supported:

```python
async with AsyncMemoryClient(base_url="http://localhost:8765") as client:
    await client.log({...})
```

---

## Methods

Both clients expose the same API surface. `MemoryClient` methods are synchronous; `AsyncMemoryClient` methods are `async`.

### `retrieve(step, *, components=None, files=None, domain=None)`

Retrieve learnings relevant to the current context.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `step` | `int` | Yes | Current plan step number |
| `components` | `list[str]` | No | Component names to filter by |
| `files` | `list[str]` | No | File paths to filter by |
| `domain` | `str` | No | Domain tag to filter by |

**Returns:** `dict` with `total_entries` (int) and `entries` (list of entry dicts).

```python
results = client.retrieve(
    step=5,
    components=["CollisionSystem", "Rigidbody"],
    files=["src/physics/collision.py"],
    domain="backend",
)
```

### `log(entry)`

Log a new learning entry. The entry is validated client-side via Pydantic before being sent.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `entry` | `dict[str, Any]` | Yes | Learning entry dict (see [Data Schema](data-schema.md) for required fields) |

**Returns:** `dict` with `status` (`"added"`) and `entry` (the stamped entry, including auto-generated `ts`).

**Raises:** `ValidationError` if required fields are missing or invalid.

```python
result = client.log({
    "step": 1,
    "source_agent": "gm",
    "type": "bug_fix",
    "domain": "tooling",
    "components": ["Tests"],
    "files_touched": ["test_auth.py"],
    "trigger": "When auth tests fail intermittently",
    "action": "ALWAYS check test database state before each run",
    "reason": "Shared DB causes flaky tests",
    "importance": 6,
    "severity": "minor",
})
ts = result["entry"]["ts"]  # auto-stamped timestamp
```

### `update(ts, entry)`

Amend an existing learning entry by timestamp.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `ts` | `str` | Yes | Timestamp of the entry to update (`YYYY-MM-DDTHH:MM:SSZ`) |
| `entry` | `dict[str, Any]` | Yes | Updated entry dict (validated client-side) |

**Returns:** `dict` with `status` (`"updated"`) and `entry` (the updated entry).

**Raises:** `NotFoundError` if no entry matches `ts`. `ValidationError` if the updated entry is invalid.

```python
client.update(ts, {**original_entry, "action": "NEVER trust user input without validation"})
```

### `resolve(ts)`

Mark a learning entry as resolved.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `ts` | `str` | Yes | Timestamp of the entry to resolve |

**Returns:** `dict` with `status` (`"resolved"`) and `entry` (the resolved entry with `resolved: true`).

**Raises:** `NotFoundError` if no entry matches `ts`.

```python
client.resolve("2026-01-15T10:00:00Z")
```

### `stats()`

Get aggregate memory statistics.

**Returns:** `dict` with `total`, `unresolved`, `resolved`, `avg_access_count`, `avg_reinforcement_count`, `step_range`, `severity_breakdown`, `type_breakdown`, `scope_breakdown`, `debt_breakdown`, `verified`, `unverified`, `proven`, `over_injected`, `under_retrieved`, `sleep_cycle_due`, `sleep_cycle_reasons`.

```python
stats = client.stats()
print(f"{stats['total']} entries, {stats['unresolved']} unresolved")
```

### `metrics(*, since=None, type=None)`

Read operational metrics (retrieval, logging, consolidation events).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `since` | `str` or `datetime` | No | Date filter (inclusive). String format: `YYYY-MM-DD` |
| `type` | `str` | No | Filter by event type: `"retrieval"`, `"log"`, `"consolidate"`, `"stats"`, `"review_agents"` |

**Returns:** `dict` with `total_events` and type-specific breakdowns (`retrieval`, `logging`, `consolidation`), or a filtered `events` list when `type` is specified.

```python
# All metrics
metrics = client.metrics()

# Retrieval metrics since a date
metrics = client.metrics(since="2026-01-01", type="retrieval")
```

### `consolidate(*, sprint_number=None, force=False)`

Consolidate learnings into a sprint archive.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `sprint_number` | `int` | No | Sprint number for the archive file. Auto-numbered if omitted. |
| `force` | `bool` | No | Force consolidation even if thresholds aren't met (default: `False`) |

**Returns:** `dict` with `status` (`"reported"` on success, `"no_entries"` if nothing to archive) and consolidation details.

**Raises:** `ConflictError` if an archive already exists for the given sprint.

```python
result = client.consolidate(sprint_number=1, force=True)
```

### `close()`

Close the underlying HTTP connection pool. No-op for local transport.

- `MemoryClient.close()` — synchronous
- `AsyncMemoryClient.close()` — async (must be `await`ed)

Called automatically when used as a context manager.

---

## Exceptions

All SDK exceptions inherit from `MemoryError`. Import from `agent_memory.sdk`:

```python
from agent_memory.sdk import (
    MemoryError,
    ValidationError,
    NotFoundError,
    ConflictError,
    APIError,
)
```

| Exception | HTTP Status | When raised |
|-----------|-------------|-------------|
| `ValidationError` | 400, 422 | Entry fails schema validation, invalid timestamp, quarantined entry |
| `NotFoundError` | 404 | Referenced entry (by `ts`) does not exist |
| `ConflictError` | 409 | Archive already exists for the given sprint |
| `APIError` | 401, 500, other | Authentication failure, server error, or unexpected HTTP status |

Every exception carries structured fields:

| Field | Type | Description |
|-------|------|-------------|
| `message` | `str` | Human-readable error message |
| `code` | `str` | Machine-readable error code (e.g. `"VALIDATION_ERROR"`, `"NOT_FOUND"`) |
| `suggested_action` | `str` | Recommended fix (may be empty) |
| `entry_ref` | `str` or `None` | Timestamp of the related entry, if applicable |

```python
from agent_memory.sdk import MemoryClient, ValidationError

try:
    client.log({"step": 1})  # missing required fields
except ValidationError as e:
    print(f"[{e.code}] {e.message}")
    if e.suggested_action:
        print(f"Suggested: {e.suggested_action}")
```

---

## Entry Schema

See the [Data Schema Reference](data-schema.md) for the complete field list, enum values, and validation rules.

**Required fields at minimum:** `step`, `source_agent`, `type`, `domain`, `components`, `files_touched`, `trigger`, `action`, `reason`, `importance`, `severity`.

The `trigger` field must start with "When" (case-insensitive). The `action` field must contain "ALWAYS" or "NEVER" (case-insensitive).

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `AGENT_MEMORY_API_KEY` | API key for HTTP transport (used if `api_key` not passed to constructor) |

---

## Examples

### Full lifecycle with local transport

```python
from agent_memory.sdk import MemoryClient

client = MemoryClient(memory_dir="./memory")

# Log
result = client.log({
    "step": 2,
    "source_agent": "code-reviewer",
    "type": "architectural_pattern",
    "domain": "api",
    "components": ["Router", "Middleware"],
    "files_touched": ["src/router.py"],
    "trigger": "When adding new API routes",
    "action": "ALWAYS register middleware before route handlers",
    "reason": "Order matters for auth middleware",
    "importance": 7,
    "severity": "major",
    "scope": "module",
})
ts = result["entry"]["ts"]

# Retrieve
learnings = client.retrieve(step=2, components=["Router"])

# Update
client.update(ts, {
    "step": 2,
    "source_agent": "code-reviewer",
    "type": "architectural_pattern",
    "domain": "api",
    "components": ["Router", "Middleware"],
    "files_touched": ["src/router.py"],
    "trigger": "When adding new API routes",
    "action": "ALWAYS register middleware before route handlers and document the order",
    "reason": "Order matters for auth middleware; undocumented order caused a production incident",
    "importance": 9,
    "severity": "critical",
})

# Resolve once the pattern is established
client.resolve(ts)

# Check stats
stats = client.stats()
print(f"{stats['resolved']} resolved, {stats['unresolved']} open")
```

### Async with HTTP transport

```python
import asyncio
from agent_memory.sdk import AsyncMemoryClient

async def main():
    async with AsyncMemoryClient(
        base_url="https://memory.example.com",
        api_key="sk-...",
    ) as client:
        # Log from multiple agents concurrently
        entries = [
            {"step": 1, "source_agent": "gm", "type": "bug_fix", ...},
            {"step": 1, "source_agent": "test-writer", "type": "bug_fix", ...},
        ]
        results = await asyncio.gather(*[client.log(e) for e in entries])

        # Retrieve
        learnings = await client.retrieve(step=1, domain="testing")
        for entry in learnings["entries"]:
            print(entry["ts"], entry["action"])

asyncio.run(main())
```
