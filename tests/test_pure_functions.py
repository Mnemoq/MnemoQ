"""Direct-import unit tests for pure functions in validation, retrieval, and consolidation.

Follows the established pattern in test_memory.py where pure functions are
imported directly from engine modules (bm25_score, cosine_similarity,
migrate_entry, reranker internals, etc.).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_memory.engine.auto_learn import (
    _derive_domain,
    detect_conflicts,
    detect_over_injected,
    detect_repeated_fixes,
    detect_retrieval_failure,
    detect_reverts,
    detect_under_retrieved,
)
from agent_memory.engine.consolidation import (
    detect_contradictions,
    get_agents_md_suggestions,
    infer_sprint_number,
    is_promotion_candidate,
    score_for_promotion,
)
from agent_memory.engine.constants import (
    VALID_DEBT_LEVELS,
    VALID_DOMAINS,
    VALID_SCOPES,
    VALID_SEVERITIES,
    VALID_SOURCE_AGENTS,
    VALID_TYPES,
)
from agent_memory.engine.evaluate import (
    _build_candidate,
    detect_bug_fixed,
    detect_decision,
    detect_explicit_remember,
    detect_human_correction,
    detect_workaround,
)
from agent_memory.engine.retrieval import is_in_retention, score_entry
from agent_memory.engine.validation import (
    actions_oppose,
    find_best_match,
    jaccard_similarity,
    validate_entry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(**overrides):
    """Build a minimal valid ctx dict for validate_entry."""
    base = {
        "max_step": None,
        "valid_source_agents": VALID_SOURCE_AGENTS,
        "valid_types": VALID_TYPES,
        "valid_domains": VALID_DOMAINS,
        "valid_severities": VALID_SEVERITIES,
        "valid_scopes": VALID_SCOPES,
        "valid_debt_levels": VALID_DEBT_LEVELS,
    }
    base.update(overrides)
    return base


def _valid_entry(**overrides):
    """Build a minimal valid entry that passes all validate_entry checks."""
    base = {
        "step": 1,
        "source_agent": "gm",
        "type": "bug_fix",
        "domain": "tooling",
        "components": ["CollisionSystem"],
        "files_touched": ["collision.py"],
        "trigger": "When AABB collision detected",
        "action": "ALWAYS use broadphase",
        "reason": "Broadphase is efficient",
        "importance": 7,
        "severity": "major",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# validate_entry — positive cases
# ---------------------------------------------------------------------------

class TestValidateEntryPositive:
    def test_valid_entry_no_errors(self):
        errors = validate_entry(_valid_entry(), _ctx())
        assert errors == []

    def test_valid_entry_with_all_optional_fields(self):
        entry = _valid_entry(
            reinforcement_count=3,
            verified=True,
            scope="module",
            symptoms="High latency",
            debt_level="workaround",
            schema_version=2,
        )
        errors = validate_entry(entry, _ctx())
        assert errors == []

# ---------------------------------------------------------------------------
# validate_entry — required field checks
# ---------------------------------------------------------------------------

class TestValidateEntryRequiredFields:
    def test_missing_each_required_field(self):
        required = [
            "step", "source_agent", "type", "domain", "components",
            "files_touched", "trigger", "action", "reason",
            "importance", "severity",
        ]
        for field in required:
            entry = _valid_entry()
            del entry[field]
            errors = validate_entry(entry, _ctx())
            assert any(f"Missing required field: {field}" in e for e in errors), \
                f"Expected missing field error for {field}"

    def test_missing_fields_short_circuits(self):
        """When required fields are missing, only missing-field errors are returned."""
        entry = {"step": 1}
        errors = validate_entry(entry, _ctx())
        # Should only report missing fields, not type/value errors
        assert all("Missing required field" in e for e in errors)
        assert len(errors) == 10  # 11 required - 1 present (step)


# ---------------------------------------------------------------------------
# validate_entry — step validation
# ---------------------------------------------------------------------------

class TestValidateEntryStep:
    def test_step_zero(self):
        errors = validate_entry(_valid_entry(step=0), _ctx())
        assert any("step must be a positive integer" in e for e in errors)

    def test_step_negative(self):
        errors = validate_entry(_valid_entry(step=-5), _ctx())
        assert any("step must be a positive integer" in e for e in errors)

    def test_step_non_integer(self):
        errors = validate_entry(_valid_entry(step="1"), _ctx())
        assert any("step must be a positive integer" in e for e in errors)

    def test_step_exceeds_max_step(self):
        errors = validate_entry(_valid_entry(step=100), _ctx(max_step=10))
        assert any("step must be <= 10" in e for e in errors)

    def test_step_equals_max_step_ok(self):
        errors = validate_entry(_valid_entry(step=10), _ctx(max_step=10))
        assert not any("step must be <=" in e for e in errors)

    def test_step_below_max_step_ok(self):
        errors = validate_entry(_valid_entry(step=5), _ctx(max_step=10))
        assert not any("step must be <=" in e for e in errors)

    def test_max_step_none_skips_check(self):
        errors = validate_entry(_valid_entry(step=9999), _ctx(max_step=None))
        assert not any("step must be <=" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_entry — source_agent validation
# ---------------------------------------------------------------------------

class TestValidateEntrySourceAgent:
    def test_invalid_source_agent(self):
        errors = validate_entry(_valid_entry(source_agent="bogus"), _ctx())
        assert any("source_agent must be one of" in e for e in errors)

    def test_valid_source_agents_none_skips_check(self):
        errors = validate_entry(_valid_entry(source_agent="bogus"), _ctx(valid_source_agents=None))
        assert not any("source_agent must be one of" in e for e in errors)

    def test_cascade_agent_passes_when_whitelist_disabled(self):
        """With the whitelist opt-out (valid_source_agents=None), an IDE agent
        name like 'cascade' validates — the real-world unblock for the
        quarantined cascade entry."""
        errors = validate_entry(_valid_entry(source_agent="cascade"), _ctx(valid_source_agents=None))
        assert errors == []


# ---------------------------------------------------------------------------
# validate_entry — type, domain, severity validation
# ---------------------------------------------------------------------------

class TestValidateEntryTypeDomainSeverity:
    def test_invalid_type(self):
        errors = validate_entry(_valid_entry(type="bogus"), _ctx())
        assert any("type must be one of" in e for e in errors)

    def test_invalid_domain(self):
        errors = validate_entry(_valid_entry(domain="bogus"), _ctx())
        assert any("domain must be one of" in e for e in errors)

    def test_valid_domains_none_skips_check(self):
        errors = validate_entry(_valid_entry(domain="bogus"), _ctx(valid_domains=None))
        assert not any("domain must be one of" in e for e in errors)

    def test_invalid_severity(self):
        errors = validate_entry(_valid_entry(severity="bogus"), _ctx())
        assert any("severity must be one of" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_entry — importance validation
# ---------------------------------------------------------------------------

class TestValidateEntryImportance:
    def test_importance_zero(self):
        errors = validate_entry(_valid_entry(importance=0), _ctx())
        assert any("importance must be integer 1-10" in e for e in errors)

    def test_importance_eleven(self):
        errors = validate_entry(_valid_entry(importance=11), _ctx())
        assert any("importance must be integer 1-10" in e for e in errors)

    def test_importance_non_integer(self):
        errors = validate_entry(_valid_entry(importance=5.5), _ctx())
        assert any("importance must be integer 1-10" in e for e in errors)

    def test_importance_boundary_one(self):
        errors = validate_entry(_valid_entry(importance=1), _ctx())
        assert not any("importance" in e for e in errors)

    def test_importance_boundary_ten(self):
        errors = validate_entry(_valid_entry(importance=10), _ctx())
        assert not any("importance" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_entry — components and files_touched validation
# ---------------------------------------------------------------------------

class TestValidateEntryComponentsFiles:
    def test_components_empty_list(self):
        errors = validate_entry(_valid_entry(components=[]), _ctx())
        assert any("components must be non-empty list" in e for e in errors)

    def test_components_non_list(self):
        errors = validate_entry(_valid_entry(components="CollisionSystem"), _ctx())
        assert any("components must be non-empty list" in e for e in errors)

    def test_components_non_string_elements(self):
        errors = validate_entry(_valid_entry(components=[1, 2]), _ctx())
        assert any("components must be list of strings" in e for e in errors)

    def test_files_touched_empty_list(self):
        errors = validate_entry(_valid_entry(files_touched=[]), _ctx())
        assert any("files_touched must be non-empty list" in e for e in errors)

    def test_files_touched_non_string_elements(self):
        errors = validate_entry(_valid_entry(files_touched=[1]), _ctx())
        assert any("files_touched must be list of strings" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_entry — trigger and action validation
# ---------------------------------------------------------------------------

class TestValidateEntryTriggerAction:
    def test_trigger_not_starting_with_when(self):
        errors = validate_entry(_valid_entry(trigger="If collision detected"), _ctx())
        assert any("trigger must start with 'When'" in e for e in errors)

    def test_trigger_when_case_insensitive(self):
        errors = validate_entry(_valid_entry(trigger="when collision detected"), _ctx())
        assert not any("trigger must start with" in e for e in errors)

    def test_trigger_empty_string(self):
        errors = validate_entry(_valid_entry(trigger="   "), _ctx())
        assert any("trigger must be non-empty string" in e for e in errors)

    def test_trigger_non_string(self):
        errors = validate_entry(_valid_entry(trigger=123), _ctx())
        assert any("trigger must be non-empty string" in e for e in errors)

    def test_action_missing_always_and_never(self):
        errors = validate_entry(_valid_entry(action="use broadphase"), _ctx())
        assert any("action must contain 'ALWAYS' or 'NEVER'" in e for e in errors)

    def test_action_with_always_case_insensitive(self):
        errors = validate_entry(_valid_entry(action="always use broadphase"), _ctx())
        assert not any("action must contain" in e for e in errors)

    def test_action_with_never(self):
        errors = validate_entry(_valid_entry(action="NEVER skip broadphase"), _ctx())
        assert not any("action must contain" in e for e in errors)

    def test_action_empty_string(self):
        errors = validate_entry(_valid_entry(action="   "), _ctx())
        assert any("action must be non-empty string" in e for e in errors)

    def test_reason_empty_string(self):
        errors = validate_entry(_valid_entry(reason="   "), _ctx())
        assert any("reason must be non-empty string" in e for e in errors)

    def test_reason_non_string(self):
        errors = validate_entry(_valid_entry(reason=None), _ctx())
        assert any("reason must be non-empty string" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_entry — optional field validation
# ---------------------------------------------------------------------------

class TestValidateEntryOptionalFields:
    def test_reinforcement_count_negative(self):
        errors = validate_entry(_valid_entry(reinforcement_count=-1), _ctx())
        assert any("reinforcement_count must be non-negative" in e for e in errors)

    def test_reinforcement_count_non_integer(self):
        errors = validate_entry(_valid_entry(reinforcement_count=2.5), _ctx())
        assert any("reinforcement_count must be non-negative" in e for e in errors)

    def test_reinforcement_count_zero_ok(self):
        errors = validate_entry(_valid_entry(reinforcement_count=0), _ctx())
        assert not any("reinforcement_count" in e for e in errors)

    def test_verified_non_bool(self):
        errors = validate_entry(_valid_entry(verified="yes"), _ctx())
        assert any("verified must be boolean" in e for e in errors)

    def test_verified_true_ok(self):
        errors = validate_entry(_valid_entry(verified=True), _ctx())
        assert not any("verified" in e for e in errors)

    def test_verified_false_ok(self):
        errors = validate_entry(_valid_entry(verified=False), _ctx())
        assert not any("verified" in e for e in errors)

    def test_scope_invalid(self):
        errors = validate_entry(_valid_entry(scope="bogus"), _ctx())
        assert any("scope must be one of" in e for e in errors)

    def test_scope_valid(self):
        for s in VALID_SCOPES:
            errors = validate_entry(_valid_entry(scope=s), _ctx())
            assert not any("scope" in e for e in errors)

    def test_symptoms_non_string(self):
        errors = validate_entry(_valid_entry(symptoms=123), _ctx())
        assert any("symptoms must be string" in e for e in errors)

    def test_symptoms_string_ok(self):
        errors = validate_entry(_valid_entry(symptoms="high latency"), _ctx())
        assert not any("symptoms" in e for e in errors)

    def test_debt_level_invalid(self):
        errors = validate_entry(_valid_entry(debt_level="bogus"), _ctx())
        assert any("debt_level must be one of" in e for e in errors)

    def test_debt_level_valid(self):
        for d in VALID_DEBT_LEVELS:
            errors = validate_entry(_valid_entry(debt_level=d), _ctx())
            assert not any("debt_level" in e for e in errors)

    def test_schema_version_non_integer(self):
        errors = validate_entry(_valid_entry(schema_version="2"), _ctx())
        assert any("schema_version must be an integer" in e for e in errors)

    def test_schema_version_bool_rejected(self):
        errors = validate_entry(_valid_entry(schema_version=True), _ctx())
        assert any("schema_version must be an integer" in e for e in errors)

    def test_schema_version_integer_ok(self):
        errors = validate_entry(_valid_entry(schema_version=2), _ctx())
        assert not any("schema_version" in e for e in errors)


# ---------------------------------------------------------------------------
# jaccard_similarity
# ---------------------------------------------------------------------------

class TestJaccardSimilarity:
    def test_identical_text(self):
        assert jaccard_similarity("hello world", "hello world") == 1.0

    def test_completely_different(self):
        assert jaccard_similarity("alpha beta", "gamma delta") == 0.0

    def test_partial_overlap(self):
        # intersection: {"the"} = 1, union: {"the", "cat", "dog"} = 3
        assert jaccard_similarity("the cat", "the dog") == pytest.approx(1 / 3)

    def test_both_empty(self):
        assert jaccard_similarity("", "") == 0.0

    def test_one_empty(self):
        assert jaccard_similarity("hello", "") == 0.0

    def test_case_insensitive(self):
        assert jaccard_similarity("Hello World", "hello world") == 1.0


# ---------------------------------------------------------------------------
# actions_oppose
# ---------------------------------------------------------------------------

class TestActionsOppose:
    def test_always_vs_never(self):
        assert actions_oppose("ALWAYS do X", "NEVER do X") is True

    def test_never_vs_always(self):
        assert actions_oppose("NEVER do X", "ALWAYS do X") is True

    def test_always_vs_always(self):
        assert actions_oppose("ALWAYS do X", "ALWAYS do Y") is False

    def test_never_vs_never(self):
        assert actions_oppose("NEVER do X", "NEVER do Y") is False

    def test_case_insensitive(self):
        assert actions_oppose("always test", "never skip") is True

    def test_no_keywords(self):
        assert actions_oppose("sometimes do X", "maybe do Y") is False

    def test_both_keywords_in_one(self):
        # An action containing both ALWAYS and NEVER opposes any action with ALWAYS
        assert actions_oppose("ALWAYS NEVER do X", "ALWAYS do Y") is True


# ---------------------------------------------------------------------------
# find_best_match
# ---------------------------------------------------------------------------

class TestFindBestMatch:
    def test_no_overlap_returns_zero(self):
        entry = _valid_entry(components=["Alpha"])
        entries = [{"components": ["Beta"], "trigger": "When X", "action": "ALWAYS Y", "resolved": False}]
        sim, match = find_best_match(entry, entries)
        assert sim == 0.0
        assert match is None

    def test_exact_match_returns_highest(self):
        entry = _valid_entry(components=["Comp"], trigger="When test", action="ALWAYS test")
        existing = {"components": ["Comp"], "trigger": "When test", "action": "ALWAYS test", "resolved": False}
        sim, match = find_best_match(entry, [existing])
        assert sim == 1.0
        assert match is existing

    def test_skips_resolved(self):
        entry = _valid_entry(components=["Comp"], trigger="When test", action="ALWAYS test")
        resolved = {"components": ["Comp"], "trigger": "When test", "action": "ALWAYS test", "resolved": True}
        sim, match = find_best_match(entry, [resolved])
        assert sim == 0.0
        assert match is None

    def test_picks_highest_similarity(self):
        entry = _valid_entry(components=["Comp"], trigger="When alpha beta", action="ALWAYS gamma")
        close = {"components": ["Comp"], "trigger": "When alpha beta", "action": "ALWAYS gamma", "resolved": False}
        far = {"components": ["Comp"], "trigger": "When delta epsilon", "action": "ALWAYS zeta", "resolved": False}
        sim, match = find_best_match(entry, [far, close])
        assert match is close

    def test_component_match_case_insensitive(self):
        entry = _valid_entry(components=["MyComp"], trigger="When test", action="ALWAYS test")
        existing = {"components": ["mycomp"], "trigger": "When test", "action": "ALWAYS test", "resolved": False}
        sim, match = find_best_match(entry, [existing])
        assert match is existing

    def test_tie_goes_to_first(self):
        """find_best_match uses > not >=, so ties go to first entry."""
        entry = _valid_entry(components=["Comp"], trigger="When alpha", action="ALWAYS beta")
        e1 = {"components": ["Comp"], "trigger": "When alpha", "action": "ALWAYS beta", "resolved": False}
        e2 = {"components": ["Comp"], "trigger": "When alpha", "action": "ALWAYS beta", "resolved": False}
        sim, match = find_best_match(entry, [e1, e2])
        assert match is e1

    def test_empty_entries(self):
        entry = _valid_entry()
        sim, match = find_best_match(entry, [])
        assert sim == 0.0
        assert match is None


# ---------------------------------------------------------------------------
# score_entry
# ---------------------------------------------------------------------------

class TestScoreEntry:
    def _ctx(self):
        return {
            "decay_rate": 0.99,
            "component_weight": 1.0,
            "file_weight": 0.7,
            "domain_weight": 0.4,
            "no_match_weight": 0.1,
        }

    def test_component_match_highest_weight(self):
        entry = _valid_entry(step=1, importance=10, components=["Comp"])
        score = score_entry(entry, 1, ["Comp"], [], "tooling", self._ctx())
        assert score == pytest.approx(1.0 * 1.0 * 1.0)  # recency=1, importance=1, weight=1

    def test_file_match(self):
        entry = _valid_entry(step=1, importance=10, components=["Other"], files_touched=["app.py"])
        score = score_entry(entry, 1, ["Comp"], ["app.py"], "tooling", self._ctx())
        assert score == pytest.approx(1.0 * 1.0 * 0.7)

    def test_domain_match(self):
        entry = _valid_entry(step=1, importance=10, components=["Other"], files_touched=["other.py"])
        score = score_entry(entry, 1, ["Comp"], ["app.py"], "tooling", self._ctx())
        assert score == pytest.approx(1.0 * 1.0 * 0.4)

    def test_no_match(self):
        entry = _valid_entry(step=1, importance=10, components=["Other"], files_touched=["other.py"], domain="security")
        score = score_entry(entry, 1, ["Comp"], ["app.py"], "tooling", self._ctx())
        assert score == pytest.approx(1.0 * 1.0 * 0.1)

    def test_decay_applied(self):
        entry = _valid_entry(step=1, importance=10, components=["Comp"])
        # step_diff = 10, decay = 0.99^10
        score = score_entry(entry, 11, ["Comp"], [], "tooling", self._ctx())
        expected = (0.99 ** 10) * 1.0 * 1.0
        assert score == pytest.approx(expected)

    def test_importance_scaled(self):
        entry = _valid_entry(step=1, importance=5, components=["Comp"])
        score = score_entry(entry, 1, ["Comp"], [], "tooling", self._ctx())
        assert score == pytest.approx(1.0 * 0.5 * 1.0)

    def test_component_match_case_insensitive(self):
        entry = _valid_entry(step=1, importance=10, components=["MyComp"])
        score = score_entry(entry, 1, ["mycomp"], [], "tooling", self._ctx())
        # Should match (component weight), not no-match
        assert score == pytest.approx(1.0 * 1.0 * 1.0)

    def test_file_match_takes_precedence_over_domain(self):
        """File match should win over domain match when components don't match."""
        entry = _valid_entry(step=1, importance=10, components=["Other"], files_touched=["app.py"], domain="tooling")
        score = score_entry(entry, 1, ["Comp"], ["app.py"], "tooling", self._ctx())
        assert score == pytest.approx(1.0 * 1.0 * 0.7)


