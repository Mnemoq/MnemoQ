"""Idempotent sync of an engine-owned instruction section into shared agent
files (AGENTS.md, CLAUDE.md, copilot-instructions.md, cursor's memory-protocol.mdc).

The engine owns only the marker-delimited block; everything outside the markers
is the user's and is never read or modified. If a user edits *inside* the block,
a sha mismatch flags drift and the file is left untouched (a .bak is written),
mirroring the "refuse to overwrite foreign content" behaviour used elsewhere.
"""

import hashlib
import re
import shutil
from pathlib import Path

_BEGIN = "<!-- BEGIN agent-memory:managed"
_END = "<!-- END agent-memory:managed -->"

# A stamped managed block: captures the version, the content sha, and the body.
_MANAGED_RE = re.compile(
    r"<!-- BEGIN agent-memory:managed v=(?P<ver>\S+) sha=(?P<sha>[0-9a-f]+) -->\n"
    r"(?P<body>.*?)"
    r"\n?<!-- END agent-memory:managed -->",
    re.DOTALL,
)

# Legacy unmarked section written by older engines: a "## Memory" heading up to
# the next top-level "## " heading or end of file.
_LEGACY_RE = re.compile(r"^##\s+Memory\s*$.*?(?=^##\s|\Z)", re.DOTALL | re.MULTILINE)


def _sha(text):
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:12]


def _wrap(section, version):
    body = section.strip()
    return f"{_BEGIN} v={version} sha={_sha(body)} -->\n{body}\n{_END}\n"


def _backup(path):
    bak = path.with_suffix(path.suffix + ".bak")
    try:
        shutil.copy2(path, bak)
    except Exception:
        pass
    return bak


def sync_managed_section(path, section, version,
                         create_header="# Agent Rules\n\n",
                         create_if_absent=True, force=False):
    """Sync ``section`` into ``path`` as a versioned managed block.

    Returns one of:
      created   — file (or its first managed block) was written fresh
      appended  — file existed with no managed/legacy section; block appended
      migrated  — a legacy unmarked "## Memory" section was replaced in place
      updated   — managed block was stale (older version) and was refreshed
      current   — managed block already matches the current version; no change
      drift     — user edited inside the block; left as-is, .bak written
      absent    — file missing and create_if_absent=False; nothing written

    Content outside the markers is never altered.
    """
    path = Path(path)
    new_block = _wrap(section, version)

    if not path.exists():
        if not create_if_absent:
            return "absent"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(create_header + new_block, encoding="utf-8")
        return "created"

    content = path.read_text(encoding="utf-8")

    m = _MANAGED_RE.search(content)
    if m:
        body, stamped = m.group("body").strip(), m.group("sha")
        edited_in_block = _sha(body) != stamped
        if edited_in_block and not force:
            _backup(path)
            return "drift"
        if not edited_in_block and m.group("ver") == version and body == section.strip():
            return "current"
        new_content = content[:m.start()] + new_block.rstrip("\n") + content[m.end():]
        path.write_text(new_content, encoding="utf-8")
        return "updated"

    legacy = _LEGACY_RE.search(content)
    if legacy:
        # Old section was engine-written; converge to the template but keep a .bak
        # in case it was customised.
        _backup(path)
        new_content = content[:legacy.start()] + new_block + content[legacy.end():]
        path.write_text(new_content, encoding="utf-8")
        return "migrated"

    sep = "" if content.endswith("\n\n") else ("\n" if content.endswith("\n") else "\n\n")
    path.write_text(content + sep + new_block, encoding="utf-8")
    return "appended"


def status_message(label, status):
    """Human-readable one-liner for a sync status (used by scaffold/update output)."""
    return {
        "created": f"Created {label} with Memory section",
        "appended": f"Appended Memory section to {label}",
        "migrated": f"Migrated {label} Memory section to a managed block",
        "updated": f"Refreshed {label} Memory section",
        "current": f"{label} Memory section already current (skipped)",
        "drift": f"{label} Memory section was hand-edited — left as-is, wrote .bak",
        "absent": f"{label} not present (skipped)",
    }.get(status, f"{label}: {status}")
