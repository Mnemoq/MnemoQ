#!/usr/bin/env python3
# AGENT-MEMORY-SHIM v1
"""Thin shim that delegates to the central engine."""
import os
import sys

# Set AGENT_MEMORY_DIR so the engine knows where to find data
os.environ["AGENT_MEMORY_DIR"] = os.path.dirname(os.path.abspath(__file__))

# Exec the central engine
engine_path = os.path.expanduser("~/.agent-memory/engine/filter.py")
if not os.path.exists(engine_path):
    print(f"ERROR: Engine not found at {engine_path}", file=sys.stderr)
    print("Run the deploy script to install the engine.", file=sys.stderr)
    sys.exit(1)

# Replace this process with the engine
# Note: On Windows, os.execv spawns a new process (PID changes).
# On Unix, it replaces the current process in-place.
os.execv(sys.executable, [sys.executable, engine_path] + sys.argv[1:])