# ---------------------------------------------------------------------------
# is_in_retention
# ---------------------------------------------------------------------------

class TestIsInRetention:
    def _ctx(self):
        return {"major_retention": 20, "minor_retention": 5}

    def test_critical_always_retained(self):
        entry = _valid_entry(step=1, severity="critical")
        assert is_in_retention(entry, 9999, self._ctx()) is True

    def test_major_within_window(self):
        entry = _valid_entry(step=1, severity="major")
        assert is_in_retention(entry, 21, self._ctx()) is True

    def test_major_outside_window(self):
        entry = _valid_entry(step=1, severity="major")
        assert is_in_retention(entry, 22, self._ctx()) is False

    def test_major_at_boundary(self):
        entry = _valid_entry(step=1, severity="major")
        assert is_in_retention(entry, 1 + 20, self._ctx()) is True  # step_diff == major_retention

    def test_minor_within_window(self):
        entry = _valid_entry(step=1, severity="minor")
        assert is_in_retention(entry, 6, self._ctx()) is True

    def test_minor_outside_window_no_access(self):
        entry = _valid_entry(step=1, severity="minor")
        assert is_in_retention(entry, 7, self._ctx()) is False

    def test_minor_outside_window_with_access_count(self):
        entry = _valid_entry(step=1, severity="minor", access_count=4)
        assert is_in_retention(entry, 7, self._ctx()) is True

    def test_minor_access_count_boundary(self):
        entry = _valid_entry(step=1, severity="minor", access_count=3)
        assert is_in_retention(entry, 7, self._ctx()) is False  # access_count > 3 is False

    def test_unknown_severity_returns_false(self):
        entry = _valid_entry(step=1, severity="bogus")
        assert is_in_retention(entry, 1, self._ctx()) is False


