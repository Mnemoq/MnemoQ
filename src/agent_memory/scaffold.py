#!/usr/bin/env python3
"""
Agent Memory Scaffold Tool
Creates a working memory/ directory in any project with a single command.

Usage:
    python scaffold.py <target-project-path> [--defaults] [--force] [--ide <platforms>]
    python scaffold.py --version

Flags:
    <target-project-path>  Path to target project (or use current directory)
    --defaults             Skip prompts, use all defaults (non-interactive)
    --force                Overwrite engine files only (never data files)
    --ide <platforms>      Wire memory into IDE/agent platform(s): opencode, windsurf, cursor, claude-code, copilot
    --version              Show version and exit
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from agent_memory.engine_version import get_engine_version
from agent_memory.shim import SHIM_TEMPLATE, is_shim

ENGINE_VERSION = get_engine_version()
ENGINE_DIR = Path.home() / ".agent-memory" / "engine"


def check_prerequisites():
    """Verify engine files exist before scaffolding."""
    required_files = ["filter.py", "templates/config.json"]
    for f in required_files:
        path = ENGINE_DIR / f
        if not path.exists():
            sys.exit(f"ERROR: Engine file missing: {path}\nRun the deploy script first.")


def resolve_target_path(cli_path):
    """Resolve target project path from CLI arg or cwd."""
    if cli_path:
        target = Path(cli_path).resolve()
    else:
        target = Path.cwd()
        # Check if cwd looks like a project
        if not (target / ".git").exists() and not (target / "package.json").exists():
            sys.exit("ERROR: No target path specified and current directory is not a project.\n"
                     "Usage: scaffold.py <target-project-path> or run from a project directory.")
    
    if not target.exists():
        sys.exit(f"ERROR: Target path does not exist: {target}")
    
    return target


def copy_engine_files(target_memory, force):
    """Write shim to target project's memory directory."""
    target_memory.mkdir(parents=True, exist_ok=True)
    
    # Remove profile.py if it exists (no longer needed)
    profile_path = target_memory / "profile.py"
    if profile_path.exists():
        profile_path.unlink()

    # Write shim
    shim_path = target_memory / "filter.py"
    if shim_path.exists() and not force:
        if is_shim(shim_path):
            return  # Already a shim, nothing to do
        # It's an old full copy, overwrite
    
    shim_path.write_text(SHIM_TEMPLATE, encoding='utf-8')


def prompt_project_name(default_name):
    """Prompt for project name with validation."""
    max_retries = 3
    for attempt in range(max_retries):
        name = input(f"Project name [{default_name}]: ").strip()
        if name:
            return name
        if attempt < max_retries - 1:
            print("  (empty input, using default)")
            return default_name
    return default_name


def prompt_tuning_defaults(template_config):
    """Prompt whether to accept default tuning parameters."""
    response = input("Accept default tuning parameters? [Y/n]: ").strip().lower()
    if response in ["", "y", "yes"]:
        return template_config.get("tuning", {})
    
    # Prompt for each tuning parameter
    tuning = {}
    defaults = template_config.get("tuning", {})
    print("\nTuning parameters (press Enter to keep default):")
    for key, default_value in defaults.items():
        value = input(f"  {key} [{default_value}]: ").strip()
        if value:
            # Try to parse as number
            try:
                if "." in value:
                    tuning[key] = float(value)
                else:
                    tuning[key] = int(value)
            except ValueError:
                tuning[key] = value
        else:
            tuning[key] = default_value
    
    return tuning


def _probe_llm_inline():
    """Inline LLM endpoint probe for scaffold (avoids cross-module import)."""
    import urllib.request
    targets = [
        ("http://localhost:11434", "/api/tags"),    # Ollama
        ("http://localhost:1234", "/v1/models"),    # LM Studio
    ]
    for base, path in targets:
        try:
            req = urllib.request.Request(base + path, method="GET")
            urllib.request.urlopen(req, timeout=1)
            return base
        except Exception:
            continue
    return None


