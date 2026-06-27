#!/usr/bin/env python3
"""
Developer Profile Loader

Loads the developer's global preference profile from ~/.agent-memory/developer-profile.json
and extracts relevant preferences for the current task context.

Profile preferences are advisory guidelines (lower priority than warnings).
See AGENT_MEMORY_GUIDE.md § Priority Hierarchy for the full priority order.
"""

import json
import sys
from pathlib import Path

PROFILE_DIR = Path.home() / ".agent-memory"
PROFILE_PATH = PROFILE_DIR / "developer-profile.json"

DEFAULT_DOMAIN_MAPPINGS = {
    "frontend": ["javascript", "typescript", "react", "vue"],
    "backend": ["python", "node", "java", "go"],
    "database": ["sql", "nosql", "orm"],
    "api": ["rest", "graphql", "http"],
    "testing": ["unit", "integration", "e2e"],
    "deployment": ["docker", "kubernetes", "ci-cd"],
    "performance": ["profiling", "optimization", "monitoring"],
    "security": ["auth", "encryption", "validation"],
    "tooling": ["build", "lint", "format"],
    "documentation": ["api-docs", "readme", "comments"]
}

MAX_GENERAL_PATTERNS = 5
MAX_STACK_PATTERNS = 5


def load_profile():
    """Load developer profile if it exists. Returns None if missing or malformed."""
    if not PROFILE_PATH.exists():
        return None

    try:
        with open(PROFILE_PATH, encoding="utf-8") as f:
            profile = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"WARNING: Could not load developer profile: {e}", file=sys.stderr)
        return None

    if not isinstance(profile, dict):
        print("WARNING: Developer profile is not a JSON object", file=sys.stderr)
        return None

    return profile


def get_profile_context(profile, task_domain=None, domain_mappings=None):
    """Extract relevant preferences from profile for current task.

    Returns a list of preference dicts with keys: source, trigger, action, reason.
    General patterns are always included. Stack-specific preferences are filtered
    by task_domain using domain_mappings with precedence:
    1. domain_mappings parameter (from config.json)
    2. profile["domain_mappings"] (from developer-profile.json)
    3. DEFAULT_DOMAIN_MAPPINGS (hardcoded fallback)
    """
    if not profile:
        return []

    context = []

    general_patterns = profile.get("general_patterns", [])
    if len(general_patterns) > MAX_GENERAL_PATTERNS:
        print(f"WARNING: Profile has {len(general_patterns)} general_patterns, "
              f"only first {MAX_GENERAL_PATTERNS} shown", file=sys.stderr)

    for pattern in general_patterns[:MAX_GENERAL_PATTERNS]:
        if _is_valid_preference(pattern):
            context.append({
                "source": "developer-profile",
                "trigger": pattern["trigger"],
                "action": pattern["action"],
                "reason": pattern["reason"]
            })

    anti_patterns = profile.get("anti_patterns", [])
    if len(anti_patterns) > MAX_GENERAL_PATTERNS:
        print(f"WARNING: Profile has {len(anti_patterns)} anti_patterns, "
              f"only first {MAX_GENERAL_PATTERNS} shown", file=sys.stderr)

    for pattern in anti_patterns[:MAX_GENERAL_PATTERNS]:
        if _is_valid_preference(pattern):
            context.append({
                "source": "developer-profile:anti-pattern",
                "trigger": pattern["trigger"],
                "action": pattern["action"],
                "reason": pattern["reason"]
            })

    if task_domain:
        # Precedence: config > profile JSON > default
        if domain_mappings is not None:
            # Use config-provided mappings (from filter.py)
            active_mappings = domain_mappings
        elif "domain_mappings" in profile:
            # Use profile-provided mappings
            active_mappings = profile["domain_mappings"]
        else:
            # Fall back to hardcoded defaults
            active_mappings = DEFAULT_DOMAIN_MAPPINGS
        
        if not isinstance(active_mappings, dict):
            active_mappings = DEFAULT_DOMAIN_MAPPINGS
        
        relevant_stacks = active_mappings.get(task_domain, [])
        if not isinstance(relevant_stacks, list):
            relevant_stacks = []

        stack_prefs = profile.get("stack_preferences", {})
        if not isinstance(stack_prefs, dict):
            stack_prefs = {}

        added = 0
        for stack in relevant_stacks:
            if added >= MAX_STACK_PATTERNS:
                break
            if stack in stack_prefs and isinstance(stack_prefs[stack], list):
                for pref in stack_prefs[stack]:
                    if added >= MAX_STACK_PATTERNS:
                        break
                    if _is_valid_preference(pref):
                        context.append({
                            "source": f"developer-profile:{stack}",
                            "trigger": pref["trigger"],
                            "action": pref["action"],
                            "reason": pref["reason"]
                        })
                        added += 1

    return context


def _is_valid_preference(pref):
    """Check if a preference dict has the required fields."""
    if not isinstance(pref, dict):
        return False
    return all(k in pref and isinstance(pref[k], str) and pref[k].strip()
               for k in ("trigger", "action", "reason"))