# ---------------------------------------------------------------------------
# score_for_promotion
# ---------------------------------------------------------------------------

class TestScoreForPromotion:
    def test_max_score_bounded(self):
        """Maximum possible score is 1.0 (all sub-scores = 1.0)."""
        entry = {"access_count": 100, "severity": "critical", "step": 1}
        score = score_for_promotion(entry, 1, {})
        assert score == pytest.approx(0.4 * 1.0 + 0.4 * 1.0 + 0.2 * 1.0)

    def test_min_score_floor(self):
        """With zero access, minor severity, and old step, only severity contributes."""
        entry = {"access_count": 0, "severity": "minor", "step": 1}
        score = score_for_promotion(entry, 100, {})
        # access_score=0, severity_score=0.3, recency=0 → 0.4*0 + 0.4*0.3 + 0.2*0 = 0.12
        assert score == pytest.approx(0.12)

    def test_access_count_capped_at_10(self):
        """access_score = min(access_count / 10, 1.0) — capped at 1.0."""
        entry_low = {"access_count": 5, "severity": "minor", "step": 1}
        entry_high = {"access_count": 50, "severity": "minor", "step": 1}
        score_low = score_for_promotion(entry_low, 1, {})
        score_high = score_for_promotion(entry_high, 1, {})
        # severity_score and recency_score are the same, only access differs
        # but both should be different since 5/10=0.5 vs min(50/10,1.0)=1.0
        assert score_high > score_low

    def test_severity_weights(self):
        """critical=1.0, major=0.6, minor=0.3."""
        base = {"access_count": 0, "step": 1}
        s_crit = score_for_promotion({**base, "severity": "critical"}, 1, {})
        s_major = score_for_promotion({**base, "severity": "major"}, 1, {})
        s_minor = score_for_promotion({**base, "severity": "minor"}, 1, {})
        assert s_crit > s_major > s_minor

    def test_recency_decay(self):
        """Older entries get lower recency_score."""
        entry = {"access_count": 0, "severity": "minor", "step": 1}
        score_recent = score_for_promotion(entry, 1, {})
        score_old = score_for_promotion(entry, 31, {})
        assert score_recent > score_old

    def test_unknown_severity_defaults_to_minor(self):
        entry = {"access_count": 0, "severity": "bogus", "step": 1}
        score = score_for_promotion(entry, 1, {})
        score_minor = score_for_promotion({"access_count": 0, "severity": "minor", "step": 1}, 1, {})
        assert score == score_minor