def prompt_reranker():
    """Prompt for reranker mode with auto-probe for llm-local."""
    print("\nReranker (optional second-pass relevance scoring):")
    print("  [1] none (default - no overhead)")
    print("  [2] cross-encoder (ms-marco-MiniLM-L-12-v2, ~420MB download)")
    print("  [3] llm-local (Ollama / LM Studio)")
    choice = input("  Choose [1-3, default 1]: ").strip()
    
    if choice == "2":
        return {"reranker": "cross-encoder"}
    elif choice == "3":
        endpoint = _probe_llm_inline()
        if endpoint:
            print(f"  Found local LLM at {endpoint}")
            model = input("  Model name (e.g. llama3, qwen2.5) [optional]: ").strip() or None
            return {"reranker": "llm-local", "reranker_llm_endpoint": endpoint, "reranker_llm_model": model}
        else:
            print("  No local LLM found (tried :11434 and :1234).")
            print("  You can configure manually later in config.json.")
            return {"reranker": "none"}
    return {"reranker": "none"}


def generate_config_interactive(target_name, template_config):
    """Generate config.json with interactive prompts."""
    print(f"\nScaffolding memory for: {target_name}\n")
    
    project_name = prompt_project_name(target_name)
    tuning = prompt_tuning_defaults(template_config)
    reranker_config = prompt_reranker()
    
    config = {
        "project_name": project_name,
        "engine_min_version": ENGINE_VERSION,
        "max_step": None,
        "valid_domains": None,
        "valid_source_agents": None,
        "retrieval_only_agents": None,
        "domain_mappings": None,
        "embedding_model": template_config.get("embedding_model", "all-MiniLM-L6-v2"),
        "embedding_cache_dir": template_config.get("embedding_cache_dir", "~/.agent-memory/models/"),
        "reranker": reranker_config.get("reranker", "none"),
        "reranker_top_n": template_config.get("reranker_top_n", 20),
        "reranker_model": template_config.get("reranker_model", "cross-encoder/ms-marco-MiniLM-L-12-v2"),
        "reranker_llm_endpoint": reranker_config.get("reranker_llm_endpoint"),
        "reranker_llm_model": reranker_config.get("reranker_llm_model"),
        "tuning": tuning
    }
    
    return config


def generate_config_defaults(target_name, template_config):
    """Generate config.json with all defaults (non-interactive)."""
    config = {
        "project_name": target_name,
        "engine_min_version": ENGINE_VERSION,
        "max_step": None,
        "valid_domains": None,
        "valid_source_agents": None,
        "retrieval_only_agents": None,
        "domain_mappings": None,
        "embedding_model": template_config.get("embedding_model", "all-MiniLM-L6-v2"),
        "embedding_cache_dir": template_config.get("embedding_cache_dir", "~/.agent-memory/models/"),
        "reranker": "none",
        "reranker_top_n": template_config.get("reranker_top_n", 20),
        "reranker_model": template_config.get("reranker_model", "cross-encoder/ms-marco-MiniLM-L-12-v2"),
        "reranker_llm_endpoint": None,
        "reranker_llm_model": None,
        "tuning": template_config.get("tuning", {})
    }
    
    return config


def create_starter_files(target_memory):
    """Create empty and starter files."""
    # Empty files
    (target_memory / "learnings.jsonl").touch()
    (target_memory / "quarantine.jsonl").touch()
    
    # Archive directory with .gitkeep
    archive_dir = target_memory / "archive"
    archive_dir.mkdir(exist_ok=True)
    (archive_dir / ".gitkeep").touch()
    
    # SYSTEM_INVARIANTS.md
    invariants_content = """# System Invariants

Consolidated structural rules.
IMMUTABLE during active tasks. Only updated during Sleep Cycle.

(No invariants yet. They will be added during Sleep Cycle consolidation.)
"""
    (target_memory / "SYSTEM_INVARIANTS.md").write_text(invariants_content)
    
    # HANDOFF.md
    from datetime import datetime
    date_str = datetime.now().strftime("%Y-%m-%d")
    handoff_content = f"""# Handoff - {date_str}

## Session Summary
(Update this section at end of each session)

## Next Steps
- [ ] First task
"""
    (target_memory / "HANDOFF.md").write_text(handoff_content)
    
    # .gitignore
    gitignore_content = """__pycache__/
*.pyc
*.tmp
.consolidate_session.json
"""
    (target_memory / ".gitignore").write_text(gitignore_content)
    
    # Eval fixture directory with template
    eval_dir = target_memory / "eval"
    eval_dir.mkdir(exist_ok=True)
    template_eval = ENGINE_DIR / "templates" / "eval" / "grading.jsonl"
    if template_eval.exists():
        (eval_dir / "grading.jsonl").write_text(template_eval.read_text())
    else:
        (eval_dir / "grading.jsonl").touch()


