"""Unit tests for the per-domain adaptive-threshold control loop.

Pure functions are tested directly (mirrors tests/test_config.py's direct-import
style via conftest helpers). End-to-end wiring through evaluate_core lives in
tests/test_evaluate.py.
"""
from conftest import _make_paths

from mnemoq.engine import homeostasis as hz


def _ctx(**over):
    base = {
        "adaptive_thresholds": True,
        "adaptive_bump": 0.02,
        "adaptive_decay": 0.9,
        "adaptive_reject_gain": 0.15,
        "adaptive_offset_floor": 0.1,
        "adaptive_offset_ceiling": 0.2,
        "adaptive_min_samples": 10,
        "adaptive_usefulness_gain": 0.1,
        "auto_learn_over_injected_access": 5,
    }
    base.update(over)
    return base


class TestEffectiveThreshold:
    def test_cold_start_unseen_domain_uses_base(self):
        """A domain with no state returns exactly the base threshold."""
        assert hz.effective_threshold({}, "backend", 0.5, _ctx()) == 0.5

    def test_offset_raises_threshold(self):
        state = {"backend": {"offset": 0.05, "accept": 0,
                             "detector_reject": 0, "actuation_reject": 0}}
        assert hz.effective_threshold(state, "backend", 0.5, _ctx()) == 0.55

    def test_volume_gate_blocks_reject_bias_below_min_samples(self):
        """Below min_samples, reject counters do not move the threshold."""
        state = {"backend": {"offset": 0.0, "accept": 1,
                             "detector_reject": 2, "actuation_reject": 0}}
        assert hz.effective_threshold(state, "backend", 0.5, _ctx()) == 0.5

    def test_reject_bias_applied_above_min_samples(self):
        """At/above min_samples, reject rate biases the threshold up."""
        # 10 samples, 5 rejects -> reject_rate 0.5 -> bias 0.15*0.5 = 0.075
        state = {"backend": {"offset": 0.0, "accept": 5,
                             "detector_reject": 3, "actuation_reject": 2}}
        assert hz.effective_threshold(state, "backend", 0.5, _ctx()) == 0.575

    def test_clamped_to_ceiling(self):
        state = {"backend": {"offset": 0.9, "accept": 0,
                             "detector_reject": 0, "actuation_reject": 0}}
        # base 0.5 + ceiling 0.2 = 0.7 hard cap
        assert hz.effective_threshold(state, "backend", 0.5, _ctx()) == 0.7


class TestFeedforward:
    def test_bump_adds_offset(self):
        state = {}
        hz.record_auto_log(state, "backend", _ctx())
        assert state["backend"]["offset"] == 0.02

    def test_bump_clamps_at_ceiling(self):
        state = {"backend": {"offset": 0.19, "accept": 0,
                             "detector_reject": 0, "actuation_reject": 0}}
        hz.record_auto_log(state, "backend", _ctx())
        assert state["backend"]["offset"] == 0.2  # not 0.21

    def test_sustained_flood_converges_to_ceiling(self):
        """bump/(1-decay) == ceiling; repeated bump+decay saturates there."""
        ctx = _ctx()
        state = {}
        for _ in range(200):
            hz.decay_all(state, ctx["adaptive_decay"])
            hz.record_auto_log(state, "backend", ctx)
        assert abs(state["backend"]["offset"] - 0.2) < 1e-3


class TestDecay:
    def test_decay_relaxes_offset(self):
        state = {"backend": {"offset": 0.1, "accept": 1,
                             "detector_reject": 0, "actuation_reject": 0}}
        hz.decay_all(state, 0.9)
        assert abs(state["backend"]["offset"] - 0.09) < 1e-6

    def test_decay_drops_fully_relaxed_empty_domain(self):
        """Tiny offset + no counters -> domain removed to keep file compact."""
        state = {"backend": {"offset": 1e-5, "accept": 0,
                             "detector_reject": 0, "actuation_reject": 0}}
        hz.decay_all(state, 0.9)
        assert "backend" not in state

    def test_decay_keeps_domain_with_counters(self):
        state = {"backend": {"offset": 1e-5, "accept": 3,
                             "detector_reject": 0, "actuation_reject": 0}}
        hz.decay_all(state, 0.9)
        assert state["backend"]["offset"] == 0.0
        assert state["backend"]["accept"] == 3