# ---------------------------------------------------------------------------
# is_promotion_candidate
# ---------------------------------------------------------------------------

class TestIsPromotionCandidate:
    def test_high_score_promotes(self):
        entry = {"access_count": 20, "severity": "critical", "step": 1}
        is_candidate, score = is_promotion_candidate(entry, 1, {})
        assert is_candidate is True
        assert score >= 0.5

    def test_low_score_no_promote(self):
        entry = {"access_count": 0, "severity": "minor", "step": 100}
        is_candidate, score = is_promotion_candidate(entry, 100, {})
        assert is_candidate is False

    def test_critical_always_promoted(self):
        entry = {"access_count": 0, "severity": "critical", "step": 100}
        is_candidate, _ = is_promotion_candidate(entry, 100, {})
        assert is_candidate is True

    def test_high_access_count_promotes(self):
        entry = {"access_count": 6, "severity": "minor", "step": 100}
        is_candidate, _ = is_promotion_candidate(entry, 100, {})
        assert is_candidate is True

    def test_access_count_boundary_not_promoted(self):
        """access_count == 5, minor, very old step — score < 0.5 and access not > 5."""
        entry = {"access_count": 5, "severity": "minor", "step": 1}
        is_candidate, _ = is_promotion_candidate(entry, 100, {})
        # score = 0.4*0.5 + 0.4*0.3 + 0.2*0 = 0.26 < 0.5, access_count=5 not > 5
        assert is_candidate is False


# ---------------------------------------------------------------------------
# detect_contradictions
# ---------------------------------------------------------------------------