def register_project(target_path):
    """Append project path to projects.txt if not already present."""
    projects_file = ENGINE_DIR / "projects.txt"
    target_str = str(target_path)
    
    # Create file if missing
    if not projects_file.exists():
        projects_file.write_text("# Registered projects — one absolute path per line\n")
    
    # Check for duplicates
    existing = projects_file.read_text().splitlines()
    for line in existing:
        line = line.strip()
        if line and not line.startswith("#") and line == target_str:
            return  # Already registered
    
    # Append
    with open(projects_file, "a") as f:
        f.write(f"{target_str}\n")


def verify_scaffold(target_memory):
    """Run filter.py to verify scaffold works."""
    import subprocess
    
    print("\nVerifying scaffold...")
    try:
        result = subprocess.run(
            [sys.executable, str(target_memory / "filter.py"), "--step", "1", "--domain", "tooling"],
            cwd=target_memory.parent,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0 and "(none)" in result.stdout:
            print("[OK] Verification passed: filter.py runs cleanly")
            return True
        else:
            print(f"[FAIL] Verification failed:")
            print(f"  stdout: {result.stdout}")
            print(f"  stderr: {result.stderr}")
            return False
    except Exception as e:
        print(f"[FAIL] Verification error: {e}")
        return False


def atomic_write_json(path, data):
    """Write JSON atomically: temp file → os.replace(), with Windows retry."""
    temp_path = path.with_suffix('.json.tmp')
    try:
        for attempt in range(3):
            try:
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)
                os.replace(temp_path, path)
                return True
            except PermissionError:
                if attempt < 2:
                    time.sleep(0.1 * (2 ** attempt))
                else:
                    return False
            except Exception:
                return False
    finally:
        if temp_path.exists():
            try:
                os.unlink(temp_path)
            except Exception:
                pass


def merge_opencode_json(target_path):
    """Field-level merge of opencode.json with snippet template."""
    FILTER_BASH_RULE = "python memory/filter.py *"
    
    snippet_path = ENGINE_DIR / "templates" / "opencode-snippet.json"
    opencode_path = target_path / "opencode.json"
    
    # Check template exists
    if not snippet_path.exists():
        print(f"  ERROR: Template missing: {snippet_path}", file=sys.stderr)
        return False, None
    
    with open(snippet_path, encoding='utf-8') as f:
        snippet = json.load(f)
    
    backup_path = None
    if opencode_path.exists():
        try:
            with open(opencode_path, encoding='utf-8') as f:
                existing = json.load(f)
        except json.JSONDecodeError as e:
            print(f"  ERROR: opencode.json is malformed: {e}", file=sys.stderr)
            print("  Fix the JSON syntax and retry.", file=sys.stderr)
            return False, None
        
        # Create backup
        backup_path = target_path / "opencode.json.bak"
        try:
            shutil.copy2(opencode_path, backup_path)
        except Exception as e:
            print(f"  WARNING: Could not create backup: {e}", file=sys.stderr)
            backup_path = None
    else:
        existing = {}
    
    if "instructions" not in existing:
        existing["instructions"] = []
    for instr in snippet.get("instructions", []):
        if instr not in existing["instructions"]:
            existing["instructions"].append(instr)
    
    if "agent" not in existing:
        existing["agent"] = {}
    
    for agent_name, agent_template in snippet.get("agent", {}).items():
        if agent_name not in existing["agent"]:
            existing["agent"][agent_name] = agent_template
        else:
            existing_agent = existing["agent"][agent_name]
            
            # If agent exists but has no permission key, deep-merge template permissions
            # to avoid stripping default permissions (read, glob, grep, edit, etc.)
            if "permission" not in existing_agent:
                # Deep copy template permissions to avoid reference issues
                template_perms = agent_template.get("permission", {})
                existing_agent["permission"] = json.loads(json.dumps(template_perms))
            else:
                # Agent has permissions, only ensure bash has filter rule
                perms = existing_agent["permission"]
                
                if "bash" not in perms:
                    perms["bash"] = {FILTER_BASH_RULE: "allow"}
                else:
                    bash = perms["bash"]
                    if isinstance(bash, str):
                        perms["bash"] = {
                            FILTER_BASH_RULE: "allow",
                            "*": bash
                        }
                    elif isinstance(bash, dict):
                        if FILTER_BASH_RULE not in bash:
                            bash[FILTER_BASH_RULE] = "allow"
                    elif isinstance(bash, list):
                        # Handle list format (convert to dict)
                        bash_dict = {FILTER_BASH_RULE: "allow"}
                        for item in bash:
                            if isinstance(item, str):
                                bash_dict[item] = "allow"
                        perms["bash"] = bash_dict
    
    if not atomic_write_json(opencode_path, existing):
        print(f"  ERROR: Failed to write opencode.json", file=sys.stderr)
        if backup_path and backup_path.exists():
            try:
                shutil.copy2(backup_path, opencode_path)
                print(f"  Restored opencode.json from backup", file=sys.stderr)
            except Exception as e:
                print(f"  WARNING: Could not restore from backup: {e}", file=sys.stderr)
        return False, backup_path
    
    return True, backup_path


