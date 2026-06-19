"""Shim template and utilities for delegating to central engine."""

# Sentinel comment for exact shim detection
SHIM_SENTINEL = "# AGENT-MEMORY-SHIM v1"

SHIM_TEMPLATE = f'''#!/usr/bin/env python3
{SHIM_SENTINEL}
"""Thin shim that delegates to the central engine."""
import os
import sys

# Set AGENT_MEMORY_DIR so the engine knows where to find data
os.environ["AGENT_MEMORY_DIR"] = os.path.dirname(os.path.abspath(__file__))

# Exec the central engine
engine_path = os.path.expanduser("~/.agent-memory/engine/filter.py")
if not os.path.exists(engine_path):
    print(f"ERROR: Engine not found at {{engine_path}}", file=sys.stderr)
    print("Run the deploy script to install the engine.", file=sys.stderr)
    sys.exit(1)

# Replace this process with the engine
# Note: On Windows, os.execv spawns a new process (PID changes).
# On Unix, it replaces the current process in-place.
os.execv(sys.executable, [sys.executable, engine_path] + sys.argv[1:])
'''


def is_shim(file_path):
    """Check if a file is a shim (vs full engine copy)."""
    if not file_path.exists():
        return False
    try:
        first_line = file_path.read_text(encoding='utf-8').split('\n', 2)[1]
        return first_line.strip() == SHIM_SENTINEL
    except (OSError, UnicodeDecodeError, IndexError):
        return False