class TestDetectContradictions:
    def test_no_entries(self):
        assert detect_contradictions([]) == []

    def test_no_architectural_patterns(self):
        entries = [_valid_entry(type="bug_fix"), _valid_entry(type="optimization")]
        assert detect_contradictions(entries) == []

    def test_architectural_pattern_without_keywords(self):
        entry = _valid_entry(type="architectural_pattern", reason="Use microservices for scaling")
        assert detect_contradictions([entry]) == []

    @pytest.mark.parametrize("keyword", [
        "supersede", "outdated", "no longer applies", "conflicts with", "replaces",
    ])
    def test_each_contradiction_keyword(self, keyword):
        entry = _valid_entry(type="architectural_pattern", reason=f"This pattern {keyword} the old one")
        result = detect_contradictions([entry])
        assert len(result) == 1
        assert result[0] is entry

    def test_only_architectural_patterns_checked(self):
        """bug_fix entries with contradiction keywords are not flagged."""
        entry = _valid_entry(type="bug_fix", reason="This supersede the old approach")
        assert detect_contradictions([entry]) == []

    def test_mixed_entries(self):
        entries = [
            _valid_entry(type="bug_fix", reason="supersede"),
            _valid_entry(type="architectural_pattern", reason="This replaces the old pattern"),
            _valid_entry(type="optimization", reason="outdated"),
            _valid_entry(type="architectural_pattern", reason="clean design"),
        ]
        result = detect_contradictions(entries)
        assert len(result) == 1
        assert result[0] is entries[1]


# ---------------------------------------------------------------------------
# infer_sprint_number
# ---------------------------------------------------------------------------

class TestInferSprintNumber:
    def test_empty_entries(self):
        assert infer_sprint_number([]) == 1

    def test_step_1_to_10_is_sprint_1(self):
        entries = [{"step": 1}, {"step": 5}, {"step": 10}]
        assert infer_sprint_number(entries) == 1

    def test_step_11_is_sprint_2(self):
        entries = [{"step": 11}]
        assert infer_sprint_number(entries) == 2

    def test_step_20_is_sprint_2(self):
        entries = [{"step": 20}]
        assert infer_sprint_number(entries) == 2

    def test_step_21_is_sprint_3(self):
        entries = [{"step": 21}]
        assert infer_sprint_number(entries) == 3

    def test_mixed_steps_uses_max(self):
        entries = [{"step": 1}, {"step": 5}, {"step": 25}]
        assert infer_sprint_number(entries) == 3

    def test_entries_missing_step_key(self):
        entries = [{"step": 5}, {"other": "no step key"}]
        assert infer_sprint_number(entries) == 1


# ---------------------------------------------------------------------------
# get_agents_md_suggestions
# ---------------------------------------------------------------------------

class TestGetAgentsMdSuggestions:
    def test_no_entries(self):
        assert get_agents_md_suggestions([]) == []

    def test_agents_md_in_files_touched(self):
        entry = _valid_entry(files_touched=["AGENTS.md", "other.py"])
        result = get_agents_md_suggestions([entry])
        assert len(result) == 1
        assert result[0] is entry

    def test_agents_in_components(self):
        entry = _valid_entry(components=["AgentsHelper"])
        result = get_agents_md_suggestions([entry])
        assert len(result) == 1

    def test_agents_lowercase_in_components(self):
        entry = _valid_entry(components=["agents"])
        result = get_agents_md_suggestions([entry])
        assert len(result) == 1

    def test_no_match(self):
        entry = _valid_entry(files_touched=["other.py"], components=["OtherComp"])
        assert get_agents_md_suggestions([entry]) == []

    def test_mixed_entries(self):
        e1 = _valid_entry(files_touched=["AGENTS.md"])
        e2 = _valid_entry(files_touched=["other.py"], components=["OtherComp"])
        e3 = _valid_entry(components=["AgentsManager"])
        result = get_agents_md_suggestions([e1, e2, e3])
        assert len(result) == 2
        assert result[0] is e1
        assert result[1] is e3

    def test_partial_path_match(self):
        """Any file path containing 'AGENTS.md' substring matches."""
        entry = _valid_entry(files_touched=["docs/AGENTS.md"])
        result = get_agents_md_suggestions([entry])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Auto-learn: _derive_domain
# ---------------------------------------------------------------------------

class TestDeriveDomain:
    def test_database(self):
        assert _derive_domain("src/db/connection.py") == "database"
        assert _derive_domain("migrations/001_init.sql") == "database"

    def test_frontend(self):
        assert _derive_domain("src/components/Button.tsx") == "frontend"
        assert _derive_domain("pages/index.vue") == "frontend"

    def test_api(self):
        assert _derive_domain("api/routes/users.py") == "api"

    def test_security(self):
        assert _derive_domain("auth/login.py") == "security"

    def test_testing(self):
        assert _derive_domain("tests/test_foo.py") == "testing"

    def test_deployment(self):
        assert _derive_domain("docker/Dockerfile") == "deployment"
        assert _derive_domain(".github/workflows/ci.yml") == "deployment"

    def test_fallback_tooling(self):
        assert _derive_domain("some/random/file.xyz") == "tooling"

    def test_windows_paths(self):
        assert _derive_domain("src\\db\\connection.py") == "database"


# ---------------------------------------------------------------------------
# Auto-learn: detect_under_retrieved
# ---------------------------------------------------------------------------

class TestDetectUnderRetrieved:
    def _al_ctx(self, **overrides):
        base = {
            "auto_learn_under_retrieved_access": 2,
            "auto_learn_under_retrieved_reinforcement": 5,
        }
        base.update(overrides)
        return base

    def test_detects_under_retrieved(self):
        entries = [
            _valid_entry(access_count=1, reinforcement_count=6),
        ]
        results = detect_under_retrieved(entries, self._al_ctx())
        assert len(results) == 1
        assert results[0]["type"] == "meta_learning"
        assert results[0]["resolved"] is True
        assert "ALWAYS" in results[0]["action"]

    def test_skips_resolved(self):
        entries = [
            _valid_entry(access_count=1, reinforcement_count=6, resolved=True),
        ]
        results = detect_under_retrieved(entries, self._al_ctx())
        assert len(results) == 0

    def test_skips_high_access(self):
        entries = [
            _valid_entry(access_count=5, reinforcement_count=6),
        ]
        results = detect_under_retrieved(entries, self._al_ctx())
        assert len(results) == 0

    def test_skips_low_reinforcement(self):
        entries = [
            _valid_entry(access_count=1, reinforcement_count=2),
        ]
        results = detect_under_retrieved(entries, self._al_ctx())
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Auto-learn: detect_conflicts
# ---------------------------------------------------------------------------