def copy_prompts(target_path, src_dir=None, dst_dir=None, files=None):
    """Copy prompt/rule files to destination, skip if exists."""
    prompts_src = src_dir or (ENGINE_DIR / "templates" / "prompts")
    prompts_dst = dst_dir or (target_path / ".opencode" / "prompts")
    prompts_dst.mkdir(parents=True, exist_ok=True)
    
    agents = files or ["gm.md", "code-reviewer.md", "test-writer.md"]
    copied = []
    skipped = []
    failed = []
    
    for agent_file in agents:
        src = prompts_src / agent_file
        dst = prompts_dst / agent_file
        
        if dst.exists():
            skipped.append(agent_file)
        elif not src.exists():
            print(f"  ERROR: Template prompt missing: {src}", file=sys.stderr)
            failed.append(agent_file)
        else:
            try:
                shutil.copy2(src, dst)
                copied.append(agent_file)
            except PermissionError as e:
                print(f"  ERROR: Permission denied copying {agent_file}: {e}", file=sys.stderr)
                failed.append(agent_file)
            except Exception as e:
                print(f"  ERROR: Could not copy {agent_file}: {e}", file=sys.stderr)
                failed.append(agent_file)
    
    return copied, skipped, failed


def append_or_create_file(target_path, filename, section_content, section_marker=r'^#+\s+Memory\s*$', create_header="# Agent Rules\n\n"):
    """Append section to file, or create file with header + section. Skip if section already present."""
    file_path = target_path / filename
    
    if file_path.exists():
        with open(file_path, encoding='utf-8') as f:
            content = f.read()
        
        if re.search(section_marker, content, re.MULTILINE | re.IGNORECASE):
            return "skipped"
        
        with open(file_path, 'a', encoding='utf-8') as f:
            if content.endswith('\n\n'):
                f.write(section_content)
            elif content.endswith('\n'):
                f.write('\n' + section_content)
            else:
                f.write('\n\n' + section_content)
        return "appended"
    else:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(create_header)
            f.write(section_content)
        return "created"


def read_memory_section():
    """Read the shared memory section template."""
    with open(ENGINE_DIR / "templates" / "agents-memory-section.md", encoding='utf-8') as f:
        return f.read()


def append_agents_md_memory_section(target_path):
    """Append Memory section to AGENTS.md, skip if already present."""
    return append_or_create_file(target_path, "AGENTS.md", read_memory_section())


def wire_opencode(target_path):
    """Orchestrate opencode wiring: copy prompts, append AGENTS.md, merge opencode.json."""
    print("\nWiring opencode...")
    
    # Run non-destructive steps first
    copied, skipped, failed = copy_prompts(target_path)
    print(f"  Copied {len(copied)} prompts to .opencode/prompts/")
    if skipped:
        print(f"  Skipped {len(skipped)} existing prompts: {', '.join(skipped)}")
    if failed:
        print(f"  WARNING: Failed to copy {len(failed)} prompts: {', '.join(failed)}", file=sys.stderr)
        # Continue anyway - opencode.json merge is more critical than prompt files
    
    mem_status = append_agents_md_memory_section(target_path)
    if mem_status == "appended":
        print("  Appended Memory section to AGENTS.md")
    elif mem_status == "created":
        print("  Created AGENTS.md with Memory section")
    else:
        print("  AGENTS.md already has Memory section (skipped)")
    
    # Run destructive merge last
    success, backup_path = merge_opencode_json(target_path)
    if not success:
        return False
    
    if backup_path:
        print(f"  Backed up opencode.json -> {backup_path.name}")
    else:
        print(f"  Created opencode.json from snippet")
    
    return True


