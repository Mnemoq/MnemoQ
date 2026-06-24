#!/usr/bin/env python3
"""Entry point for the MCP server.

Usage:
    python -m mcp_main
    python mcp_main.py
    python mcp_main.py --memory-dir /path/to/memory

Reads AGENT_MEMORY_DIR env or discovers memory/ in cwd.
"""

from __future__ import annotations

import sys

from agent_memory.engine.mcp_server import run_server


def main():
    memory_dir = None
    args = sys.argv[1:]
    if "--memory-dir" in args:
        idx = args.index("--memory-dir")
        if idx + 1 < len(args):
            memory_dir = args[idx + 1]
    run_server(memory_dir)


if __name__ == "__main__":
    main()
