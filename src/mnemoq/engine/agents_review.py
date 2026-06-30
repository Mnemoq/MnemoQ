# Copyright (C) 2026 Mnemoq
# SPDX-License-Identifier: AGPL-3.0-or-later

"""AGENTS.md review and conflict detection utilities.

Extracted from filter.py (Phase 5).
"""

from __future__ import annotations

import os
import re
import sys
import time

from mnemoq.engine.io import read_learnings
from mnemoq.engine.metrics import log_event

# Stop-words for keyword extraction
STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with",
    "by", "from", "up", "about", "into", "over", "after", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "should", "can", "could", "this", "that", "these", "those", "it", "its", "they",
    "their", "them", "we", "our", "us", "you", "your", "i", "my", "me", "he", "his",
    "him", "she", "her", "not", "no", "if", "then", "else", "when", "where", "why",
    "how", "what", "which", "who", "whom", "use", "using", "used", "make", "making",
    "made", "get", "getting", "got", "set", "setting", "add", "adding", "added",
    "remove", "removing", "removed", "update", "updating", "updated", "create",
    "creating", "created", "delete", "deleting", "deleted", "must", "always",
    "never", "only", "all", "any", "some", "every", "none"
}


def tokenize_keywords(text):
    """Shared tokenizer: lowercase, split, remove stop-words, keep alphanumeric tokens.

    Used by both extract_section_keywords() and check_agents_conflict() to ensure
    consistent keyword extraction across section and learning text.
    """
    words = text.lower().split()

    # Remove stop-words and non-alphanumeric tokens (allow digits, hyphens, underscores)
    keywords = set()
    for word in words:
        word = word.strip(".,;:!?()[]{}\"'")
        if word and word not in STOP_WORDS and re.match(r'^[a-zA-Z0-9._-]+$', word):
            keywords.add(word)

    return keywords


def parse_agents_sections(agents_md_path):
    """Parse AGENTS.md into a list of (heading, content) tuples.

    Extracts ##, ###, and #### headings. Content is everything from after
    the heading until the next heading of equal or higher level.
    Returns empty list if file doesn't exist or has no headings.
    """
    if not os.path.exists(agents_md_path):
        return []

    with open(agents_md_path, encoding="utf-8") as f:
        lines = f.readlines()

    heading_re = re.compile(r"^(#{2,4})\s+(.+)$")
    sections = []
    current_heading = None
    current_lines = []

    for line in lines:
        m = heading_re.match(line)
        if m:
            if current_heading is not None:
                sections.append((current_heading, "\n".join(current_lines)))
            current_heading = m.group(2).strip()
            current_lines = []
        else:
            if current_heading is not None:
                current_lines.append(line)

    if current_heading is not None:
        sections.append((current_heading, "\n".join(current_lines)))

    return sections


def extract_section_keywords(heading, content):
    """Extract a set of lowercase keywords from a section heading and content.

    Strips code blocks (triple-backtick), extracts table cell keywords
    (split on |), strips inline backticks (preserving content), lowercases,
    splits on whitespace, and removes stop-words.
    """
    # Strip triple-backtick code blocks
    content = re.sub(r"```.*?```", "", content, flags=re.DOTALL)

    # Fallback: if unbalanced backticks remain, strip everything after last fence
    if content.count("```") % 2 != 0:
        last_fence = content.rfind("```")
        if last_fence != -1:
            content = content[:last_fence]

    # Extract table cell keywords: split lines on |, take each cell
    table_keywords = []
    for line in content.split("\n"):
        if "|" in line:
            cells = line.split("|")
            for cell in cells:
                cell = cell.strip()
                if cell and cell.replace("-", "") != "":
                    table_keywords.append(cell)

    # Combine heading + content + table keywords
    text = heading + " " + content + " " + " ".join(table_keywords)

    # Strip inline backticks but preserve content: `PooledEntity` -> PooledEntity
    text = text.replace("`", "")

    # Use shared tokenizer
    return tokenize_keywords(text)


def check_agents_conflict(entry, paths, ctx=None):
    """Check if a learning overlaps with AGENTS.md sections.

    Returns (overlap_detected, best_section, jaccard_score, containment_hits) or
    (False, None, 0.0, 0) if no AGENTS.md reference found.
    """
    files_touched = entry.get("files_touched", [])
    components = entry.get("components", [])

    # Check for AGENTS.md reference
    has_agents_ref = (
        any("AGENTS.md" in f for f in files_touched) or
        any("agents" in c.lower() for c in components)
    )

    if not has_agents_ref:
        return False, None, 0.0, 0

    # Parse AGENTS.md sections
    sections = parse_agents_sections(paths.agents_md_path)
    if not sections:
        return False, None, 0.0, 0

    # Get learning keywords (use shared tokenizer for consistency)
    trigger_action = entry.get("trigger", "") + " " + entry.get("action", "")
    learning_keywords = tokenize_keywords(trigger_action)

    best_section = None
    best_jaccard = 0.0
    best_containment = 0

    for heading, content in sections:
        section_keywords = extract_section_keywords(heading, content)
        if not section_keywords:
            continue

        # Jaccard similarity
        union = learning_keywords | section_keywords
        intersection = learning_keywords & section_keywords
        jaccard = len(intersection) / len(union) if union else 0.0

        # Containment hits (how many learning keywords appear in section)
        containment = len(intersection)

        if jaccard > best_jaccard or (jaccard == best_jaccard and containment > best_containment):
            best_jaccard = jaccard
            best_containment = containment
            best_section = heading

    # Threshold: configurable via ctx (defaults: 0.1 Jaccard OR >=3 containment hits)
    # (Lenient for informational warning; large sections dilute Jaccard)
    _ctx = ctx or {}
    jaccard_threshold = _ctx.get("agents_conflict_jaccard_threshold", 0.1)
    containment_threshold = _ctx.get("agents_conflict_containment_threshold", 3)
    if best_jaccard >= jaccard_threshold or best_containment >= containment_threshold:
        return True, best_section, best_jaccard, best_containment

    return False, None, best_jaccard, best_containment


