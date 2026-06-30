# Copyright (C) 2026 Mnemoq
# SPDX-License-Identifier: AGPL-3.0-or-later

#!/usr/bin/env python3
"""
Agent Memory Engine Update Tool

Propagates engine updates (filter.py, profile.py) from the global engine
at ~/.agent-memory/engine/ to all registered projects in projects.txt.

Preserves project-specific data (learnings.jsonl, config.json, etc.)
while updating engine code files.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from mnemoq.engine_version import get_engine_version
from mnemoq.managed_section import sync_managed_section
from mnemoq.scaffold import read_memory_section
from mnemoq.shim import SHIM_TEMPLATE, is_shim

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 0.1


ENGINE_VERSION = get_engine_version()
ENGINE_DIR = Path.home() / ".agent-memory" / "engine"


def is_temp_path(path: Path) -> bool:
    """Check if path is under system temp directory.
    
    Returns True if the resolved path is relative to the system temp dir.
    Returns False on any resolution error (treat as non-temp for safety).
    """
    try:
        temp_dir = Path(tempfile.gettempdir()).resolve()
        return path.resolve().is_relative_to(temp_dir)
    except (ValueError, OSError):
        return False


def migrate_to_shim(project_path, dry_run=False):
    """Replace full engine copy with shim.
    
    Creates backup of existing files, then writes shim.
    Cleans stale __pycache__/ after migration.
    """
    memory_dir = project_path / "memory"
    if not memory_dir.exists():
        return False, "No memory/ directory found"
    
    # Check if already a shim
    filter_path = memory_dir / "filter.py"
    if is_shim(filter_path):
        return True, "Already a shim"
    
    # Create backup
    if not dry_run:
        backup_dir = memory_dir / "backups" / datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        # Backup existing files
        for filename in ["filter.py"]:
            src = memory_dir / filename
            if src.exists():
                shutil.copy2(src, backup_dir / filename)
    
    # Write shim
    if not dry_run:
        filter_path.write_text(SHIM_TEMPLATE, encoding='utf-8')
        
        # Cleanup old profile.py if it exists (now in engine/)
        profile_path = memory_dir / "profile.py"
        if profile_path.exists():
            profile_path.unlink()
        
        # Clean stale __pycache__/
        pycache_dir = memory_dir / "__pycache__"
        if pycache_dir.exists():
            shutil.rmtree(pycache_dir)
    
    return True, "Migrated to shim"


def load_projects(dry_run=False):
    """Load list of project paths from projects.txt, auto-pruning temp dirs.
    
    Stale temp entries are removed from projects.txt with a backup created.
    If dry_run=True, skip the prune step (read-only mode).
    """
    projects_file = ENGINE_DIR / "projects.txt"
    if not projects_file.exists():
        return []
    
    projects = []
    stale_entries = []
    all_lines = []
    
    with open(projects_file, encoding='utf-8') as f:
        all_lines = f.readlines()
    
    for line in all_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            projects.append(stripped)  # Keep comments/empty as-is (will be filtered later)
            continue
        p = Path(stripped)
        if is_temp_path(p):
            stale_entries.append(stripped)
        else:
            projects.append(p)
    
    # Auto-prune stale entries (skip during dry-run)
    if stale_entries and not dry_run:
        # Create backup
        backup_path = ENGINE_DIR / f"projects.txt.backup-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(projects_file, backup_path)
        print(f"WARNING: Pruned {len(stale_entries)} temp directory entries:", file=sys.stderr)
        for s in stale_entries:
            print(f"  - {s}", file=sys.stderr)
        print(f"Backup saved to: {backup_path}", file=sys.stderr)
        
        # Atomic rewrite: write to temp, then os.replace
        tmp_path = projects_file.with_suffix('.txt.tmp')
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                for item in projects:
                    if isinstance(item, str):
                        f.write(item + '\n')
                    else:
                        f.write(str(item) + '\n')
            os.replace(tmp_path, projects_file)
        except Exception:
            # Clean up temp file on failure
            if tmp_path.exists():
                tmp_path.unlink()
            raise
    elif stale_entries and dry_run:
        # Just warn during dry-run, don't prune
        print(f"WARNING: Found {len(stale_entries)} temp directory entries (would prune on real run):", file=sys.stderr)
        for s in stale_entries:
            print(f"  - {s}", file=sys.stderr)
    
    # Return only Path objects
    return [p for p in projects if isinstance(p, Path)]


def validate_project(project_path):
    """Validate that a project path is valid."""
    if not project_path.exists():
        return False, "Path does not exist"
    
    memory_dir = project_path / "memory"
    if not memory_dir.exists():
        return False, "memory/ directory does not exist"
    
    filter_py = memory_dir / "filter.py"
    if not filter_py.exists():
        return False, "memory/filter.py does not exist"
    
    return True, "Valid"


def get_project_version(project_path):
    """Get version from project's filter.py by running --version."""
    project_filter = project_path / "memory" / "filter.py"
    if not project_filter.exists():
        return None
    
    try:
        result = subprocess.run(
            ["python", str(project_filter), "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=project_path
        )
        # Parse version from stderr (e.g., "agent-memory-engine v1.15.0")
        if result.returncode == 0:
            match = re.search(r'v(\d+\.\d+\.\d+)', result.stderr)
            if match:
                return match.group(1)
    except Exception:
        pass
    
    return None


def compare_versions(v1, v2):
    """
    Compare two version strings.
    Returns: -1 if v1 < v2, 0 if v1 == v2, 1 if v1 > v2
    """
    def parse_version(v):
        try:
            return tuple(int(x) for x in v.split('.'))
        except (ValueError, AttributeError):
            return (0, 0, 0)
    
    pv1 = parse_version(v1)
    pv2 = parse_version(v2)
    
    if pv1 < pv2:
        return -1
    elif pv1 > pv2:
        return 1
    else:
        return 0


def create_backup(project_path):
    """Create timestamped backup with Windows file-lock retry."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = project_path / "memory" / "backups" / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    memory_dir = project_path / "memory"
    files_to_backup = [
        "filter.py", "config.json",
        "learnings.jsonl", "quarantine.jsonl",
        "SYSTEM_INVARIANTS.md", "HANDOFF.md", ".gitignore"
    ]
    
    for filename in files_to_backup:
        src = memory_dir / filename
        dst = backup_dir / filename
        if src.exists():
            for attempt in range(RETRY_ATTEMPTS):
                try:
                    shutil.copy2(src, dst)
                    break
                except PermissionError:
                    if attempt < RETRY_ATTEMPTS - 1:
                        time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                    else:
                        raise
    
    # Backup archive/ directory with retry logic
    archive_src = memory_dir / "archive"
    archive_dst = backup_dir / "archive"
    if archive_src.exists():
        for attempt in range(RETRY_ATTEMPTS):
            try:
                shutil.copytree(archive_src, archive_dst, dirs_exist_ok=True)
                break
            except PermissionError:
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                else:
                    raise
    
    return backup_dir


def restore_from_backup(project_path, backup_dir):
    """Restore only engine files from backup (preserve project data)."""
    memory_dir = project_path / "memory"
    
    engine_files = ["filter.py"]
    
    for filename in engine_files:
        backup_file = backup_dir / filename
        target_file = memory_dir / filename
        if backup_file.exists():
            for attempt in range(RETRY_ATTEMPTS):
                try:
                    shutil.copy2(backup_file, target_file)
                    break
                except PermissionError:
                    if attempt < RETRY_ATTEMPTS - 1:
                        time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                    else:
                        print(f"  WARNING: Could not restore {filename} (file is read-only or locked)", file=sys.stderr)
                except Exception as e:
                    print(f"  WARNING: Could not restore {filename} ({type(e).__name__}: {e})", file=sys.stderr)
                    break
    
    # config.json, learnings.jsonl, quarantine.jsonl, archive/ are never restored
    # They are project data that may have changed between backup and failure


def deep_merge_add_missing(base, update):
    """Recursively add missing keys from update to base, preserving all existing values."""
    result = base.copy()
    for key, value in update.items():
        if key not in result:
            # Key doesn't exist in base, add it
            result[key] = value
        elif isinstance(result[key], dict) and isinstance(value, dict):
            # Both are dicts, recurse
            result[key] = deep_merge_add_missing(result[key], value)
        # else: key exists in base, preserve it (don't overwrite)
    return result


def update_config_schema(project_path, engine_path, new_version):
    """Recursively merge new schema fields into project config."""
    project_config = project_path / "memory" / "config.json"
    template_config = engine_path / "templates" / "config.json"
    
    if not project_config.exists() or not template_config.exists():
        return False
    
    try:
        with open(project_config, encoding='utf-8') as f:
            project_data = json.load(f)
        with open(template_config, encoding='utf-8') as f:
            template_data = json.load(f)
        
        # Deep merge: add missing fields at any depth, preserve existing values
        merged_data = deep_merge_add_missing(project_data, template_data)
        
        # Bump engine_min_version to new version
        merged_data["engine_min_version"] = new_version
        
        # Write back
        with open(project_config, 'w', encoding='utf-8') as f:
            json.dump(merged_data, f, indent=2)
        
        return True
    except Exception as e:
        print(f"  Error updating config: {e}", file=sys.stderr)
        return False


# Instruction files that may embed the shared memory-protocol block. mnemoq-update
# only REFRESHES files that already exist (create_if_absent=False) — it never wires
# new IDE files into a project that did not opt into them at scaffold time.
_INSTRUCTION_TARGETS = [
    "AGENTS.md",
    "CLAUDE.md",
    ".github/copilot-instructions.md",
    ".cursor/rules/memory-protocol.mdc",
]


def sync_instructions(project_path, version):
    """Refresh the managed Memory block in existing instruction files.

    Returns {rel_path: status} for files that are present (absent files skipped).
    """
    section = read_memory_section()
    results = {}
    for rel in _INSTRUCTION_TARGETS:
        status = sync_managed_section(project_path / rel, section, version,
                                      create_if_absent=False)
        if status != "absent":
            results[rel] = status
    return results


def update_engine_files(project_path, engine_path, dry_run=False):
    """For shim projects, no file updates needed. For legacy copies, migrate to shim."""
    memory_dir = project_path / "memory"
    filter_path = memory_dir / "filter.py"
    
    # Check if it's a shim
    if is_shim(filter_path):
        return True  # Shim, nothing to update
    
    # Legacy copy — migrate to shim
    return migrate_to_shim(project_path, dry_run=dry_run)[0]


def clear_pycache(project_path):
    """Remove stale .pyc files after updating filter.py."""
    pycache_dir = project_path / "memory" / "__pycache__"
    if pycache_dir.exists():
        try:
            shutil.rmtree(pycache_dir)
            return True
        except Exception as e:
            print(f"  Warning: Could not clear __pycache__: {e}", file=sys.stderr)
            return False
    return True


def verify_shim_integrity(project_path):
    """Verify shim file matches template (lightweight check, no subprocess)."""
    filter_path = project_path / "memory" / "filter.py"
    if not filter_path.exists():
        return False
    try:
        content = filter_path.read_text(encoding='utf-8')
        return content == SHIM_TEMPLATE
    except Exception:
        return False


def verify_update(project_path):
    """Run smoke test after update using --stats (deterministic, no learnings dependency)."""
    try:
        result = subprocess.run(
            ["python", "memory/filter.py", "--stats"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=10
        )
        # Check for success: exit code 0, no errors in stderr, and expected output pattern
        if result.returncode != 0:
            return False
        if "ERROR" in result.stderr or "Traceback" in result.stderr:
            return False
        # --stats should always output "MEMORY STATS" header
        return "MEMORY STATS" in result.stdout
    except Exception as e:
        print(f"  Verification failed: {e}", file=sys.stderr)
        return False


def confirm_update(projects, dry_run=False):
    """Show confirmation prompt listing affected projects."""
    if len(projects) == 1:
        return True  # No prompt for single project
    
    print(f"\n{'[DRY RUN] ' if dry_run else ''}Projects to update:")
    for project in projects:
        print(f"  - {project}")
    
    if dry_run:
        return True
    
    response = input(f"\nUpdate {len(projects)} projects? [y/N] ")
    return response.lower() in ['y', 'yes']


def update_project(project_path, engine_version, engine_path, dry_run=False,
                   create_backup_flag=True, update_config_flag=False, force=False,
                   sync_instructions_flag=False):
    """
    Update a single project.
    Returns: (success, status_message, backup_dir_or_None)
    """
    # Validate project
    valid, message = validate_project(project_path)
    if not valid:
        return False, f"Invalid project: {message}", None
    
    # Get versions
    project_version = get_project_version(project_path)
    if project_version is None:
        return False, "Could not determine project version", None
    
    # Compare versions
    cmp = compare_versions(project_version, engine_version)
    
    if cmp == 0 and not force:
        # Even when the engine version matches, allow an instruction-only refresh
        # (e.g. the protocol template changed). Drift-protected per file.
        if sync_instructions_flag and not dry_run:
            instr = sync_instructions(project_path, engine_version)
            changed = {k: v for k, v in instr.items() if v != "current"}
            if changed:
                summary = ", ".join(f"{k}={v}" for k, v in changed.items())
                return True, f"Already up-to-date (v{project_version}); instructions: {summary}", None
        return True, f"Already up-to-date (v{project_version})", None
    
    if cmp > 0:
        if not force:
            return False, (f"Project version (v{project_version}) is newer than "
                           f"engine (v{engine_version}). Use --force to downgrade."), None
        # If force is True, continue with the downgrade
    
    if dry_run:
        return True, f"Would update: v{project_version} -> v{engine_version}", None
    
    # Create backup
    backup_dir = None
    if create_backup_flag:
        try:
            backup_dir = create_backup(project_path)
        except Exception as e:
            return False, f"Backup creation failed: {e}", None
    
    # Update engine files
    try:
        update_engine_files(project_path, engine_path, dry_run=dry_run)
    except Exception as e:
        if backup_dir:
            restore_from_backup(project_path, backup_dir)
        return False, f"Update failed: {e}", backup_dir
    
    # Update config if requested
    config_updated = False
    if update_config_flag:
        config_updated = update_config_schema(project_path, engine_path, engine_version)

    # Refresh managed instruction blocks if requested (existing files only)
    instr_results = {}
    if sync_instructions_flag:
        instr_results = sync_instructions(project_path, engine_version)
    
    # Clear __pycache__ (skip for shim projects - no compiled artifacts)
    if not dry_run:
        filter_path = project_path / "memory" / "filter.py"
        if not is_shim(filter_path):
            clear_pycache(project_path)
    
    # Verify update (shim projects: integrity check; legacy: subprocess verify)
    filter_path = project_path / "memory" / "filter.py"
    if is_shim(filter_path):
        if not verify_shim_integrity(project_path):
            # Shim corrupted, re-write it
            filter_path.write_text(SHIM_TEMPLATE, encoding='utf-8')
    else:
        if not verify_update(project_path):
            if backup_dir:
                restore_from_backup(project_path, backup_dir)
            return False, "Post-update verification failed", backup_dir
    
    status = f"Updated: v{project_version} -> v{engine_version}"
    if config_updated:
        status += " (config updated)"
    if instr_results:
        summary = ", ".join(f"{k}={v}" for k, v in instr_results.items())
        status += f" (instructions: {summary})"

    return True, status, backup_dir


def main():
    parser = argparse.ArgumentParser(
        description="Update agent memory engine in registered projects",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python update.py                          # Update all projects
  python update.py --dry-run                # Preview changes
  python update.py --project /path/to/proj  # Update specific project
  python update.py --force                  # Force update even if versions match
  python update.py --update-config          # Also update config.json schema
  python update.py --migrate-to-shim        # Replace full engine copies with shims
        """
    )
    
    parser.add_argument("--project", type=str, help="Update specific project (default: all from projects.txt)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without making changes")
    parser.add_argument("--force", action="store_true", help="Force update even if versions match")
    parser.add_argument("--no-backup", action="store_true", help="Skip backup creation (default: create backup)")
    parser.add_argument("--update-config", action="store_true",
                        help="Update config.json with new schema (default: False)")
    parser.add_argument("--sync-instructions", action="store_true",
                        help="Refresh the managed Memory block in existing AGENTS.md/CLAUDE.md/"
                             "copilot/cursor files (preserves user content; never creates new files)")
    parser.add_argument("--migrate-to-shim", action="store_true", help="Replace full engine copies with shims")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt for multi-project updates")
    parser.add_argument("--version", action="store_true", help="Show update.py version")
    
    args = parser.parse_args()
    
    if args.version:
        print(f"agent-memory-update v{ENGINE_VERSION}", file=sys.stderr)
        return 0
    
    engine_version = get_engine_version()
    engine_path = ENGINE_DIR
    
    # Determine projects to update
    if args.project:
        projects = [Path(args.project).resolve()]
    else:
        projects = load_projects(dry_run=args.dry_run)
        if not projects:
            print("No projects found in projects.txt", file=sys.stderr)
            return 2
    
    # Remove duplicates
    projects = list(dict.fromkeys(projects))
    
    # Handle --migrate-to-shim
    if args.migrate_to_shim:
        print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Migrating to shim...\n")
        for project in projects:
            valid, message = validate_project(project)
            if not valid:
                print(f"  {project}: SKIP — {message}")
                continue
            success, msg = migrate_to_shim(project, args.dry_run)
            print(f"  {project}: {msg}")
        return 0
    
    # Confirm update
    if not args.yes and not confirm_update(projects, args.dry_run):
        print("Update cancelled.", file=sys.stderr)
        return 0
    
    # Update each project
    updated = 0
    skipped = 0
    failed = 0
    
    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Engine version: v{engine_version}\n")
    
    for project in projects:
        print(f"Project: {project}")
        
        success, status, backup_dir = update_project(
            project,
            engine_version,
            engine_path,
            dry_run=args.dry_run,
            create_backup_flag=not args.no_backup,
            update_config_flag=args.update_config,
            force=args.force,
            sync_instructions_flag=args.sync_instructions
        )
        
        print(f"  Status: {status}")
        if backup_dir:
            print(f"  Backup: {backup_dir}")
        
        if success:
            if "Already up-to-date" in status or "Would update" in status:
                skipped += 1
            else:
                updated += 1
        else:
            failed += 1
        
        print()
    
    # Summary
    print("=" * 60)
    print("Update Summary")
    print("=" * 60)
    print(f"Total projects: {len(projects)}")
    print(f"Updated: {updated}")
    print(f"Skipped: {skipped}")
    print(f"Failed: {failed}")
    
    if failed > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