def wire_windsurf(target_path):
    """Wire memory into Windsurf: copy workflows, create Plans dir, append AGENTS.md."""
    print("\nWiring windsurf...")
    
    workflows_src = ENGINE_DIR / "templates" / "windsurf" / "workflows"
    workflows_dst = target_path / ".windsurf" / "workflows"
    workflow_files = ["gm.md", "code-reviewer.md", "test-writer.md", "fuzzer.md",
                      "meta-agent.md", "plan-deviation.md", "plan-reviewer.md"]
    copied, skipped, failed = copy_prompts(target_path, src_dir=workflows_src, dst_dir=workflows_dst,
                                           files=workflow_files)
    if copied:
        print(f"  Created .windsurf/workflows/{', '.join(copied)}")
    if skipped:
        print(f"  Skipped existing: {', '.join(skipped)}")
    if failed:
        print(f"  WARNING: Failed to copy {len(failed)} workflows: {', '.join(failed)}", file=sys.stderr)
    
    plans_dir = target_path / ".windsurf" / "Plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    gitkeep = plans_dir / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.touch()
    print(f"  Created .windsurf/Plans/")
    
    mem_status = append_agents_md_memory_section(target_path)
    if mem_status == "appended":
        print("  Appended Memory section to AGENTS.md")
    elif mem_status == "created":
        print("  Created AGENTS.md with Memory section")
    else:
        print("  AGENTS.md already has Memory section (skipped)")
    
    return True


def wire_cursor(target_path):
    """Wire memory into Cursor: copy .mdc rules, append AGENTS.md."""
    print("\nWiring cursor...")
    
    rules_src = ENGINE_DIR / "templates" / "cursor-rules"
    rules_dst = target_path / ".cursor" / "rules"
    rule_files = ["memory-protocol.mdc", "gm.mdc", "code-reviewer.mdc", "test-writer.mdc"]
    copied, skipped, failed = copy_prompts(target_path, src_dir=rules_src, dst_dir=rules_dst,
                                           files=rule_files)
    if copied:
        print(f"  Created .cursor/rules/{', '.join(copied)}")
    if skipped:
        print(f"  Skipped existing: {', '.join(skipped)}")
    if failed:
        print(f"  WARNING: Failed to copy {len(failed)} rules: {', '.join(failed)}", file=sys.stderr)
    
    mem_status = append_agents_md_memory_section(target_path)
    if mem_status == "appended":
        print("  Appended Memory section to AGENTS.md")
    elif mem_status == "created":
        print("  Created AGENTS.md with Memory section")
    else:
        print("  AGENTS.md already has Memory section (skipped)")
    
    return True


def wire_claude_code(target_path):
    """Wire memory into Claude Code: create/append CLAUDE.md."""
    print("\nWiring claude-code...")
    
    status = append_or_create_file(target_path, "CLAUDE.md", read_memory_section(),
                                   create_header="# Project Instructions\n\n")
    if status == "appended":
        print("  Appended Memory section to CLAUDE.md")
    elif status == "created":
        print("  Created CLAUDE.md with Memory section")
    else:
        print("  CLAUDE.md already has Memory section (skipped)")
    
    return True


def wire_copilot(target_path):
    """Wire memory into GitHub Copilot: create/append copilot-instructions.md, append AGENTS.md."""
    print("\nWiring copilot...")
    
    github_dir = target_path / ".github"
    github_dir.mkdir(parents=True, exist_ok=True)
    
    status = append_or_create_file(target_path, ".github/copilot-instructions.md", read_memory_section(),
                                   create_header="# Project Instructions\n\n")
    if status == "appended":
        print("  Appended Memory section to .github/copilot-instructions.md")
    elif status == "created":
        print("  Created .github/copilot-instructions.md with Memory section")
    else:
        print("  .github/copilot-instructions.md already has Memory section (skipped)")
    
    mem_status = append_agents_md_memory_section(target_path)
    if mem_status == "appended":
        print("  Appended Memory section to AGENTS.md")
    elif mem_status == "created":
        print("  Created AGENTS.md with Memory section")
    else:
        print("  AGENTS.md already has Memory section (skipped)")
    
    return True