class TestDetectConflicts:
    def test_detects_opposing_actions(self):
        e1 = _valid_entry(components=["Cache"], action="ALWAYS use Redis")
        e2 = _valid_entry(components=["Cache"], action="NEVER use Redis")
        results = detect_conflicts([e1, e2], {})
        assert len(results) == 1
        assert results[0]["type"] == "meta_learning"
        assert results[0]["resolved"] is True
        assert "NEVER" in results[0]["action"]

    def test_no_conflict_same_direction(self):
        e1 = _valid_entry(components=["Cache"], action="ALWAYS use Redis")
        e2 = _valid_entry(components=["Cache"], action="ALWAYS flush on write")
        results = detect_conflicts([e1, e2], {})
        assert len(results) == 0

    def test_no_conflict_different_components(self):
        e1 = _valid_entry(components=["Cache"], action="ALWAYS use Redis")
        e2 = _valid_entry(components=["Auth"], action="NEVER use Redis")
        results = detect_conflicts([e1, e2], {})
        assert len(results) == 0

    def test_skips_resolved(self):
        e1 = _valid_entry(components=["Cache"], action="ALWAYS use Redis", resolved=True)
        e2 = _valid_entry(components=["Cache"], action="NEVER use Redis")
        results = detect_conflicts([e1, e2], {})
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Auto-learn: detect_over_injected
# ---------------------------------------------------------------------------

class TestDetectOverInjected:
    def _al_ctx(self, **overrides):
        base = {
            "auto_learn_over_injected_access": 10,
            "auto_learn_over_injected_reinforcement": 2,
        }
        base.update(overrides)
        return base

    def test_detects_over_injected(self):
        entry = _valid_entry(access_count=12, reinforcement_count=1, ts="2026-01-01T00:00:00Z")
        log_events = []  # no subsequent coverage
        staleness_map = {}
        results = detect_over_injected([entry], log_events, staleness_map, self._al_ctx())
        assert len(results) == 1
        assert results[0]["type"] == "meta_learning"
        assert "NEVER" in results[0]["action"]

    def test_skips_subsequent_coverage(self):
        entry = _valid_entry(access_count=12, reinforcement_count=1, ts="2026-01-01T00:00:00Z",
                             components=["Cache"])
        log_events = [
            {"ts": "2026-02-01T00:00:00Z", "entry_components": ["Cache"]},
        ]
        results = detect_over_injected([entry], log_events, {}, self._al_ctx())
        assert len(results) == 0

    def test_staleness_boost(self):
        entry = _valid_entry(access_count=6, reinforcement_count=1, ts="2026-01-01T00:00:00Z")
        staleness_map = {entry["ts"]: True}
        results = detect_over_injected([entry], [], staleness_map, self._al_ctx())
        assert len(results) == 1  # threshold halved from 10 to 5


# ---------------------------------------------------------------------------
# Auto-learn: detect_repeated_fixes
# ---------------------------------------------------------------------------

class TestDetectRepeatedFixes:
    def test_detects_repeated_fixes(self):
        commits = [
            {"hash": "a1", "message": "fix: crash in connection", "files": ["src/db/conn.py"]},
            {"hash": "a2", "message": "bug: null pointer in connection", "files": ["src/db/conn.py"]},
            {"hash": "a3", "message": "fix: timeout in connection", "files": ["src/db/conn.py"]},
        ]
        results = detect_repeated_fixes(commits, {"auto_learn_fix_commit_threshold": 3,
                                                   "auto_learn_max_files_per_commit": 5,
                                                   "auto_learn_git_scan_depth": 20})
        assert len(results) == 1
        assert results[0]["type"] == "bug_fix"
        assert results[0]["resolved"] is False
        assert results[0]["domain"] == "database"

    def test_skips_broad_commits(self):
        files = [f"file_{i}.py" for i in range(6)]
        commits = [
            {"hash": "a1", "message": "fix: stuff", "files": files},
            {"hash": "a2", "message": "fix: stuff", "files": files},
            {"hash": "a3", "message": "fix: stuff", "files": files},
        ]
        results = detect_repeated_fixes(commits, {"auto_learn_fix_commit_threshold": 3,
                                                   "auto_learn_max_files_per_commit": 5,
                                                   "auto_learn_git_scan_depth": 20})
        assert len(results) == 0

    def test_below_threshold(self):
        commits = [
            {"hash": "a1", "message": "fix: crash", "files": ["src/db/conn.py"]},
            {"hash": "a2", "message": "fix: crash", "files": ["src/db/conn.py"]},
        ]
        results = detect_repeated_fixes(commits, {"auto_learn_fix_commit_threshold": 3,
                                                   "auto_learn_max_files_per_commit": 5,
                                                   "auto_learn_git_scan_depth": 20})
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Auto-learn: detect_reverts
# ---------------------------------------------------------------------------

class TestDetectReverts:
    def test_detects_quoted_revert(self):
        commits = [
            {"hash": "a1", "message": 'Revert "feat: add Redis cache"', "files": ["src/cache.py"]},
        ]
        results, count = detect_reverts(commits, {})
        assert count == 1
        assert len(results) == 1
        assert results[0]["type"] == "bug_fix"
        assert results[0]["severity"] == "critical"
        assert "NEVER" in results[0]["action"]

    def test_skips_hash_only_revert(self):
        commits = [
            {"hash": "a1", "message": "Revert commit abc1234", "files": ["src/cache.py"]},
        ]
        results, count = detect_reverts(commits, {})
        assert count == 1
        assert len(results) == 0

    def test_no_reverts(self):
        commits = [
            {"hash": "a1", "message": "feat: add stuff", "files": ["src/app.py"]},
        ]
        results, count = detect_reverts(commits, {})
        assert count == 0
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Auto-learn: detect_retrieval_failure
# ---------------------------------------------------------------------------

class TestDetectRetrievalFailure:
    def test_detects_correlation(self):
        retrieval_events = [
            {"ts": "2026-01-01T00:00:00Z", "query_components": ["Auth"],
             "warnings_returned": 0, "patterns_returned": 0},
        ]
        log_events = [
            {"ts": "2026-01-02T00:00:00Z", "entry_type": "bug_fix",
             "entry_components": ["Auth"], "entry_files_touched": ["auth.py"],
             "entry_step": 5, "entry_domain": "security"},
        ]
        results = detect_retrieval_failure([], retrieval_events, log_events, None,
                                           {"auto_learn_retrieval_failure_cap": 100})
        assert len(results) == 1
        assert results[0]["type"] == "bug_fix"
        assert results[0]["resolved"] is False
        assert "ALWAYS" in results[0]["action"]

    def test_skips_nonzero_results(self):
        retrieval_events = [
            {"ts": "2026-01-01T00:00:00Z", "query_components": ["Auth"],
             "warnings_returned": 1, "patterns_returned": 0},
        ]
        log_events = [
            {"ts": "2026-01-02T00:00:00Z", "entry_type": "bug_fix",
             "entry_components": ["Auth"], "entry_files_touched": ["auth.py"],
             "entry_step": 5, "entry_domain": "security"},
        ]
        results = detect_retrieval_failure([], retrieval_events, log_events, None,
                                           {"auto_learn_retrieval_failure_cap": 100})
        assert len(results) == 0

    def test_skips_non_bug_fix(self):
        retrieval_events = [
            {"ts": "2026-01-01T00:00:00Z", "query_components": ["Auth"],
             "warnings_returned": 0, "patterns_returned": 0},
        ]
        log_events = [
            {"ts": "2026-01-02T00:00:00Z", "entry_type": "optimization",
             "entry_components": ["Auth"], "entry_files_touched": ["auth.py"],
             "entry_step": 5, "entry_domain": "security"},
        ]
        results = detect_retrieval_failure([], retrieval_events, log_events, None,
                                           {"auto_learn_retrieval_failure_cap": 100})
        assert len(results) == 0