def review_agents_core(current_step, threshold, paths):
    """Core logic for --review-agents: returns dict with section health data.

    Used by MCP server, HTTP API, and handle_review_agents wrapper.
    """
    _start = time.perf_counter()
    sections = parse_agents_sections(paths.agents_md_path)

    if not sections:
        latency = round((time.perf_counter() - _start) * 1000, 2)
        log_event(paths, "review_agents", current_step=current_step, threshold=threshold,
                  section_count=0, recent_learnings=0, active_sections=0,
                  cold_sections=0, unmatched_learnings=0,
                  latency_ms=latency)
        warning = (f"AGENTS.md not found at: {paths.agents_md_path}"
                   if not os.path.exists(paths.agents_md_path)
                   else "AGENTS.md has no ## headings — nothing to report.")
        return {"exit_code": 0, "section_count": 0, "recent_learnings": 0,
                "active_sections": [], "cold_sections": [], "unmatched_learnings": [],
                "warning": warning, "latency_ms": latency}

    section_keywords = []
    for heading, content in sections:
        keywords = extract_section_keywords(heading, content)
        section_keywords.append((heading, keywords))

    entries = read_learnings(paths)
    recent_entries = [
        e for e in entries
        if not e.get("resolved", False)
        and (current_step - e.get("step", 0)) <= threshold
    ]

    section_ref_counts = {heading: 0 for heading, _ in sections}
    unmatched_learnings = []
    multi_match_warnings = []

    for entry in recent_entries:
        trigger_action = entry.get("trigger", "") + " " + entry.get("action", "")
        trigger_action_words = tokenize_keywords(trigger_action)

        matched_sections = []
        for heading, keywords in section_keywords:
            if keywords and (keywords & trigger_action_words):
                section_ref_counts[heading] += 1
                matched_sections.append(heading)

        if not matched_sections:
            unmatched_learnings.append({
                "step": entry.get("step", "?"),
                "domain": entry.get("domain", "?"),
                "trigger": entry.get("trigger", ""),
            })
        elif len(matched_sections) > 1:
            multi_match_warnings.append({
                "sections": matched_sections,
                "trigger": entry.get("trigger", ""),
            })

    active = [{"heading": h, "refs": c} for h, c in section_ref_counts.items() if c > 0]
    cold = [{"heading": h, "refs": 0} for h, c in section_ref_counts.items() if c == 0]

    active_count = len(active)
    cold_count = len(cold)
    latency = round((time.perf_counter() - _start) * 1000, 2)

    log_event(paths, "review_agents",
        current_step=current_step,
        threshold=threshold,
        section_count=len(sections),
        recent_learnings=len(recent_entries),
        active_sections=active_count,
        cold_sections=cold_count,
        unmatched_learnings=len(unmatched_learnings),
        latency_ms=latency,
    )

    return {
        "exit_code": 0,
        "current_step": current_step,
        "threshold": threshold,
        "section_count": len(sections),
        "recent_learnings": len(recent_entries),
        "active_sections": active,
        "cold_sections": cold,
        "unmatched_learnings": unmatched_learnings,
        "multi_match_warnings": multi_match_warnings,
        "latency_ms": latency,
    }


def handle_review_agents(current_step, threshold, paths):
    """Handle --review-agents mode: diagnostic report on AGENTS.md section health."""
    result = review_agents_core(current_step, threshold, paths)

    print("## AGENTS.md Section Health Report")
    print(f"(step {current_step}, threshold {threshold})")
    print()

    if result.get("warning"):
        print(f"WARNING: {result['warning']}")
        return 0

    if result["active_sections"]:
        print("### ACTIVE — Referenced by learnings")
        for s in result["active_sections"]:
            print(f"- {s['heading'].lower().replace(' ', '-')} ({s['refs']} refs)")
        print()

    if result["cold_sections"]:
        print(f"### COLD — No references in last {threshold} steps")
        for s in result["cold_sections"]:
            print(f"- {s['heading'].lower().replace(' ', '-')} (0 refs) — may be foundational or stale")
        print()

    if result["unmatched_learnings"]:
        print("### UNMATCHED — Learnings with no section match")
        for u in result["unmatched_learnings"]:
            print(f"- [step-{u['step']}, {u['domain']}] {u['trigger']}")
        print()

    for w in result.get("multi_match_warnings", []):
        print(f"WARNING: Learning matches multiple sections: {w['sections']}", file=sys.stderr)
        print(f"  Learning: {w['trigger']}", file=sys.stderr)

    return 0
