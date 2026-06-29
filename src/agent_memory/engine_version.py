# Copyright (C) 2026 Mnemoq
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Engine Version — Single source of truth for version constant.

Resolution order:
1. Installed package metadata (post-`pip install .`)
2. VERSION file — searched at both candidate locations:
     - repo root: <this-file>/../../../VERSION          (dev layout)
     - deployed engine: <this-file>/../../VERSION      (deploy.ps1 layout)
3. Hardcoded fallback: FALLBACK_VERSION
"""
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path

# Hardcoded fallback — update this when bumping version
FALLBACK_VERSION = "1.21.2"


def _read_version_file() -> str | None:
    # dev: src/agent_memory/engine_version.py -> ../../../VERSION  = repo root
    # deploy: ~/.agent-memory/engine/agent_memory/engine_version.py -> ../../VERSION
    #         = ~/.agent-memory/engine/VERSION
    for candidate in (
        Path(__file__).parent.parent.parent / "VERSION",
        Path(__file__).parent.parent / "VERSION",
    ):
        if candidate.exists():
            try:
                v = candidate.read_text().strip()
                if v:
                    return v
            except Exception:
                continue
    return None


def get_engine_version() -> str:
    """Read engine version, with fallback chain."""
    # 1. Installed package metadata
    try:
        v = pkg_version("mnemoq")
        if v:
            return v
    except PackageNotFoundError:
        pass

    # 2. VERSION file (dev or deployed layout)
    v = _read_version_file()
    if v:
        return v

    # 3. Hardcoded fallback
    return FALLBACK_VERSION