IDE_WIRERS = {
    "opencode": wire_opencode,
    "windsurf": wire_windsurf,
    "cursor": wire_cursor,
    "claude-code": wire_claude_code,
    "copilot": wire_copilot,
}


def main():
    parser = argparse.ArgumentParser(
        description="Scaffold a working memory/ directory into any project",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scaffold.py /path/to/project
  python scaffold.py . --defaults
  python scaffold.py /path/to/project --force
  python scaffold.py /path/to/project --defaults --ide windsurf
  python scaffold.py /path/to/project --defaults --ide windsurf,cursor,claude-code
  python scaffold.py /path/to/project --defaults --ide all
  python scaffold.py --ide ?
        """
    )
    
    parser.add_argument("target", nargs="?", help="Target project path (default: current directory)")
    parser.add_argument("--defaults", action="store_true", help="Skip prompts, use all defaults")
    parser.add_argument("--force", action="store_true", help="Overwrite engine files only")
    parser.add_argument("--ide", type=str, default=None,
        help="Wire memory into IDE/agent platform(s): opencode, windsurf, cursor, claude-code, copilot, all (comma-separated). Use --ide ? to list platforms.")
    parser.add_argument("--opencode", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    
    args = parser.parse_args()
    
    # --ide ? or --ide '' → list available platforms and exit
    if args.ide is not None and args.ide.strip() in ("", "?"):
        print("Available IDE platforms:")
        for name, wirer in IDE_WIRERS.items():
            print(f"  {name:14s} — {wirer.__doc__}")
        print(f"  {'all':14s} — wire all platforms at once")
        return 0
    
    if args.version:
        print(f"agent-memory-scaffold v{ENGINE_VERSION}", file=sys.stderr)
        return 0
    
    # Check prerequisites
    check_prerequisites()
    
    # Resolve target path
    target_path = resolve_target_path(args.target)
    target_memory = target_path / "memory"
    
    print(f"Target: {target_path}")
    
    # Check for existing memory/
    if target_memory.exists():
        if not args.force:
            sys.exit(f"ERROR: {target_memory} already exists.\n"
                     f"Use --force to overwrite engine files (data files are never touched).")
        else:
            print(f"  --force: overwriting engine files only")
    
    # Load template config
    template_path = ENGINE_DIR / "templates" / "config.json"
    with open(template_path) as f:
        template_config = json.load(f)
    
    # Generate config.json
    if args.defaults or not sys.stdin.isatty():
        config = generate_config_defaults(target_path.name, template_config)
        print(f"  Using defaults (project_name: {config['project_name']})")
    else:
        config = generate_config_interactive(target_path.name, template_config)
    
    # Create memory/ directory
    target_memory.mkdir(parents=True, exist_ok=True)
    
    # Copy engine files
    copy_engine_files(target_memory, args.force)
    print(f"  Wrote shim to filter.py")
    
    # Write config.json (create if absent, never overwrite)
    config_path = target_memory / "config.json"
    if not config_path.exists():
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        print(f"  Created config.json")
    else:
        print(f"  config.json exists (not overwritten)")
    
    # Create starter files (only if absent)
    create_starter_files(target_memory)
    print(f"  Created starter files")
    
    # Register project
    register_project(target_path)
    print(f"  Registered in projects.txt")
    
    # Verify
    verify_scaffold(target_memory)
    
    # Resolve IDE platforms to wire
    platforms = []
    if args.ide:
        for p in args.ide.split(","):
            p = p.strip()
            if p == "all":
                platforms = list(IDE_WIRERS.keys())
                break
            platforms.append(p)
    if args.opencode:
        if "opencode" not in platforms:
            platforms.append("opencode")
    
    # Wire IDE platform(s)
    for platform in platforms:
        wirer = IDE_WIRERS.get(platform)
        if wirer is None:
            valid = ", ".join(list(IDE_WIRERS.keys()) + ["all"])
            sys.exit(f"ERROR: Unknown IDE platform '{platform}'. Valid: {valid}")
        if not wirer(target_path):
            sys.exit(1)
    
    print(f"\n[OK] Scaffold complete: {target_memory}")
    if platforms:
        print(f"  Wired: {', '.join(platforms)}")
    print(f"\nNext steps:")
    print(f"  1. cd {target_path}")
    print(f"  2. python memory/filter.py --step 1 --components YourComponent --domain tooling")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
