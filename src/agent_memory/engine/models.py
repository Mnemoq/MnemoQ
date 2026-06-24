"""Pydantic models for the memory engine API layer.

Shared across HTTP server (server.py), MCP server (mcp_server.py), and SDK.
Single source of truth for request/response schemas.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LearningEntry(BaseModel):
    """Full learning entry schema. Fields match validation.py required fields."""

    step: int = Field(ge=1)
    source_agent: str
    type: str
    domain: str
    components: list[str] = Field(min_length=1)
    files_touched: list[str] = Field(min_length=1)
    trigger: str
    action: str
    reason: str
    importance: int = Field(ge=1, le=10)
    severity: str
    # Optional fields
    scope: str | None = None
    verified: bool | None = None
    symptoms: str | None = None
    debt_level: str | None = None
    schema_version: int | None = None
    access_count: int | None = None
    reinforcement_count: int | None = None
    commit: str | None = None
    embedding: str | None = None
    project_id: str | None = None
    origin_project: str | None = None
    contributing_projects: list[str] | None = None
    contributors: list[str] | None = None

    model_config = {"extra": "allow"}


class LogRequest(BaseModel):
    """POST /api/log request body."""
    entry: dict[str, Any]


class UpdateRequest(BaseModel):
    """POST /api/update request body."""
    ts: str
    entry: dict[str, Any]


class ResolveRequest(BaseModel):
    """POST /api/resolve request body."""
    ts: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class ConsolidateRequest(BaseModel):
    """POST /api/consolidate request body."""
    sprint_number: int | None = None
    force: bool = False


class ErrorResponse(BaseModel):
    """Error response for all non-2xx responses."""
    code: str
    message: str
    suggested_action: str = ""
    entry_ref: str | None = None


class StatsResponse(BaseModel):
    """GET /api/stats response."""
    total: int
    unresolved: int
    resolved: int
    avg_access_count: float
    avg_reinforcement_count: float
    step_range: list[int]
    severity_breakdown: dict[str, int]
    type_breakdown: dict[str, int]
    scope_breakdown: dict[str, int]
    debt_breakdown: dict[str, int]
    verified: int
    unverified: int
    proven: int
    over_injected: int
    under_retrieved: int
    sleep_cycle_due: bool = False
