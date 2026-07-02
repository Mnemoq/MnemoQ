"""R7 — Sleep Cycle quality: tunable promotion scoring, staleness tiers, and the
promotion feedback loop.

Direct-import style (per the test_triggers/test_config exception in AGENTS.md).
"""
import json
from pathlib import Path

from conftest import _make_ctx, _make_paths

from mnemoq.engine.consolidation import (
    consolidate_core,
    is_promotion_candidate,
    score_for_promotion,
)
from mnemoq.engine.constants import DEFAULTS
from mnemoq.engine.git_utils import staleness_tier
from mnemoq.engine import promotion_feedback as pf


# --------------------------------------------------------------------------
# Change 1 — promotion scoring is config-tunable, defaults reproduce legacy math
# --------------------------------------------------------------------------

def _entry(access_count=0, severity="minor", step=0):
    return {"access_count": access_count, "severity": severity, "step": step,
            "trigger": "t", "domain": "backend"}


def test_score_reproduces_legacy_defaults():
    ctx = _make_ctx()
    # Legacy math: 0.4*min(6/10,1) + 0.4*0.6 + 0.2*max(0,1-10/30)
    #            = 0.4*0.6 + 0.24 + 0.2*(2/3) = 0.24 + 0.24 + 0.13333...
    e = _entry(access_count=6, severity="major", step=0)
    score = score_for_promotion(e, current_step=10, ctx=ctx)
    assert abs(score - (0.24 + 0.24 + 0.2 * (2 / 3))) < 1e-9


def test_score_moves_with_ctx_override():
    e = _entry(access_count=6, severity="major", step=0)
    base = score_for_promotion(e, 10, _make_ctx())
    # Halve the access divisor -> access_score saturates at 1.0 -> higher score.
    bumped = score_for_promotion(e, 10, _make_ctx(promotion_access_divisor=5.0))
    assert bumped > base


def test_candidate_default_threshold():
    ctx = _make_ctx()
    # score < 0.5, not critical, access not > 5 -> not a candidate
    weak = _entry(access_count=1, severity="minor", step=0)
    ok, _ = is_promotion_candidate(weak, 100, ctx)
    assert ok is False


def test_candidate_threshold_override_changes_result():
    weak = _entry(access_count=1, severity="minor", step=0)
    # Drop the cutoff below the weak entry's score -> becomes a candidate.
    score = score_for_promotion(weak, 100, _make_ctx())
    ctx = _make_ctx(promotion_score_threshold=score - 0.01)
    ok, _ = is_promotion_candidate(weak, 100, ctx)
    assert ok is True


def test_candidate_force_access_override():
    e = _entry(access_count=4, severity="minor", step=0)
    assert is_promotion_candidate(e, 100, _make_ctx())[0] is False
    # Lower the force-access bar below 4 -> forced candidate.
    assert is_promotion_candidate(e, 100, _make_ctx(promotion_force_access=3))[0] is True


# --------------------------------------------------------------------------
# Change 2 — staleness tiers
# --------------------------------------------------------------------------

def test_staleness_tier_boundaries():
    ctx = _make_ctx()  # minor=500, moderate=1500, severe=5000
    assert staleness_tier(499, ctx) == "none"
    assert staleness_tier(500, ctx) == "none"       # binary is_stale is > minor
    assert staleness_tier(501, ctx) == "minor"
    assert staleness_tier(1499, ctx) == "minor"
    assert staleness_tier(1500, ctx) == "moderate"
    assert staleness_tier(4999, ctx) == "moderate"
    assert staleness_tier(5000, ctx) == "severe"
    assert staleness_tier(50000, ctx) == "severe"


def test_staleness_tier_agrees_with_binary_floor():
    ctx = _make_ctx()
    # tier != 'none' exactly when lines_changed > auto_learn_staleness_threshold
    assert (staleness_tier(600, ctx) != "none") is (600 > 500)
    assert (staleness_tier(400, ctx) != "none") is (400 > 500)


# --------------------------------------------------------------------------
# Change 3 — promotion feedback loop
# --------------------------------------------------------------------------

def test_feedback_records_and_follows_up(temp_project):
    memory_dir = temp_project / "memory"
    paths = _make_paths(memory_dir, temp_project)

    e = {"step": 3, "trigger": "When touching backend", "domain": "backend",
         "access_count": 2}
    state = pf.load_state(paths)
    # First pass: propose e (access 2). Nothing tracked yet.
    summary = pf.record_and_follow_up(state, [(0.7, e)], {pf.entry_key(e): 2})
    assert summary["reinforced"] == 0
    pf.save_state(paths, state)
    assert (memory_dir / ".promotion_state.json").exists()

    # Second pass: same entry now accessed more -> reinforced.
    state2 = pf.load_state(paths)
    summary2 = pf.record_and_follow_up(state2, [], {pf.entry_key(e): 9})
    assert summary2["tracked"] == 1
    assert summary2["reinforced"] == 1


def test_feedback_corrupt_state_degrades(temp_project):
    memory_dir = temp_project / "memory"
    paths = _make_paths(memory_dir, temp_project)
    (memory_dir / ".promotion_state.json").write_text("{ not json", encoding="utf-8")
    # load_state must not raise; returns {}
    assert pf.load_state(paths) == {}


def test_consolidate_core_emits_follow_up(temp_project):
    memory_dir = temp_project / "memory"
    entry = {"ts": "2025-01-01T00:00:01Z", "step": 1, "source_agent": "system",
             "type": "architectural_pattern", "domain": "backend",
             "components": ["Thing"], "files_touched": ["src/thing.py"],
             "trigger": "When touching backend", "action": "do", "reason": "r",
             "importance": 5, "severity": "critical", "resolved": False,
             "access_count": 9}
    with open(memory_dir / "learnings.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    paths = _make_paths(memory_dir, temp_project)
    result = consolidate_core(None, False, True, paths, _make_ctx())
    assert result["exit_code"] == 0
    assert "promotion_follow_up" in result
    assert (memory_dir / ".promotion_state.json").exists()
    # critical entry is a forced candidate
    assert result["promotion_candidates"]


# --------------------------------------------------------------------------
# Config drift guard — new keys live in both DEFAULTS and the tuning template
# --------------------------------------------------------------------------

def test_new_keys_in_defaults_and_template():
    template = json.loads(
        (Path(__file__).resolve().parents[1] / "templates" / "config.json").read_text()
    )
    tuning = template["tuning"]
    new_keys = [
        "promotion_access_divisor", "promotion_recency_window",
        "promotion_severity_critical", "promotion_severity_major",
        "promotion_severity_minor", "promotion_weight_access",
        "promotion_weight_severity", "promotion_weight_recency",
        "promotion_score_threshold", "promotion_force_critical",
        "promotion_force_access", "staleness_moderate_threshold",
        "staleness_severe_threshold",
    ]
    for k in new_keys:
        assert k.upper() in DEFAULTS, f"{k.upper()} missing from DEFAULTS"
        assert k in tuning, f"{k} missing from templates/config.json tuning"
        assert DEFAULTS[k.upper()] == tuning[k], f"{k} drifted between DEFAULTS and template"