# ---------------------------------------------------------------------------
# evaluate: _build_candidate
# ---------------------------------------------------------------------------

class TestBuildCandidate:
    def _summary(self, **overrides):
        base = {
            "step": 1,
            "components": ["Logger"],
            "files_touched": ["src/log.py"],
            "text": "some text",
        }
        base.update(overrides)
        return base

    def test_returns_none_when_files_touched_empty(self):
        c = _build_candidate(self._summary(files_touched=[]), _ctx(),
                             trigger="When X", action="ALWAYS Y", reason="r")
        assert c is None

    def test_returns_none_when_components_empty(self):
        c = _build_candidate(self._summary(components=[]), _ctx(),
                             trigger="When X", action="ALWAYS Y", reason="r")
        assert c is None

    def test_stamps_source_agent_system(self):
        c = _build_candidate(self._summary(), _ctx(),
                             trigger="When X", action="ALWAYS Y", reason="r")
        assert c["source_agent"] == "system"

    def test_stamps_resolved_false(self):
        c = _build_candidate(self._summary(), _ctx(),
                             trigger="When X", action="ALWAYS Y", reason="r")
        assert c["resolved"] is False

    def test_derives_domain_from_first_file(self):
        c = _build_candidate(self._summary(files_touched=["src/db/conn.py"]), _ctx(),
                             trigger="When X", action="ALWAYS Y", reason="r")
        assert c["domain"] == _derive_domain("src/db/conn.py")

    def test_debt_level_only_when_overridden(self):
        c = _build_candidate(self._summary(), _ctx(),
                             trigger="When X", action="ALWAYS Y", reason="r",
                             debt_level="workaround")
        assert c["debt_level"] == "workaround"

        c2 = _build_candidate(self._summary(), _ctx(),
                              trigger="When X", action="ALWAYS Y", reason="r")
        assert "debt_level" not in c2


# ---------------------------------------------------------------------------
# evaluate: detect_human_correction
# ---------------------------------------------------------------------------

class TestDetectHumanCorrection:
    def _summary(self, **overrides):
        base = {
            "step": 1,
            "prompt_type": "human",
            "outcome": "correction",
            "corrected_action": "use async writes",
            "rejected_action": "",
            "text": "corrected the logger",
            "components": ["Logger"],
            "files_touched": ["src/log.py"],
        }
        base.update(overrides)
        return base

    def test_detects_corrected_action(self):
        result = detect_human_correction(self._summary(), _ctx())
        assert result is not None
        confidence, candidate = result
        assert confidence == 0.95
        assert candidate["action"].startswith("ALWAYS ")

    def test_detects_rejected_action_only(self):
        result = detect_human_correction(
            self._summary(corrected_action="", rejected_action="use sync writes"), _ctx())
        assert result is not None
        confidence, candidate = result
        assert confidence == 0.95
        assert candidate["action"].startswith("NEVER ")

    def test_returns_none_when_not_human(self):
        assert detect_human_correction(self._summary(prompt_type="agent"), _ctx()) is None

    def test_returns_none_when_not_correction(self):
        assert detect_human_correction(self._summary(outcome="decision"), _ctx()) is None

    def test_returns_none_when_both_actions_empty(self):
        assert detect_human_correction(
            self._summary(corrected_action="", rejected_action=""), _ctx()) is None

    def test_returns_none_when_no_components(self):
        assert detect_human_correction(self._summary(components=[]), _ctx()) is None

    def test_returns_none_when_no_files(self):
        assert detect_human_correction(self._summary(files_touched=[]), _ctx()) is None


# ---------------------------------------------------------------------------
# evaluate: detect_explicit_remember
# ---------------------------------------------------------------------------

class TestDetectExplicitRemember:
    def _summary(self, **overrides):
        base = {
            "step": 1,
            "outcome": "preference",
            "text": "always use snake_case for variables",
            "components": ["StyleGuide"],
            "files_touched": ["src/style.py"],
        }
        base.update(overrides)
        return base

    def test_detects_remember_keyword(self):
        result = detect_explicit_remember(self._summary(), _ctx())
        assert result is not None
        confidence, candidate = result
        assert confidence == 0.85

    def test_detects_don_t_forget(self):
        result = detect_explicit_remember(
            self._summary(text="don't forget to validate input"), _ctx())
        assert result is not None
        assert result[0] == 0.85

    def test_returns_none_when_no_keyword(self):
        assert detect_explicit_remember(
            self._summary(text="use snake_case"), _ctx()) is None

    def test_returns_none_when_empty_text(self):
        assert detect_explicit_remember(self._summary(text=""), _ctx()) is None

    def test_returns_none_when_wrong_outcome(self):
        assert detect_explicit_remember(
            self._summary(outcome="bug_fixed"), _ctx()) is None

    def test_returns_none_when_no_components(self):
        assert detect_explicit_remember(self._summary(components=[]), _ctx()) is None


# ---------------------------------------------------------------------------
# evaluate: detect_bug_fixed
# ---------------------------------------------------------------------------

class TestDetectBugFixed:
    def _summary(self, **overrides):
        base = {
            "step": 1,
            "outcome": "bug_fixed",
            "error_text": "NullPointer in parser",
            "text": "",
            "components": ["Parser"],
            "files_touched": ["src/parser.py"],
        }
        base.update(overrides)
        return base

    def test_detects_with_error_text(self):
        result = detect_bug_fixed(self._summary(), _ctx())
        assert result is not None
        confidence, candidate = result
        assert confidence == 0.70
        assert candidate["type"] == "bug_fix"

    def test_detects_with_text_only(self):
        result = detect_bug_fixed(
            self._summary(error_text="", text="fixed parsing bug"), _ctx())
        assert result is not None
        assert result[0] == 0.70

    def test_returns_none_when_wrong_outcome(self):
        assert detect_bug_fixed(self._summary(outcome="decision"), _ctx()) is None

    def test_returns_none_when_no_error_or_text(self):
        assert detect_bug_fixed(
            self._summary(error_text="", text=""), _ctx()) is None

    def test_returns_none_when_no_components(self):
        assert detect_bug_fixed(self._summary(components=[]), _ctx()) is None


# ---------------------------------------------------------------------------
# evaluate: detect_decision
# ---------------------------------------------------------------------------

