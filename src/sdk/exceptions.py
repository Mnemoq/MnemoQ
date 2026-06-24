"""Typed exceptions for the Agent Memory SDK."""

from __future__ import annotations


class MemoryError(Exception):
    """Base exception for SDK errors."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        suggested_action: str = "",
        entry_ref: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.suggested_action = suggested_action
        self.entry_ref = entry_ref


class ValidationError(MemoryError):
    """Raised when an entry or request fails validation."""


class NotFoundError(MemoryError):
    """Raised when a referenced learning entry does not exist."""


class ConflictError(MemoryError):
    """Raised when an operation conflicts with existing state."""


class APIError(MemoryError):
    """Raised for unexpected HTTP or internal server errors."""
