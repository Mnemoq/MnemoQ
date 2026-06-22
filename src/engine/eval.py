"""Grading harness for retrieval quality evaluation.

Loads a fixture of (task_context, expected_trigger) pairs from
memory/eval/grading.jsonl, runs retrieval for each, and reports
top-1 / top-3 hit rate.

Fixture format (one JSON object per line):
    {"step": N, "components": "A,B", "files": "", "domain": "D",
     "expected_trigger": "substring expected in output"}
"""

from __future__ import annotations

import json
import os
import subprocess
import sys


def load_fixture(fixture_path):
    """Load grading fixtures from a JSONL file.

    Returns list of dicts with keys: step, components, files, domain, expected_trigger.
    Skips blank lines and comments (#).
    """
    if not os.path.exists(fixture_path):
        return []

    fixtures = []
    with open(fixture_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                fixtures.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"WARNING: Skipping malformed fixture line: {e}", file=sys.stderr)
    return fixtures


def run_eval(paths, ctx):
    """Run the grading harness.

    Reads memory/eval/grading.jsonl, runs retrieval for each fixture,
    and prints a hit-rate report.

    ctx is accepted for API consistency with other engine functions but is
    not used directly — each fixture spawns a subprocess that loads its own
    config from the memory directory.

    Returns 0 on success, 1 if no fixtures found.
    """
    fixture_path = os.path.join(paths.memory_dir, "eval", "grading.jsonl")
    fixtures = load_fixture(fixture_path)

    if not fixtures:
        print("No grading fixtures found.")
        print(f"  Expected: {fixture_path}")
        print("  Create one with lines like:")
        print('    {"step": 5, "components": "Player,Collision", "files": "", "domain": "gameplay", "expected_trigger": "When AABB collision detected"}')
        return 1

    engine_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    filter_py = os.path.join(engine_dir, "filter.py")
    if not os.path.exists(filter_py):
        filter_py = os.path.join(os.path.dirname(sys.argv[0]), "filter.py")
    python_exe = sys.executable

    total = len(fixtures)
    top1_hits = 0
    top3_hits = 0
    results = []

    for i, fx in enumerate(fixtures):
        step = fx.get("step", 1)
        components = fx.get("components", "")
        files = fx.get("files", "")
        domain = fx.get("domain", "")
        expected = fx.get("expected_trigger", "")

        cmd = [python_exe, filter_py, "--step", str(step), "--memory-dir", paths.memory_dir]
        if components:
            cmd += ["--components", components]
        if files:
            cmd += ["--files", files]
        if domain:
            cmd += ["--domain", domain]

        proc = subprocess.run(
            cmd,
            cwd=paths.repo_root,
            capture_output=True,
            text=True,
        )

        output = proc.stdout
        if proc.returncode != 0:
            print(f"  [fixture {i+1}] WARNING: retrieval subprocess failed: {proc.stderr.strip()}", file=sys.stderr)
        # Extract pattern lines from output (lines starting with "- [step-")
        pattern_lines = []
        in_patterns = False
        for line in output.split("\n"):
            if "RELEVANT PATTERNS" in line:
                in_patterns = True
                continue
            if in_patterns:
                if line.startswith("- [step-"):
                    pattern_lines.append(line)
                elif line.startswith("## ") or line.strip() == "(none)":
                    break

        # Also check warnings section
        warning_lines = []
        in_warnings = False
        for line in output.split("\n"):
            if "WARNINGS" in line:
                in_warnings = True
                continue
            if in_warnings:
                if line.startswith("- [step-"):
                    warning_lines.append(line)
                elif line.startswith("## ") or line.strip() == "(none)":
                    break

        # Warnings (critical severity) ranked before patterns (non-critical)
        all_result_lines = warning_lines + pattern_lines
        hit = False
        hit_rank = -1

        for rank, line in enumerate(all_result_lines):
            if expected.lower() in line.lower():
                hit = True
                hit_rank = rank + 1
                break

        if hit:
            if hit_rank == 1:
                top1_hits += 1
            if hit_rank <= 3:
                top3_hits += 1
            status = f"HIT@{hit_rank}"
        else:
            status = "MISS"

        results.append((i + 1, status, expected[:60]))

    # Print report
    print("## Grading Harness Results")
    print()
    print(f"Fixtures: {total}")
    if total == 0:
        print("No valid fixtures to grade.")
        return 1
    print(f"Top-1 hit rate: {top1_hits}/{total} ({top1_hits / total * 100:.1f}%)")
    print(f"Top-3 hit rate: {top3_hits}/{total} ({top3_hits / total * 100:.1f}%)")
    print()
    print("### Per-fixture breakdown")
    print(f"{'#':>3}  {'Result':<10}  Expected")
    print("-" * 80)
    for idx, status, expected in results:
        print(f"{idx:>3}  {status:<10}  {expected}")

    return 0
