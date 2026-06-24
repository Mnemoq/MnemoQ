"""Schema validation and dedup matching for the memory engine.

Extracted from filter.py (Phase 2). validate_entry takes a ctx dict
for configurable constraints instead of reading module globals.
"""

from __future__ import annotations


def validate_entry(entry, ctx):
    """Validate an entry against the schema. Returns list of error strings.

    ctx keys used: max_step, valid_source_agents, valid_types, valid_domains,
                   valid_severities, valid_scopes, valid_debt_levels
    """
    errors = []

    required_fields = [
        "step", "source_agent", "type", "domain", "components",
        "files_touched", "trigger", "action", "reason",
        "importance", "severity"
    ]

    for field in required_fields:
        if field not in entry:
            errors.append(f"Missing required field: {field}")

    if errors:
        return errors

    if not isinstance(entry["step"], int) or entry["step"] < 1:
        errors.append("step must be a positive integer")
    elif ctx.get("max_step") is not None and entry["step"] > ctx["max_step"]:
        errors.append(f"step must be <= {ctx['max_step']}")

    valid_source_agents = ctx.get("valid_source_agents")
    if valid_source_agents is not None and entry["source_agent"] not in valid_source_agents:
        errors.append(f"source_agent must be one of: {', '.join(sorted(valid_source_agents))}")

    if entry["type"] not in ctx["valid_types"]:
        errors.append(f"type must be one of: {', '.join(sorted(ctx['valid_types']))}")

    valid_domains = ctx.get("valid_domains")
    if valid_domains is not None and entry["domain"] not in valid_domains:
        errors.append(f"domain must be one of: {', '.join(sorted(valid_domains))}")

    if entry["severity"] not in ctx["valid_severities"]:
        errors.append(f"severity must be one of: {', '.join(sorted(ctx['valid_severities']))}")

    if not isinstance(entry["importance"], int) or not (1 <= entry["importance"] <= 10):
        errors.append("importance must be integer 1-10")

    if not isinstance(entry["components"], list) or len(entry["components"]) == 0:
        errors.append("components must be non-empty list of strings")
    elif not all(isinstance(c, str) for c in entry["components"]):
        errors.append("components must be list of strings")

    if not isinstance(entry["files_touched"], list) or len(entry["files_touched"]) == 0:
        errors.append("files_touched must be non-empty list of strings")
    elif not all(isinstance(f, str) for f in entry["files_touched"]):
        errors.append("files_touched must be list of strings")

    if not isinstance(entry["trigger"], str) or not entry["trigger"].strip():
        errors.append("trigger must be non-empty string")
    elif not entry["trigger"].lower().startswith("when"):
        errors.append("trigger must start with 'When' (case-insensitive)")

    if not isinstance(entry["action"], str) or not entry["action"].strip():
        errors.append("action must be non-empty string")
    elif "ALWAYS" not in entry["action"].upper() and "NEVER" not in entry["action"].upper():
        errors.append("action must contain 'ALWAYS' or 'NEVER' (case-insensitive)")

    if not isinstance(entry["reason"], str) or not entry["reason"].strip():
        errors.append("reason must be non-empty string")

    # reinforcement_count is optional (defaults to 0)
    if "reinforcement_count" in entry:
        if not isinstance(entry["reinforcement_count"], int) or entry["reinforcement_count"] < 0:
            errors.append("reinforcement_count must be non-negative integer")

    if "verified" in entry:
        if not isinstance(entry["verified"], bool):
            errors.append("verified must be boolean")

    if "scope" in entry:
        if entry["scope"] not in ctx["valid_scopes"]:
            errors.append(f"scope must be one of: {', '.join(sorted(ctx['valid_scopes']))}")

    if "symptoms" in entry:
        if not isinstance(entry["symptoms"], str):
            errors.append("symptoms must be string")

    if "debt_level" in entry:
        if entry["debt_level"] not in ctx["valid_debt_levels"]:
            errors.append(f"debt_level must be one of: {', '.join(sorted(ctx['valid_debt_levels']))}")

    if "schema_version" in entry:
        if not isinstance(entry["schema_version"], int) or isinstance(entry["schema_version"], bool):
            errors.append("schema_version must be an integer")

    return errors


def jaccard_similarity(text1, text2):
    """Compute Jaccard similarity between two texts."""
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    if not words1 and not words2:
        return 0.0
    union = words1 | words2
    if len(union) == 0:
        return 0.0
    intersection = words1 & words2
    return len(intersection) / len(union)


def actions_oppose(action1, action2):
    """Check if two actions oppose each other (ALWAYS vs NEVER)."""
    a1_upper = action1.upper()
    a2_upper = action2.upper()
    a1_has_always = "ALWAYS" in a1_upper
    a1_has_never = "NEVER" in a1_upper
    a2_has_always = "ALWAYS" in a2_upper
    a2_has_never = "NEVER" in a2_upper
    return (a1_has_always and a2_has_never) or (a1_has_never and a2_has_always)


def find_best_match(entry, entries):
    """Find the highest similarity match among entries sharing components."""
    entry_components_lower = {c.lower() for c in entry["components"]}
    best_similarity = 0.0
    best_match = None

    for existing in entries:
        if existing.get("resolved", False):
            continue
        existing_components_lower = {c.lower() for c in existing.get("components", [])}
        if not (entry_components_lower & existing_components_lower):
            continue

        trigger_action_new = entry["trigger"] + " " + entry["action"]
        trigger_action_existing = existing["trigger"] + " " + existing["action"]
        similarity = jaccard_similarity(trigger_action_new, trigger_action_existing)

        if similarity > best_similarity:
            best_similarity = similarity
            best_match = existing

    return best_similarity, best_match