class TestOutcomeCounters:
    def test_added_is_accept(self):
        state = {}
        hz.record_outcome(state, "backend", "added")
        assert state["backend"]["accept"] == 1

    def test_duplicate_is_actuation_reject(self):
        state = {}
        hz.record_outcome(state, "backend", "duplicate")
        hz.record_outcome(state, "backend", "semantic_duplicate")
        assert state["backend"]["actuation_reject"] == 2

    def test_conflict_and_quarantine_are_detector_reject(self):
        state = {}
        hz.record_outcome(state, "backend", "conflict")
        hz.record_outcome(state, "backend", "quarantined")
        assert state["backend"]["detector_reject"] == 2


class TestUsefulnessRecompute:
    def test_high_access_sets_negative_offset(self):
        state = {}
        # mean_access 5 == access_ref -> usefulness 1.0 -> offset -gain (-0.1)
        stats = {"backend": {"n": 20, "mean_access": 5.0}}
        lowered = hz.recompute_usefulness(state, stats, _ctx())
        assert lowered == 1
        assert state["backend"]["usefulness_offset"] == -0.1

    def test_partial_access_scales_offset(self):
        state = {}
        # mean_access 2.5 / ref 5 = 0.5 -> offset -0.05
        stats = {"backend": {"n": 20, "mean_access": 2.5}}
        hz.recompute_usefulness(state, stats, _ctx())
        assert state["backend"]["usefulness_offset"] == -0.05

    def test_volume_gate_blocks_below_min_samples(self):
        state = {}
        stats = {"backend": {"n": 9, "mean_access": 100.0}}
        lowered = hz.recompute_usefulness(state, stats, _ctx())
        assert lowered == 0
        assert state["backend"]["usefulness_offset"] == 0.0

    def test_offset_clamped_to_floor(self):
        state = {}
        # huge access, but gain caps at floor 0.1
        stats = {"backend": {"n": 50, "mean_access": 1000.0}}
        hz.recompute_usefulness(state, stats, _ctx(adaptive_usefulness_gain=0.5))
        assert state["backend"]["usefulness_offset"] == -0.1  # floor, not -0.5

    def test_usefulness_lowers_effective_threshold(self):
        state = {"backend": {"offset": 0.0, "usefulness_offset": -0.08,
                             "accept": 0, "detector_reject": 0, "actuation_reject": 0}}
        assert abs(hz.effective_threshold(state, "backend", 0.5, _ctx()) - 0.42) < 1e-9

    def test_usefulness_offset_survives_decay(self):
        """usefulness_offset is non-decaying; only feedforward offset decays."""
        state = {"backend": {"offset": 0.1, "usefulness_offset": -0.08,
                             "accept": 1, "detector_reject": 0, "actuation_reject": 0}}
        hz.decay_all(state, 0.9)
        assert state["backend"]["usefulness_offset"] == -0.08
        assert abs(state["backend"]["offset"] - 0.09) < 1e-6

    def test_domain_with_only_usefulness_not_dropped(self):
        state = {"backend": {"offset": 1e-5, "usefulness_offset": -0.05,
                             "accept": 0, "detector_reject": 0, "actuation_reject": 0}}
        hz.decay_all(state, 0.9)
        assert "backend" in state
        assert state["backend"]["usefulness_offset"] == -0.05


class TestStateIO:
    def test_roundtrip(self, tmp_path):
        memory = tmp_path / "memory"
        memory.mkdir()
        paths = _make_paths(memory, tmp_path)
        state = {"backend": {"offset": 0.04, "accept": 2,
                             "detector_reject": 1, "actuation_reject": 0}}
        hz.save_state(paths, state)
        assert hz.load_state(paths) == state

    def test_missing_file_returns_empty(self, tmp_path):
        memory = tmp_path / "memory"
        memory.mkdir()
        paths = _make_paths(memory, tmp_path)
        assert hz.load_state(paths) == {}

    def test_corrupt_file_degrades_to_empty(self, tmp_path):
        memory = tmp_path / "memory"
        memory.mkdir()
        (memory / hz.STATE_FILENAME).write_text("{not valid json")
        paths = _make_paths(memory, tmp_path)
        assert hz.load_state(paths) == {}
