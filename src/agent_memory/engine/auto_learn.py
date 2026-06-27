"""Auto-learning detection module for the memory engine.

Pure detection functions receive pre-computed data (no I/O).
auto_learn_core() handles all I/O and orchestration.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone

from agent_memory.engine.git_utils import check_staleness
from agent_memory.engine.handlers import log_core
from agent_memory.engine.io import read_learnings
from agent_memory.engine.metrics import read_metrics
from agent_memory.engine.triggers import _last_consolidation_ts
from agent_memory.engine.validation import actions_oppose


# ---------------------------------------------------------------------------
# Path-to-domain heuristic
# ---------------------------------------------------------------------------

_PATH_DOMAIN_MAP = {
    "db/": "database", "database/": "database", "sql": "database",
    "migration": "database", "schema": "database", "orm/": "database",
    "model/": "database", "repository/": "database", "dao/": "database",
    "entity/": "database", "prisma": "database", "drizzle": "database",
    "sequelize": "database", "knex": "database", "alembic": "database",

    "ui/": "frontend", "frontend/": "frontend", "components/": "frontend",
    "view/": "frontend", "views/": "frontend", "page/": "frontend",
    "pages/": "frontend", "component/": "frontend", "widget/": "frontend",
    "template/": "frontend", "templates/": "frontend", "src/app/": "frontend",
    "public/": "frontend", "assets/": "frontend", "static/": "frontend",
    "css/": "frontend", "scss/": "frontend", "sass/": "frontend",
    "tailwind": "frontend", "jsx": "frontend", "tsx": "frontend",
    "vue": "frontend", "svelte": "frontend",

    "api/": "api", "routes/": "api", "router/": "api",
    "endpoint/": "api", "endpoints/": "api", "controller/": "api",
    "controllers/": "api", "graphql": "api", "rest/": "api",
    "openapi": "api", "swagger": "api",

    "services/": "backend", "service/": "backend", "handler/": "backend",
    "handlers/": "backend", "logic/": "backend", "domain/": "backend",
    "use_case/": "backend", "usecase/": "backend", "interactor/": "backend",
    "command/": "backend", "query/": "backend",

    "auth/": "security", "security/": "security", "crypto/": "security",
    "login/": "security", "oauth/": "security", "jwt/": "security",
    "token/": "security", "password/": "security", "permission/": "security",
    "rbac/": "security", "middleware/": "security",

    "deploy/": "deployment", "deployment/": "deployment", "infra/": "deployment",
    "infrastructure/": "deployment", "scripts/": "deployment",
    "docker": "deployment", "k8s/": "deployment", "kubernetes/": "deployment",
    "terraform/": "deployment", "ansible/": "deployment", "helm/": "deployment",
    "ci/": "deployment", "cd/": "deployment", ".github/": "deployment",
    "gitlab-ci": "deployment", "jenkins": "deployment",

    "test/": "testing", "tests/": "testing", "spec/": "testing",
    "specs/": "testing", "__tests__/": "testing", "fixture/": "testing",
    "fixtures/": "testing", "mock/": "testing", "mocks/": "testing",
    "e2e/": "testing", "integration/": "testing",

    "data/": "data", "etl/": "data", "pipeline/": "data",
    "ingest/": "data", "dataset/": "data", "analytics/": "data",
    "warehouse/": "data", "spark/": "data", "kafka/": "data",
    "airflow/": "data", "dag/": "data", "stream/": "data",

    "ux/": "ui", "design-system/": "ui", "design_system/": "ui",
    "storybook/": "ui", "figma/": "ui", "theme/": "ui",
    "themes/": "ui",

    "perf/": "performance", "performance/": "performance",
    "benchmark/": "performance", "benchmarks/": "performance",
    "cache/": "performance", "redis/": "performance",
    "worker/": "performance", "queue/": "performance",

    "docs/": "documentation", "doc/": "documentation",
    "documentation/": "documentation", "readme": "documentation",
    "changelog": "documentation", "license": "documentation",

    "cli/": "tooling", "bin/": "tooling", "tools/": "tooling",
    "config/": "tooling", "eslint": "tooling", "prettier": "tooling",
    "webpack": "tooling", "vite": "tooling", "rollup": "tooling",
    "babel": "tooling", "tsconfig": "tooling",
}


def _derive_domain(file_path):
    """Derive a domain tag from a file path using keyword matching.

    Falls back to 'tooling' for unmatched paths.
    """
    path_lower = file_path.lower().replace("\\", "/")
    for keyword, domain in _PATH_DOMAIN_MAP.items():
        if keyword in path_lower:
            return domain
    return "tooling"


# ---------------------------------------------------------------------------
# Detection functions (pure — receive pre-computed data, no I/O)
# ---------------------------------------------------------------------------

def detect_under_retrieved(entries, ctx):
    """Detect entries with low access but high reinforcement."""
    access_thresh = ctx.get("auto_learn_under_retrieved_access", 2)
    reinforcement_thresh = ctx.get("auto_learn_under_retrieved_reinforcement", 5)
    results = []
    for entry in entries:
        if entry.get("resolved", False):
            continue
        access_count = entry.get("access_count", 0)
        reinforcement_count = entry.get("reinforcement_count", 0)
        if access_count <= access_thresh and reinforcement_count >= reinforcement_thresh:
            results.append({
                "step": entry.get("step", 1),
                "source_agent": "system",
                "type": "meta_learning",
                "domain": entry.get("domain", "tooling"),
                "components": entry.get("components", []),
                "files_touched": entry.get("files_touched", []),
                "trigger": entry["trigger"],
                "action": "ALWAYS consider this rule: " + entry["action"],
                "reason": f"Under-retrieved learning: access_count={access_count}, "
                          f"reinforcement_count={reinforcement_count}. "
                          f"The rule is frequently reinforced but rarely retrieved.",
                "importance": 5,
                "severity": "minor",
                "resolved": True,
            })
    return results


def detect_conflicts(entries, ctx):
    """Detect entries with opposing actions and overlapping components."""
    results = []
    unresolved = [e for e in entries if not e.get("resolved", False)]
    for i, entry_i in enumerate(unresolved):
        for entry_j in unresolved[i + 1:]:
            comps_i = {c.lower() for c in entry_i.get("components", [])}
            comps_j = {c.lower() for c in entry_j.get("components", [])}
            if not (comps_i & comps_j):
                continue
            if actions_oppose(entry_i.get("action", ""), entry_j.get("action", "")):
                shared_components = list(comps_i & comps_j)
                all_files = list(set(entry_i.get("files_touched", []) + entry_j.get("files_touched", [])))
                if not all_files:
                    all_files = ["unknown"]
                results.append({
                    "step": max(entry_i.get("step", 1), entry_j.get("step", 1)),
                    "source_agent": "system",
                    "type": "meta_learning",
                    "domain": entry_i.get("domain", "tooling"),
                    "components": shared_components,
                    "files_touched": all_files,
                    "trigger": f"When working with {', '.join(shared_components)}, "
                               f"both '{entry_i['trigger']}' and '{entry_j['trigger']}' apply",
                    "action": f"NEVER apply both '{entry_i['action']}' and '{entry_j['action']}' "
                              f"for the same context; resolve the conflict before proceeding",
                    "reason": f"Auto-detected conflict between "
                              f"{entry_i.get('source_agent', 'unknown')} and "
                              f"{entry_j.get('source_agent', 'unknown')}",
                    "importance": 8,
                    "severity": "critical",
                    "resolved": True,
                })
    return results


def detect_over_injected(entries, log_events, staleness_map, ctx):
    """Detect entries with high access, low reinforcement, and no subsequent coverage."""
    access_thresh = ctx.get("auto_learn_over_injected_access", 10)
    reinforcement_thresh = ctx.get("auto_learn_over_injected_reinforcement", 2)
    results = []

    # Build a set of components from log events for overlap checking
    log_components = []
    for evt in log_events:
        comps = evt.get("entry_components", [])
        if comps:
            log_components.append((evt.get("ts", ""), set(c.lower() for c in comps)))

    for entry in entries:
        if entry.get("resolved", False):
            continue
        access_count = entry.get("access_count", 0)
        reinforcement_count = entry.get("reinforcement_count", 0)
        entry_ts = entry.get("ts", "")

        # Apply staleness boost
        effective_access_thresh = access_thresh
        if staleness_map.get(entry_ts, False):
            effective_access_thresh = max(1, access_thresh // 2)

        if access_count < effective_access_thresh:
            continue
        if reinforcement_count > reinforcement_thresh:
            continue

        # Check no subsequent log event has overlapping components
        entry_comps = {c.lower() for c in entry.get("components", [])}
        if not entry_comps:
            continue

        has_subsequent = False
        for log_ts, log_comps in log_components:
            if log_ts > entry_ts and log_comps & entry_comps:
                has_subsequent = True
                break
        if has_subsequent:
            continue

        results.append({
            "step": entry.get("step", 1),
            "source_agent": "system",
            "type": "meta_learning",
            "domain": entry.get("domain", "tooling"),
            "components": entry.get("components", []),
            "files_touched": entry.get("files_touched", []),
            "trigger": entry["trigger"],
            "action": "NEVER rely on this rule as the primary signal unless it has been reinforced: " + entry["action"],
            "reason": "Over-injected: high access but low reinforcement. "
                      "No subsequent learning covers the same components.",
            "importance": 5,
            "severity": "minor",
            "resolved": True,
        })
    return results


def detect_retrieval_failure(entries, retrieval_events, log_events, since_ts, ctx):
    """Detect bug_fix entries whose components had prior zero-result retrievals."""
    cap = ctx.get("auto_learn_retrieval_failure_cap", 100)

    # Filter log events to bug_fix type
    bug_fix_events = [e for e in log_events if e.get("entry_type") == "bug_fix"]

    # Apply since_ts filter
    if since_ts is not None:
        bug_fix_events = [e for e in bug_fix_events if _parse_ts(e.get("ts", "")) is not None
                          and _parse_ts(e.get("ts", "")) >= since_ts]
        retrieval_events = [e for e in retrieval_events if _parse_ts(e.get("ts", "")) is not None
                            and _parse_ts(e.get("ts", "")) >= since_ts]
    else:
        # Cap retrieval events to most recent
        retrieval_events = retrieval_events[-cap:]

    # Build lookup of zero-result retrieval events by component
    zero_retrievals = []
    for evt in retrieval_events:
        warnings_returned = evt.get("warnings_returned", 0)
        patterns_returned = evt.get("patterns_returned", 0)
        if warnings_returned == 0 and patterns_returned == 0:
            query_components = set(c.lower() for c in evt.get("query_components", []))
            if query_components:
                zero_retrievals.append((evt.get("ts", ""), query_components))

    results = []
    for bf_event in bug_fix_events:
        bf_components = set(c.lower() for c in bf_event.get("entry_components", []))
        if not bf_components:
            continue
        bf_ts = bf_event.get("ts", "")

        # Find prior retrieval events with overlapping components and zero results
        for ret_ts, ret_comps in zero_retrievals:
            if ret_ts >= bf_ts:
                continue
            if ret_comps & bf_components:
                components_list = list(bf_components)
                files = bf_event.get("entry_files_touched", [])
                if not files:
                    files = ["unknown"]
                results.append({
                    "step": bf_event.get("entry_step", 1),
                    "source_agent": "system",
                    "type": "bug_fix",
                    "domain": bf_event.get("entry_domain", "tooling"),
                    "components": components_list,
                    "files_touched": files,
                    "trigger": f"When retrieving learnings for {', '.join(components_list)}",
                    "action": f"ALWAYS include the pattern from entry ts:{bf_ts} — "
                              f"prior retrieval queries for these components returned zero results",
                    "reason": "Retrieval-failure correlation: a prior query with matching "
                              "components returned zero results.",
                    "importance": 7,
                    "severity": "major",
                    "resolved": False,
                })
                break  # one match per bug_fix event

    return results


def detect_repeated_fixes(commits, ctx):
    """Detect files fixed repeatedly in recent commits."""
    threshold = ctx.get("auto_learn_fix_commit_threshold", 3)
    max_files = ctx.get("auto_learn_max_files_per_commit", 5)
    fix_keywords = {"fix", "bug", "error", "broken", "crash"}

    # Group fix commits by file
    file_fix_commits = {}
    for commit in commits:
        msg_lower = commit["message"].lower()
        if not any(kw in msg_lower for kw in fix_keywords):
            continue
        files = commit.get("files", [])
        if len(files) > max_files:
            continue
        for f in files:
            file_fix_commits.setdefault(f, []).append(commit)

    results = []
    for file_path, fix_commits in file_fix_commits.items():
        if len(fix_commits) < threshold:
            continue
        # Derive components from file path
        parts = file_path.replace("\\", "/").split("/")
        component = parts[-1].rsplit(".", 1)[0] if parts else file_path
        results.append({
            "step": 1,  # will be set by auto_learn_core
            "source_agent": "system",
            "type": "bug_fix",
            "domain": _derive_domain(file_path),
            "components": [component],
            "files_touched": [file_path],
            "trigger": f"When modifying {file_path}",
            "action": f"ALWAYS verify the area around {file_path} — "
                      f"this file has been fixed repeatedly in recent commits",
            "reason": f"Detected {len(fix_commits)} fix commits touching {file_path} "
                      f"in the last {ctx.get('auto_learn_git_scan_depth', 20)} commits.",
            "importance": 7,
            "severity": "major",
            "resolved": False,
        })
    return results


def detect_reverts(commits, ctx):
    """Detect revert commits and generate learnings from parseable subjects."""
    results = []
    revert_count = 0
    subject_re = re.compile(r'revert\s+"(.+?)"', re.IGNORECASE)

    for commit in commits:
        msg = commit["message"]
        if "revert" not in msg.lower():
            continue
        revert_count += 1

        match = subject_re.search(msg)
        if not match:
            continue

        extracted_subject = match.group(1)
        commit_files = commit.get("files", [])
        domain = _derive_domain(commit_files[0]) if commit_files else "tooling"
        if not commit_files:
            commit_files = ["unknown"]

        # Derive components from subject or files
        subject_words = extracted_subject.split()
        components = [subject_words[0]] if subject_words else ["revert"]
        if not components:
            components = [commit_files[0].rsplit(".", 1)[0]]

        results.append({
            "step": 1,  # will be set by auto_learn_core
            "source_agent": "system",
            "type": "bug_fix",
            "domain": domain,
            "components": components,
            "files_touched": commit_files,
            "trigger": f"When attempting {extracted_subject}",
            "action": f"NEVER attempt {extracted_subject} — this approach was reverted",
            "reason": "Git revert detected: this approach was reverted, "
                      "indicating it caused problems.",
            "importance": 8,
            "severity": "critical",
            "resolved": False,
        })
    return results, revert_count


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_ts(ts_str):
    """Parse ISO timestamp string to UTC datetime. Returns None on failure."""
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _parse_git_log(repo_root, depth):
    """Run git log and parse into list of {hash, message, files} dicts.

    Uses -z (NUL-separated) so the format output and the file list for each
    commit are separate NUL-delimited blocks: block 0 = format, block 1 = files,
    block 2 = next format, block 3 = next files, ...
    """
    result = subprocess.run(
        ["git", "log", "-z", "--format=%H\x1f%s\x1f%b\x1f", "--name-only", f"-n{depth}"],
        capture_output=True, cwd=repo_root
    )
    if result.returncode != 0:
        return []

    output = result.stdout.decode("utf-8", errors="replace")
    blocks = output.split("\x00")
    commits = []

    # With -z: blocks alternate between format output and file list.
    # Even indices (0, 2, 4, ...) = format blocks.
    # Odd indices (1, 3, 5, ...) = file blocks.
    # The last commit may not have a trailing file block if it has no files.
    for i in range(0, len(blocks), 2):
        fmt_block = blocks[i]
        if not fmt_block:
            continue
        parts = fmt_block.split("\x1f")
        if len(parts) < 4:
            continue
        hash_val = parts[0]
        subject = parts[1]
        body = parts[2].strip()

        # File list is the next block (odd index)
        file_block = blocks[i + 1] if i + 1 < len(blocks) else ""
        files = [f.strip() for f in file_block.split("\n") if f.strip()]

        message = f"{subject} {body}" if body else subject

        commits.append({
            "hash": hash_val,
            "message": message,
            "files": files,
        })

    return commits


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def auto_learn_core(paths, ctx):
    """Run auto-learning detection and generate new entries.

    Returns dict with scanned stats, generated entries, deduped/skipped counts.
    """
    if not ctx.get("auto_learn_enabled", True):
        return {
            "exit_code": 0,
            "status": "ok",
            "scanned": {"metrics_events": 0, "learnings": 0, "git_commits": 0, "retrieval_events": 0},
            "generated": [],
            "deduped": 0,
            "skipped": 0,
            "capped": False,
            "git_available": True,
            "disabled": True,
        }

    entries = read_learnings(paths)
    metrics = read_metrics(paths)

    max_step = max((e.get("step", 0) for e in entries), default=1)

    # Git scan
    git_available = True
    commits = []
    try:
        commits = _parse_git_log(paths.repo_root, ctx.get("auto_learn_git_scan_depth", 20))
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        git_available = False

    # Staleness map for high-access entries
    staleness_map = {}
    over_injected_access = ctx.get("auto_learn_over_injected_access", 10)
    for entry in entries:
        if entry.get("access_count", 0) >= over_injected_access:
            try:
                is_stale, _, err = check_staleness(entry, paths.repo_root, ctx)
                staleness_map[entry.get("ts", "")] = is_stale and err is None
            except Exception:
                staleness_map[entry.get("ts", "")] = False

    # since_ts from last consolidation
    since_ts = None
    last_consolidation = _last_consolidation_ts(paths)
    if last_consolidation:
        since_ts = _parse_ts(last_consolidation)

    # Split metrics
    retrieval_events = [e for e in metrics if e.get("event_type") == "retrieval"]
    log_events = [e for e in metrics if e.get("event_type") == "log"]

    # Run detectors in priority order
    max_per_run = ctx.get("auto_learn_max_per_run", 20)
    all_candidates = []

    # 1. retrieval-failure
    all_candidates.extend(detect_retrieval_failure(entries, retrieval_events, log_events, since_ts, ctx))
    # 2. repeated-fixes
    all_candidates.extend(detect_repeated_fixes(commits, ctx))
    # 3. reverts
    revert_results, _revert_count = detect_reverts(commits, ctx)
    all_candidates.extend(revert_results)
    # 4. conflicts
    all_candidates.extend(detect_conflicts(entries, ctx))
    # 5. under-retrieved
    all_candidates.extend(detect_under_retrieved(entries, ctx))
    # 6. over-injected
    all_candidates.extend(detect_over_injected(entries, log_events, staleness_map, ctx))

    # Set step for git-derived entries (they have step=1 placeholder)
    for candidate in all_candidates:
        if candidate.get("step") == 1 and candidate.get("type") == "bug_fix":
            candidate["step"] = max_step

    # Cap candidates
    capped = len(all_candidates) > max_per_run
    all_candidates = all_candidates[:max_per_run]

    # Log each candidate via log_core
    generated = []
    deduped = 0
    skipped = 0

    for candidate in all_candidates:
        json_str = json.dumps(candidate)
        result = log_core(json_str, paths, ctx)
        status = result.get("status", "")
        if status in ("added", "conflict"):
            generated.append({
                "type": candidate["type"],
                "trigger": candidate["trigger"],
                "action": candidate["action"],
                "components": candidate.get("components", []),
                "files_touched": candidate.get("files_touched", []),
                "domain": candidate.get("domain", "tooling"),
            })
        elif status in ("duplicate", "semantic_duplicate"):
            deduped += 1
        elif status == "quarantined":
            skipped += 1

    return {
        "exit_code": 0,
        "status": "ok",
        "scanned": {
            "metrics_events": len(metrics),
            "learnings": len(entries),
            "git_commits": len(commits),
            "retrieval_events": len(retrieval_events),
        },
        "generated": generated,
        "deduped": deduped,
        "skipped": skipped,
        "capped": capped,
        "git_available": git_available,
    }