class TestDetectDecision:
    def _summary(self, **overrides):
        base = {
            "step": 1,
            "outcome": "decision",
            "text": "use event sourcing for audit log",
            "components": ["AuditLog"],
            "files_touched": ["src/audit.py"],
        }
        base.update(overrides)
        return base

    def test_detects_decision(self):
        result = detect_decision(self._summary(), _ctx())
        assert result is not None
        confidence, candidate = result
        assert confidence == 0.60

    def test_returns_none_when_wrong_outcome(self):
        assert detect_decision(self._summary(outcome="bug_fixed"), _ctx()) is None

    def test_returns_none_when_empty_text(self):
        assert detect_decision(self._summary(text=""), _ctx()) is None

    def test_returns_none_when_no_components(self):
        assert detect_decision(self._summary(components=[]), _ctx()) is None


# ---------------------------------------------------------------------------
# evaluate: detect_workaround
# ---------------------------------------------------------------------------

class TestDetectWorkaround:
    def _summary(self, **overrides):
        base = {
            "step": 1,
            "outcome": "workaround",
            "text": "skip flaky test on CI",
            "components": ["TestSuite"],
            "files_touched": ["tests/test_ci.py"],
        }
        base.update(overrides)
        return base

    def test_detects_workaround(self):
        result = detect_workaround(self._summary(), _ctx())
        assert result is not None
        confidence, candidate = result
        assert confidence == 0.55
        assert candidate["type"] == "bug_fix"
        assert candidate["debt_level"] == "workaround"

    def test_returns_none_when_wrong_outcome(self):
        assert detect_workaround(self._summary(outcome="decision"), _ctx()) is None

    def test_returns_none_when_empty_text(self):
        assert detect_workaround(self._summary(text=""), _ctx()) is None

    def test_returns_none_when_no_components(self):
        assert detect_workaround(self._summary(components=[]), _ctx()) is None


# ---------------------------------------------------------------------------
# evaluate: validate_entry rejects malformed candidates
# ---------------------------------------------------------------------------

class TestEvaluateCandidateValidation:
    def test_malformed_action_rejected_by_validate_entry(self):
        """A candidate with a non-ALWAYS/NEVER action must fail validate_entry."""
        candidate = _build_candidate(
            {"step": 1, "components": ["X"], "files_touched": ["x.py"]},
            _ctx(),
            trigger="When X",
            action="maybe do something",
            reason="r",
        )
        assert candidate is not None
        errors = validate_entry(candidate, _ctx())
        assert any("action must contain" in e for e in errors)


# ---------------------------------------------------------------------------
# read_learnings_for_dashboard
# ---------------------------------------------------------------------------

class TestReadLearningsForDashboard:
    def _paths(self, tmp_path, learnings=None, fakes=None):
        class _P:
            def __init__(self, tmp_path):
                self.memory_dir = str(tmp_path)
                self.learnings_path = str(tmp_path / "learnings.jsonl")
                self.config_path = str(tmp_path / "config.json")
                self.quarantine_path = str(tmp_path / "quarantine.jsonl")
                self.archive_dir = str(tmp_path / "archive")
                self.session_file = str(tmp_path / "session.json")
                self.repo_root = str(tmp_path)

        p = _P(tmp_path)
        import json as _json
        if learnings is not None:
            with open(p.learnings_path, "w", encoding="utf-8") as f:
                for e in learnings:
                    f.write(_json.dumps(e) + "\n")
        if fakes is not None:
            with open(str(tmp_path / "fakes.jsonl"), "w", encoding="utf-8") as f:
                for e in fakes:
                    f.write(_json.dumps(e) + "\n")
        return p

    def test_real_reads_learnings(self, tmp_path):
        from agent_memory.engine.io import read_learnings_for_dashboard

        paths = self._paths(tmp_path, learnings=[{"ts": "2024-01-01T00:00:00Z", "step": 1}])
        entries = read_learnings_for_dashboard(paths, {"data_source": "real"})
        assert len(entries) == 1
        assert entries[0]["ts"] == "2024-01-01T00:00:00Z"

    def test_fakes_reads_fakes(self, tmp_path):
        from agent_memory.engine.io import read_learnings_for_dashboard

        paths = self._paths(
            tmp_path,
            learnings=[{"ts": "2024-01-01T00:00:00Z", "step": 1}],
            fakes=[{"ts": "2024-01-02T00:00:00Z", "step": 2}, {"ts": "2024-01-03T00:00:00Z", "step": 3}],
        )
        entries = read_learnings_for_dashboard(paths, {"data_source": "fakes"})
        assert len(entries) == 2
        assert entries[0]["ts"] == "2024-01-02T00:00:00Z"

    def test_no_data_source_key_defaults_to_real(self, tmp_path):
        from agent_memory.engine.io import read_learnings_for_dashboard

        paths = self._paths(tmp_path, learnings=[{"ts": "2024-01-01T00:00:00Z", "step": 1}])
        entries = read_learnings_for_dashboard(paths, {})
        assert len(entries) == 1
        assert entries[0]["ts"] == "2024-01-01T00:00:00Z"

    def test_fakes_missing_file_returns_empty(self, tmp_path):
        from agent_memory.engine.io import read_learnings_for_dashboard

        paths = self._paths(tmp_path, learnings=[{"ts": "2024-01-01T00:00:00Z", "step": 1}])
        entries = read_learnings_for_dashboard(paths, {"data_source": "fakes"})
        assert entries == []


# ---------------------------------------------------------------------------
# templates/config.json vs constants.DEFAULTS drift guard
# ---------------------------------------------------------------------------

class TestTemplateConfigDrift:
    """Guard against silent divergence between the scaffolded template config and
    the engine's hardcoded DEFAULTS. This is the class of bug where lowering a
    threshold in constants.py alone has no effect for projects that copy the
    template (and vice versa)."""

    # Keys whose template value intentionally differs from the constant default.
    # sleep_cycle_days: constant default is 1 (eager), template recommends 7.
    KNOWN_DIVERGENCES = {"sleep_cycle_days"}

    def test_template_tuning_matches_defaults(self):
        import json

        from agent_memory.engine.constants import DEFAULTS

        template_path = Path(__file__).parent.parent / "templates" / "config.json"
        with open(template_path, encoding="utf-8") as f:
            tuning = json.load(f)["tuning"]

        mismatches = []
        for key, value in tuning.items():
            if key in self.KNOWN_DIVERGENCES:
                continue
            default_key = key.upper()
            if default_key not in DEFAULTS:
                continue  # tuning-only key with no constant counterpart
            if DEFAULTS[default_key] != value:
                mismatches.append((key, value, DEFAULTS[default_key]))

        assert not mismatches, (
            "templates/config.json drifted from constants.DEFAULTS "
            "(key, template, default): " + repr(mismatches)
        )
