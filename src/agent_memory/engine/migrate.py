# Copyright (C) 2026 Mnemoq
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Schema migration runner for learning entries.

Migrate-on-read: io.read_learnings() calls migrate_entry() on every entry,
so old entries get new fields backfilled automatically. --migrate-schema
CLI flag does an explicit batch migration that writes the updated file.
"""

from __future__ import annotations

CURRENT_SCHEMA_VERSION = 1


def migrate_v0_to_v1(entry):
    """v0 → v1: add schema_version and fields needed by Steps 3 & 4."""
    entry.setdefault("schema_version", 1)
    entry.setdefault("embedding", None)
    entry.setdefault("project_id", None)
    entry.setdefault("origin_project", None)
    entry.setdefault("contributing_projects", [])
    return entry


MIGRATIONS = {0: migrate_v0_to_v1}


def migrate_entry(entry):
    """Apply migrations in sequence until entry is at CURRENT_SCHEMA_VERSION."""
    version = entry.get("schema_version", 0)
    while version < CURRENT_SCHEMA_VERSION:
        fn = MIGRATIONS.get(version)
        if fn is None:
            break
        entry = fn(entry)
        version += 1
        entry["schema_version"] = version
    return entry


def migrate_all(entries):
    """Batch-migrate a list of entries. Returns (migrated_list, count_migrated)."""
    migrated = []
    count = 0
    for entry in entries:
        old_version = entry.get("schema_version", 0)
        new_entry = migrate_entry(dict(entry))
        if new_entry.get("schema_version", 0) != old_version:
            count += 1
        migrated.append(new_entry)
    return migrated, count


def run_migration(paths):
    """CLI handler: read raw learnings.jsonl, migrate all, write back, print summary."""
    # Function-level import to avoid circular dependency (io.py imports migrate_entry)
    from agent_memory.engine.io import _read_raw_jsonl, write_learnings

    # Raw read — NOT via io.read_learnings (which auto-migrates and would hide the count)
    entries = _read_raw_jsonl(paths.learnings_path)

    if not entries:
        print("No entries found. Nothing to migrate.")
        return 0

    migrated, count = migrate_all(entries)

    write_learnings(paths, migrated)

    print("## SCHEMA MIGRATION COMPLETE")
    print(f"Total entries: {len(migrated)}")
    print(f"Migrated: {count}")
    print(f"Already current: {len(migrated) - count}")
    print(f"Current schema version: {CURRENT_SCHEMA_VERSION}")
    return 0
