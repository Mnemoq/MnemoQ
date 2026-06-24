"""
Engine Version — Single source of truth for version constant.

Reads from VERSION file, falls back to hardcoded value if file missing.

Resolution order:
1. Deployed engine: ~/.agent-memory/engine/VERSION
2. Repo root: <this-file>/../VERSION (during development)
3. Hardcoded fallback: FALLBACK_VERSION
"""
from pathlib import Path

# Hardcoded fallback — update this when bumping version
FALLBACK_VERSION = "1.20.4"

def get_engine_version() -> str:
    """Read engine version from VERSION file, with fallback chain."""
    # 1. Deployed engine location
    version_file = Path.home() / ".agent-memory" / "engine" / "VERSION"
    if version_file.exists():
        try:
            version = version_file.read_text().strip()
            if version:
                return version
        except Exception:
            pass
    
    # 2. Repo root VERSION (during development, before deploy)
    repo_version = Path(__file__).parent.parent / "VERSION"
    if repo_version.exists():
        try:
            version = repo_version.read_text().strip()
            if version:
                return version
        except Exception:
            pass
    
    # 3. Hardcoded fallback
    return FALLBACK_VERSION
