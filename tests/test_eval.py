"""Tests for the in-process grading harness (eval.py).

These exercise the engine directly (log_core + grade_fixtures) rather than via
subprocess, mirroring the direct-import style used for retrieval internals.
The subprocess/stdout-format tests for `--eval` live in
test_retrieval_integration.py::TestEvalHarness and must keep passing too.
"""
import json

from conftest import _make_ctx, _make_paths

_LEARNING = {
    "step": 1, "source_agent": "gm", "type": "bug_fix",
    "domain": "tooling", "components": ["CollisionSystem"],
    "files_touched": ["collision.py"], "trigger": "When AABB collision detected",
    "action": "ALWAYS use broadphase", "reason": "Broadphase is efficient",
    "importance": 8, "severity": "major",
}


def _log(paths, ctx, entry):
    from mnemoq.engine.handlers import log_core
    result = log_core(json.dumps(entry), paths, ctx)
    assert result["exit_code"] == 0


class TestAggregate:
    """Pure metric math: top-1/top-3, MRR, nDCG from per-fixture ranks."""

    def test_known_ranks(self):
        from mnemoq.engine.eval import _aggregate
        m = _aggregate([1, 3, 0])  # hit@1, hit@3, miss
        assert m["fixtures"] == 3
        assert m["top1_hits"] == 1
        assert m["top3_hits"] == 2
        assert abs(m["mrr"] - (1.0 + 1.0 / 3) / 3) < 1e-9
        # nDCG: (1/log2(2) + 1/log2(4)) / 3 = (1.0 + 0.5) / 3
        assert abs(m["ndcg"] - (1.0 + 0.5) / 3) < 1e-9

    def test_empty(self):
        from mnemoq.engine.eval import _aggregate
        m = _aggregate([])
        assert m["fixtures"] == 0
        assert m["mrr"] == 0.0
        assert m["ndcg"] == 0.0
        assert m["top1_rate"] == 0.0


class TestMatching:
    """Match-mode primitives."""

    def test_exact_substring(self):
        from mnemoq.engine.eval import _match_exact
        entry = {"trigger": "When AABB collision detected", "action": "use broadphase", "reason": "fast"}
        assert _match_exact("AABB collision", entry)
        assert not _match_exact("physics overlap", entry)

    def test_fuzzy_token_overlap(self):
        from mnemoq.engine.eval import _match_fuzzy
        entry = {"trigger": "When AABB collision detected", "action": "use broadphase", "reason": "fast"}
        # 2 of 3 expected tokens present -> >= 0.6
        assert _match_fuzzy("AABB collision overlap", entry, set(), 0.6)
        # only "broadphase" is shared of three tokens -> below 0.6
        assert not _match_fuzzy("physics body overlap", entry, set(), 0.6)

    def test_fuzzy_empty_expected(self):
        from mnemoq.engine.eval import _match_fuzzy
        assert not _match_fuzzy("", {"trigger": "x", "action": "", "reason": ""}, set(), 0.6)


class TestGradeFixtures:
    """End-to-end grading against a real (logged) corpus."""

    def test_fuzzy_hits_where_exact_misses(self, temp_project):
        paths = _make_paths(temp_project / "memory", temp_project)
        ctx = _make_ctx(score_threshold=0.01, decay_rate=0.99)
        _log(paths, ctx, _LEARNING)

        from mnemoq.engine.eval import grade_fixtures
        # Reworded expected: shares "AABB"+"collision" but is not a substring.
        fixtures = [{"step": 2, "components": "CollisionSystem", "domain": "tooling",
                     "expected_trigger": "AABB collision overlap"}]

        exact = grade_fixtures(paths, ctx, fixtures, mode="exact")
        fuzzy = grade_fixtures(paths, ctx, fixtures, mode="fuzzy")

        assert exact["top1_hits"] == 0
        assert exact["per_fixture"][0]["status"] == "MISS"
        assert fuzzy["top1_hits"] == 1
        assert fuzzy["per_fixture"][0]["status"] == "HIT@1"
        assert fuzzy["mrr"] == 1.0
        # per-fixture diagnostic carries the top returned entries
        assert fuzzy["per_fixture"][0]["top3"][0]["trigger"] == "When AABB collision detected"

    def test_semantic_falls_back_to_fuzzy_without_model(self, temp_project, monkeypatch):
        paths = _make_paths(temp_project / "memory", temp_project)
        ctx = _make_ctx(score_threshold=0.01, decay_rate=0.99)
        _log(paths, ctx, _LEARNING)

        import mnemoq.engine.eval as eval_mod
        # Simulate "no embedding model": every embedding call returns None.
        monkeypatch.setattr(eval_mod, "compute_embedding", lambda *a, **k: None)

        fixtures = [{"step": 2, "components": "CollisionSystem", "domain": "tooling",
                     "expected_trigger": "AABB collision overlap"}]
        m = eval_mod.grade_fixtures(paths, ctx, fixtures, mode="semantic")
        # No model -> per-comparison fuzzy fallback -> still a hit.
        assert m["top1_hits"] == 1


