# Copyright (C) 2026 Mnemoq
# SPDX-License-Identifier: AGPL-3.0-or-later

"""CI-driven auto-learning: parse structured CI reports into candidates.

Currently supports pytest JUnit XML reports (`pytest --junitxml=...`).
Free-form log "why-it-failed" parsing is deferred to the LLM mode (see Phase 8
in the design notes).

Pure detection (`detect_test_failure`) is split from orchestration
(`evaluate_ci_core`) so it stays unit-testable without filesystem access.
"""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET

from agent_memory.engine.auto_learn import _derive_domain
from agent_memory.engine.handlers import log_core
from agent_memory.engine.io import read_learnings


def _component_from_classname(classname: str, name: str) -> str:
    """Derive a short component name from a JUnit testcase."""
    if classname:
        # e.g. "tests.api.test_auth.TestLogin" -> "test_auth" (most specific module)
        parts = [p for p in classname.replace("/", ".").split(".") if p]
        for part in reversed(parts):
            if part.startswith("test_") or part.startswith("Test"):
                return part
        if parts:
            return parts[-1]
    return name or "unknown"


def _file_from_classname(classname: str, file_attr: str | None) -> str:
    """Best-effort: derive a file path from JUnit metadata.

    Prefers the `file` attribute when present; otherwise reconstructs from
    a dotted classname (`tests.api.test_auth` -> `tests/api/test_auth.py`).
    """
    if file_attr:
        return file_attr
    if not classname:
        return "unknown"
    parts = [p for p in classname.split(".") if p]
    # Strip trailing class name (CamelCase) if present
    if parts and parts[-1][:1].isupper():
        parts = parts[:-1]
    if not parts:
        return classname
    return "/".join(parts) + ".py"


def detect_test_failure(report, ctx):
    """Parse a pytest JUnit XML report into candidate entries.

    Args:
        report: Either an XML string, a path-like to an XML file, or a parsed
            ElementTree root element. Pure helper accepts any of the three so
            it stays unit-testable.
        ctx: Engine ctx dict.

    Returns:
        List of candidate dicts (each ready for log_core).
    """
    # Normalize to ElementTree root
    if isinstance(report, ET.Element):
        root = report
    elif isinstance(report, str) and report.lstrip().startswith("<"):
        try:
            root = ET.fromstring(report)
        except ET.ParseError:
            return []
    else:
        # treat as path
        try:
            root = ET.parse(report).getroot()
        except (ET.ParseError, OSError, FileNotFoundError):
            return []

    max_failures = ctx.get("auto_learn_max_per_run", 20)
    results = []
    seen = set()

    # JUnit XML may be <testsuites><testsuite>... or a single <testsuite>.
    suites = root.findall(".//testsuite") or ([root] if root.tag == "testsuite" else [])
    for suite in suites:
        for case in suite.findall("testcase"):
            # ElementTree elements without children are falsy, so test `is None` explicitly.
            failure_el = case.find("failure")
            if failure_el is None:
                failure_el = case.find("error")
            if failure_el is None:
                continue
            classname = case.attrib.get("classname", "")
            name = case.attrib.get("name", "")
            file_attr = case.attrib.get("file") or None

            component = _component_from_classname(classname, name)
            file_path = _file_from_classname(classname, file_attr)
            full_name = f"{classname}::{name}" if classname else name
            key = (file_path, full_name)
            if key in seen:
                continue
            seen.add(key)

            msg = (failure_el.attrib.get("message") or "").strip()
            if not msg and failure_el.text:
                msg = failure_el.text.strip().splitlines()[0] if failure_el.text.strip() else ""
            msg_snippet = (msg[:180] + "…") if len(msg) > 180 else msg
            failure_kind = failure_el.tag  # "failure" or "error"

            results.append({
                "step": 1,  # caller stamps with max_step
                "source_agent": "system",
                "type": "bug_fix",
                "domain": _derive_domain(file_path),
                "components": [component] if component else [name or "unknown"],
                "files_touched": [file_path] if file_path else ["unknown"],
                "trigger": f"When changing code covered by {full_name}",
                "action": (
                    f"ALWAYS re-run {full_name} after edits — this test "
                    f"{'errored' if failure_kind == 'error' else 'failed'} in CI"
                    + (f" with: {msg_snippet}" if msg_snippet else "")
                ),
                "reason": f"CI test {failure_kind} detected via JUnit report.",
                "importance": 8 if failure_kind == "error" else 7,
                "severity": "critical" if failure_kind == "error" else "major",
                "resolved": False,
            })
            if len(results) >= max_failures:
                return results
    return results


def evaluate_ci_core(report_path, paths, ctx):
    """Parse a CI report and write candidates via log_core.

    Returns a dict with {exit_code, generated, deduped, skipped, failures, ...}.
    """
    if not os.path.exists(report_path):
        return {"exit_code": 1, "error": f"report not found: {report_path}",
                "generated": [], "deduped": 0, "skipped": 0, "failures": 0}

    candidates = detect_test_failure(report_path, ctx)

    entries = read_learnings(paths)
    max_step = max((e.get("step", 0) for e in entries), default=1)
    for c in candidates:
        c["step"] = max_step

    generated = []
    deduped = 0
    skipped = 0
    for candidate in candidates:
        try:
            result = log_core(json.dumps(candidate), paths, ctx)
        except Exception:
            skipped += 1
            continue
        status = result.get("status", "")
        if status in ("added", "conflict"):
            generated.append({
                "type": candidate["type"],
                "trigger": candidate["trigger"],
                "action": candidate["action"],
                "files_touched": candidate.get("files_touched", []),
            })
        elif status in ("duplicate", "semantic_duplicate"):
            deduped += 1
        else:
            skipped += 1

    return {
        "exit_code": 0,
        "generated": generated,
        "deduped": deduped,
        "skipped": skipped,
        "failures": len(candidates),
    }
