# Copyright (C) 2026 Mnemoq
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Agent Memory Engine — episodic memory for AI agents."""
from mnemoq.engine_version import get_engine_version

__version__ = get_engine_version()


# Lazy re-exports to avoid importing FastAPI/pydantic at package import time.
def __getattr__(name):
    if name == "MemoryClient":
        from mnemoq.sdk.client import MemoryClient

        return MemoryClient
    if name == "LearningEntry":
        from mnemoq.engine.models import LearningEntry

        return LearningEntry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["MemoryClient", "LearningEntry", "__version__"]