class TestRunEval:
    """Top-level entry point: return codes and JSON output."""

    def test_no_fixtures_returns_1(self, temp_project):
        from mnemoq.engine.eval import run_eval
        paths = _make_paths(temp_project / "memory", temp_project)
        assert run_eval(paths, _make_ctx()) == 1

    def test_invalid_match_mode_returns_1(self, temp_project):
        from mnemoq.engine.eval import run_eval
        paths = _make_paths(temp_project / "memory", temp_project)
        assert run_eval(paths, _make_ctx(), match="bogus") == 1

    def test_json_output_parses(self, temp_project, capsys):
        from mnemoq.engine.eval import run_eval
        paths = _make_paths(temp_project / "memory", temp_project)
        ctx = _make_ctx(score_threshold=0.01, decay_rate=0.99)
        _log(paths, ctx, _LEARNING)

        eval_dir = temp_project / "memory" / "eval"
        eval_dir.mkdir(parents=True, exist_ok=True)
        (eval_dir / "grading.jsonl").write_text(json.dumps({
            "step": 2, "components": "CollisionSystem", "domain": "tooling",
            "expected_trigger": "When AABB collision detected"}) + "\n")

        capsys.readouterr()  # clear any setup output
        rc = run_eval(paths, ctx, match="exact", as_json=True)
        out = capsys.readouterr().out

        assert rc == 0
        data = json.loads(out)
        assert data["fixtures"] == 1
        assert data["top1_hits"] == 1
        assert data["match_mode"] == "exact"
        assert "mrr" in data and "ndcg" in data
        assert data["per_fixture"][0]["status"] == "HIT@1"


class TestCompare:
    """A/B config comparison (--compare) using the real config loader."""

    _FIXTURE = {"step": 2, "components": "CollisionSystem", "domain": "tooling",
                "expected_trigger": "When AABB collision detected"}

    def _setup(self, temp_project):
        """Log one entry; write config A (high threshold -> filtered) and B (low -> kept)."""
        paths = _make_paths(temp_project / "memory", temp_project)
        _log(paths, _make_ctx(), _LEARNING)
        cfg_a = temp_project / "config_a.json"
        cfg_b = temp_project / "config_b.json"
        cfg_a.write_text(json.dumps({"tuning": {"score_threshold": 0.99}}))
        cfg_b.write_text(json.dumps({"tuning": {"score_threshold": 0.01}}))
        return paths, cfg_a, cfg_b

    def _write_fixture(self, temp_project):
        eval_dir = temp_project / "memory" / "eval"
        eval_dir.mkdir(parents=True, exist_ok=True)
        (eval_dir / "grading.jsonl").write_text(json.dumps(self._FIXTURE) + "\n")

    def test_ctx_from_config_overlays_tuning(self, temp_project):
        paths, cfg_a, cfg_b = self._setup(temp_project)
        from mnemoq.engine.eval import _ctx_from_config
        assert _ctx_from_config(paths, cfg_a)["score_threshold"] == 0.99
        assert _ctx_from_config(paths, cfg_b)["score_threshold"] == 0.01

    def test_compare_configs_delta(self, temp_project):
        paths, cfg_a, cfg_b = self._setup(temp_project)
        from mnemoq.engine.eval import _compare_configs
        ma, mb = _compare_configs(paths, [self._FIXTURE], "exact", str(cfg_a), str(cfg_b))
        # A's high threshold filters the entry out (miss); B keeps it (hit).
        assert ma["top1_hits"] == 0
        assert mb["top1_hits"] == 1

    def test_run_eval_compare_text(self, temp_project, capsys):
        paths, cfg_a, cfg_b = self._setup(temp_project)
        self._write_fixture(temp_project)
        from mnemoq.engine.eval import run_eval
        capsys.readouterr()
        rc = run_eval(paths, _make_ctx(), match="exact", compare=[str(cfg_a), str(cfg_b)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "A/B Comparison" in out
        assert "Top-1 hit rate" in out

    def test_run_eval_compare_json(self, temp_project, capsys):
        paths, cfg_a, cfg_b = self._setup(temp_project)
        self._write_fixture(temp_project)
        from mnemoq.engine.eval import run_eval
        capsys.readouterr()
        rc = run_eval(paths, _make_ctx(), match="exact", as_json=True,
                      compare=[str(cfg_a), str(cfg_b)])
        out = capsys.readouterr().out
        assert rc == 0
        data = json.loads(out)
        assert data["a"]["top1_hits"] == 0
        assert data["b"]["top1_hits"] == 1
        assert abs(data["delta"]["top1_rate"] - 1.0) < 1e-9
