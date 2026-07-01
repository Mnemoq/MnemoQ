"""Tests for retrieval integration: BM25, RRF, reranker, eval harness."""
import json
import subprocess
import sys

import pytest
from conftest import _load_config, _make_ctx, _make_paths


class TestBM25Score:
    """Test BM25 scoring function directly."""

    @pytest.mark.smoke
    def test_bm25_rare_term_scores_higher(self):
        """Rare matching term should score higher than common matching term."""
        from mnemoq.engine.retrieval import _compute_corpus_stats, _tokenize, bm25_score

        stop_words = set()
        entries = []
        for i in range(5):
            entries.append({"trigger": f"When physics test {i}",
                            "action": "ALWAYS physics", "reason": "physics common"})
        entries.append({"trigger": "When AABB collision", "action": "NEVER AABB", "reason": "AABB rare"})

        doc_freqs, total_docs, avg_doc_len = _compute_corpus_stats(entries, stop_words)

        query_tokens = _tokenize("physics AABB", stop_words)
        doc_physics = _tokenize("physics common", stop_words)
        doc_aabb = _tokenize("AABB collision rare", stop_words)

        score_physics = bm25_score(query_tokens, doc_physics, doc_freqs,
                                    total_docs, avg_doc_len, 1.5, 0.75)
        score_aabb = bm25_score(query_tokens, doc_aabb, doc_freqs,
                                total_docs, avg_doc_len, 1.5, 0.75)

        assert score_aabb > score_physics, (
            f"Rare term AABB ({score_aabb}) should score higher than "
            f"common term physics ({score_physics})")

    @pytest.mark.smoke
    def test_bm25_no_match_scores_zero(self):
        """Doc with no matching query terms should score 0.0."""
        from mnemoq.engine.retrieval import _compute_corpus_stats, _tokenize, bm25_score

        stop_words = set()
        entries = [
            {"trigger": "When physics test", "action": "ALWAYS physics", "reason": "physics"},
            {"trigger": "When AABB collision", "action": "NEVER AABB", "reason": "AABB"},
        ]
        doc_freqs, total_docs, avg_doc_len = _compute_corpus_stats(entries, stop_words)

        query_tokens = _tokenize("physics AABB", stop_words)
        doc_no_match = _tokenize("completely unrelated words", stop_words)

        score = bm25_score(query_tokens, doc_no_match, doc_freqs, total_docs, avg_doc_len, 1.5, 0.75)
        assert score == 0.0

    @pytest.mark.smoke
    def test_bm25_empty_corpus(self):
        """Empty corpus should return 0.0 without crashing."""
        from mnemoq.engine.retrieval import bm25_score

        score = bm25_score(["test"], ["test"], {}, 0, 0.0, 1.5, 0.75)
        assert score == 0.0


class TestRRFFusion:
    """Test RRF fusion in retrieval integration."""

    def test_rrf_fusion_integration(self, temp_project):
        """RRF should rank entries that match both channels higher."""
        paths = _make_paths(temp_project / "memory", temp_project)
        ctx = _make_ctx()
        from mnemoq.engine.handlers import log_core
        from mnemoq.engine.retrieval import retrieve_core

        entry_a = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["CollisionSystem"],
            "files_touched": ["collision.py"], "trigger": "When AABB collision detected",
            "action": "ALWAYS use AABB broadphase", "reason": "AABB is efficient for collision",
            "importance": 8, "severity": "major"
        }
        entry_b = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["CollisionSystem"],
            "files_touched": ["collision.py"], "trigger": "When rendering sprites",
            "action": "ALWAYS batch draw calls", "reason": "Batching improves performance",
            "importance": 7, "severity": "major"
        }
        entry_c = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["RenderEngine"],
            "files_touched": ["render.py"], "trigger": "When AABB bounding boxes overlap",
            "action": "NEVER skip AABB check", "reason": "AABB overlap detection is critical",
            "importance": 9, "severity": "major"
        }

        for entry in [entry_a, entry_b, entry_c]:
            result = log_core(json.dumps(entry), paths, ctx)
            assert result["exit_code"] == 0

        result = retrieve_core(1, ["CollisionSystem"], [], "tooling", ctx, paths)

        # Entry A and B should be in results (match components)
        triggers = [e["trigger"] for e in result["patterns"]]
        assert "When AABB collision detected" in triggers
        assert "When rendering sprites" in triggers
        # Entry A should rank before Entry B (RRF ranks it higher — AABB match in both channels)
        a_idx = triggers.index("When AABB collision detected")
        b_idx = triggers.index("When rendering sprites")
        assert a_idx < b_idx, "Entry A (AABB) should rank before Entry B (batch)"

    def test_rrf_formula_sanity(self):
        """Sanity check the RRF formula math for a single-candidate scenario."""
        rrf_k = 60
        expected = 2.0 / (rrf_k + 1)
        assert abs(expected - 0.032786) < 0.001

    def test_bm25_config_loaded(self, temp_project):
        """Custom BM25 config values should be loaded without crash."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test",
            "tuning": {
                "bm25_k1": 2.0,
                "bm25_b": 0.5,
                "rrf_k": 40
            }
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))

        paths = _make_paths(memory_dir, temp_project)
        ctx = _make_ctx(config_path=str(config_path))
        from mnemoq.engine.handlers import log_core
        from mnemoq.engine.retrieval import retrieve_core

        learning = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["TestComp"],
            "files_touched": ["test.py"], "trigger": "When testing config",
            "action": "ALWAYS test config", "reason": "Config test",
            "importance": 7, "severity": "major"
        }

        result = log_core(json.dumps(learning), paths, ctx)
        assert result["exit_code"] == 0

        result = retrieve_core(1, ["TestComp"], [], "tooling", ctx, paths)
        assert result["total_entries"] >= 1

        triggers = [e["trigger"] for e in result["patterns"]]
        assert "When testing config" in triggers


class TestReranker:
    """Test the optional reranking pass (Phase 5).

    These tests use direct imports to mock internal singletons (_ce_cache,
    _PROBE_CACHE, _call_llm) that cannot be exercised via CLI subprocess.
    This is an intentional deviation from the AGENTS.md convention of
    CLI-only integration testing, justified by the need to test graceful
    fallback paths without real model downloads or running LLM servers.
    """

    def test_rerank_none_passthrough(self):
        """rerank_none returns candidates unchanged."""
        from mnemoq.engine.reranker import rerank_none
        candidates = [(0.9, 0.8, 0.7, {"severity": "critical"}), (0.5, 0.4, 0.3, {"severity": "minor"})]
        result, active = rerank_none("query", candidates, {})
        assert result is candidates
        assert active is True

    def test_rerank_dispatch_none(self):
        """rerank() with mode 'none' returns candidates unchanged."""
        from mnemoq.engine.reranker import rerank
        candidates = [(0.9, 0.8, 0.7, {"severity": "critical"})]
        result, active = rerank("query", candidates, {"reranker": "none"})
        assert result is candidates
        assert active is True

    def test_rerank_cross_encoder_mock(self):
        """Cross-encoder reranking with mocked model."""
        from mnemoq.engine import reranker

        class MockCrossEncoder:
            def predict(self, pairs):
                return [5.0, 9.0, 1.0]

        reranker._ce_cache["test-model"] = MockCrossEncoder()

        candidates = [
            (0.9, 0.8, 0.7, {"severity": "critical", "trigger": "A", "action": "B", "reason": "C"}),
            (0.5, 0.4, 0.3, {"severity": "minor", "trigger": "D", "action": "E", "reason": "F"}),
            (0.3, 0.2, 0.1, {"severity": "minor", "trigger": "G", "action": "H", "reason": "I"}),
        ]
        ctx = {"reranker_model": "test-model", "embedding_cache_dir": "/tmp"}
        result, active = reranker.rerank_cross_encoder("query", candidates, ctx)

        assert active is True
        assert result[0][3]["trigger"] == "D"
        assert result[1][3]["trigger"] == "A"
        assert result[2][3]["trigger"] == "G"

        del reranker._ce_cache["test-model"]

    def test_rerank_cross_encoder_fallback(self):
        """Cross-encoder falls back to passthrough when model unavailable."""
        from mnemoq.engine import reranker

        reranker._ce_cache["unavailable-model"] = None

        candidates = [
            (0.9, 0.8, 0.7, {"severity": "critical", "trigger": "A", "action": "B", "reason": "C"}),
            (0.5, 0.4, 0.3, {"severity": "minor", "trigger": "D", "action": "E", "reason": "F"}),
        ]
        ctx = {"reranker_model": "unavailable-model", "embedding_cache_dir": "/tmp"}
        result, active = reranker.rerank_cross_encoder("query", candidates, ctx)

        assert active is False
        assert result is candidates

        del reranker._ce_cache["unavailable-model"]

    def test_rerank_llm_local_malformed_response(self):
        """LLM-local returns passthrough when response has fewer numbers than candidates."""
        from mnemoq.engine import reranker

        reranker._PROBE_CACHE = {"endpoint": None, "checked": False}

        original_call = reranker._call_llm
        reranker._call_llm = lambda endpoint, model, prompt: "I think the first one is good"

        reranker._PROBE_CACHE = {"endpoint": "http://localhost:11434", "checked": True}

        candidates = [
            (0.9, 0.8, 0.7, {"severity": "critical", "trigger": "A", "action": "B", "reason": "C"}),
            (0.5, 0.4, 0.3, {"severity": "minor", "trigger": "D", "action": "E", "reason": "F"}),
            (0.3, 0.2, 0.1, {"severity": "minor", "trigger": "G", "action": "H", "reason": "I"}),
        ]
        ctx = {"reranker_llm_endpoint": "http://localhost:11434", "reranker_llm_model": "test"}
        result, active = reranker.rerank_llm_local("query", candidates, ctx)

        assert active is False
        assert result is candidates

        reranker._call_llm = original_call
        reranker._PROBE_CACHE = {"endpoint": None, "checked": False}

    def test_rerank_llm_local_no_endpoint(self):
        """LLM-local falls back when no endpoint is found."""
        from mnemoq.engine import reranker

        reranker._PROBE_CACHE = {"endpoint": None, "checked": True}

        candidates = [(0.9, 0.8, 0.7, {"severity": "critical", "trigger": "A", "action": "B", "reason": "C"})]
        ctx = {"reranker_llm_endpoint": None}
        result, active = reranker.rerank_llm_local("query", candidates, ctx)

        assert active is False
        assert result is candidates

        reranker._PROBE_CACHE = {"endpoint": None, "checked": False}

    def test_rerank_llm_local_mock_success(self):
        """LLM-local reranks successfully with mocked response."""
        from mnemoq.engine import reranker

        reranker._PROBE_CACHE = {"endpoint": "http://localhost:11434", "checked": True}

        original_call = reranker._call_llm
        reranker._call_llm = lambda endpoint, model, prompt: "3 9 1"

        candidates = [
            (0.9, 0.8, 0.7, {"severity": "critical", "trigger": "A", "action": "B", "reason": "C"}),
            (0.5, 0.4, 0.3, {"severity": "minor", "trigger": "D", "action": "E", "reason": "F"}),
            (0.3, 0.2, 0.1, {"severity": "minor", "trigger": "G", "action": "H", "reason": "I"}),
        ]
        ctx = {"reranker_llm_endpoint": "http://localhost:11434", "reranker_llm_model": "test"}
        result, active = reranker.rerank_llm_local("query", candidates, ctx)

        assert active is True
        assert result[0][3]["trigger"] == "D"
        assert result[1][3]["trigger"] == "A"
        assert result[2][3]["trigger"] == "G"

        reranker._call_llm = original_call
        reranker._PROBE_CACHE = {"endpoint": None, "checked": False}

    def test_rerank_unknown_mode(self):
        """Unknown reranker mode falls back to passthrough."""
        from mnemoq.engine import reranker
        candidates = [(0.9, 0.8, 0.7, {"severity": "critical"})]
        result, active = reranker.rerank("query", candidates, {"reranker": "bogus"})
        assert active is False
        assert result is candidates

    def test_parse_scores_sufficient(self):
        """_parse_scores returns floats when enough numbers found."""
        from mnemoq.engine.reranker import _parse_scores
        scores = _parse_scores("7.5 3 9.0 1", 4)
        assert scores == [7.5, 3.0, 9.0, 1.0]

    def test_parse_scores_insufficient(self):
        """_parse_scores returns None when too few numbers found."""
        from mnemoq.engine.reranker import _parse_scores
        scores = _parse_scores("only 2 numbers here", 5)
        assert scores is None

    def test_probe_llm_endpoint_configured(self):
        """_probe_llm_endpoint returns configured endpoint without probing."""
        from mnemoq.engine.reranker import _probe_llm_endpoint
        result = _probe_llm_endpoint("http://custom:8080")
        assert result == "http://custom:8080"

    def test_config_reranker_invalid_value(self, temp_project):
        """Invalid reranker value raises ValueError."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test",
            "reranker": "bogus",
            "tuning": {}
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))

        with pytest.raises(ValueError, match="must be one of"):
            _load_config(config_path)

    def test_config_reranker_null_llm_endpoint(self, temp_project):
        """Null reranker_llm_endpoint passes through as None."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test",
            "reranker": "llm-local",
            "reranker_llm_endpoint": None,
            "reranker_llm_model": None,
            "tuning": {}
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))

        config = _load_config(config_path)
        assert config.get("RERANKER_LLM_ENDPOINT") is None

    def test_config_reranker_top_n_invalid(self, temp_project):
        """reranker_top_n < 1 raises ValueError."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test",
            "reranker": "none",
            "reranker_top_n": 0,
            "tuning": {}
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))

        with pytest.raises(ValueError, match="reranker_top_n"):
            _load_config(config_path)

    def test_retrieval_none_mode_regression(self, temp_project):
        """Retrieval with reranker: 'none' produces same output as before (regression)."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test",
            "reranker": "none",
            "tuning": {"decay_rate": 0.99, "score_threshold": 0.01}
        }
        config_path = memory_dir / "config.json"
        config_path.write_text(json.dumps(config))

        paths = _make_paths(memory_dir, temp_project)
        ctx = _make_ctx(config_path=str(config_path))
        from mnemoq.engine.handlers import log_core
        from mnemoq.engine.retrieval import retrieve_core

        learning = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["TestComp"],
            "files_touched": ["test.py"], "trigger": "When testing",
            "action": "ALWAYS verify", "reason": "Safety first",
            "importance": 7, "severity": "major"
        }

        result = log_core(json.dumps(learning), paths, ctx)
        assert result["exit_code"] == 0

        result = retrieve_core(2, ["TestComp"], [], "tooling", ctx, paths)
        assert result["total_entries"] >= 1

        triggers = [e["trigger"] for e in result["patterns"]]
        assert "When testing" in triggers


class TestEvalHarness:
    """Test the grading harness (--eval flag).

    These tests assert on CLI stdout formatting (Top-1 hit rate, HIT@, MISS)
    so they stay as subprocess tests using `python -m mnemoq.cli`.
    """

    @staticmethod
    def _setup_learning_and_fixture(temp_project, fixture_trigger, learning=None):
        """Shared setup: write config, log a learning, create eval fixture."""
        memory_dir = temp_project / "memory"
        (memory_dir / "config.json").write_text(json.dumps({
            "project_name": "Test",
            "tuning": {"decay_rate": 0.99, "score_threshold": 0.01}
        }))

        if learning is None:
            learning = {
                "step": 1, "source_agent": "gm", "type": "bug_fix",
                "domain": "tooling", "components": ["CollisionSystem"],
                "files_touched": ["collision.py"], "trigger": "When AABB collision detected",
                "action": "ALWAYS use broadphase", "reason": "Broadphase is efficient",
                "importance": 8, "severity": "major"
            }
        f = temp_project / "learning.json"
        f.write_text(json.dumps(learning))
        subprocess.run(
            [sys.executable, "-m", "mnemoq.cli", "--log-file", str(f)],
            cwd=temp_project, capture_output=True, text=True
        )

        eval_dir = memory_dir / "eval"
        eval_dir.mkdir(exist_ok=True)
        fixture = {"step": 2, "components": "CollisionSystem", "domain": "tooling",
                    "expected_trigger": fixture_trigger}
        (eval_dir / "grading.jsonl").write_text(json.dumps(fixture) + "\n")

    def test_eval_no_fixture(self, temp_project):
        """--eval with no fixture file returns 1 with helpful message."""
        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.cli", "--eval"],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode == 1
        assert "No grading fixtures found" in result.stdout

    def test_eval_with_fixture_hit(self, temp_project):
        """--eval reports a hit when expected trigger appears in results."""
        self._setup_learning_and_fixture(temp_project, "When AABB collision detected")

        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.cli", "--eval"],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "Grading Harness Results" in result.stdout
        assert "Top-1 hit rate: 1/1" in result.stdout
        assert "HIT@" in result.stdout

    def test_eval_with_fixture_miss(self, temp_project):
        """--eval reports a miss when expected trigger is absent."""
        self._setup_learning_and_fixture(temp_project, "When physics body overlaps")

        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.cli", "--eval"],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "Top-1 hit rate: 0/1" in result.stdout
        assert "MISS" in result.stdout

    def test_eval_multiple_fixtures(self, temp_project):
        """--eval handles multiple fixtures with mixed hit/miss."""
        memory_dir = temp_project / "memory"
        (memory_dir / "config.json").write_text(json.dumps({
            "project_name": "Test",
            "tuning": {"decay_rate": 0.99, "score_threshold": 0.01}
        }))

        for fname, learning in [
            ("learning1.json", {
                "step": 1, "source_agent": "gm", "type": "bug_fix",
                "domain": "tooling", "components": ["CollisionSystem"],
                "files_touched": ["collision.py"], "trigger": "When AABB collision detected",
                "action": "ALWAYS use broadphase", "reason": "Broadphase is efficient",
                "importance": 8, "severity": "major"
            }),
            ("learning2.json", {
                "step": 1, "source_agent": "gm", "type": "optimization",
                "domain": "performance", "components": ["RenderLoop"],
                "files_touched": ["render.py"], "trigger": "When frame rate drops below 60",
                "action": "ALWAYS batch draw calls", "reason": "Reduces GPU overhead",
                "importance": 7, "severity": "major"
            }),
        ]:
            f = temp_project / fname
            f.write_text(json.dumps(learning))
            subprocess.run(
                [sys.executable, "-m", "mnemoq.cli", "--log-file", str(f)],
                cwd=temp_project, capture_output=True, text=True
            )

        eval_dir = memory_dir / "eval"
        eval_dir.mkdir(exist_ok=True)
        fixture1 = {"step": 2, "components": "CollisionSystem", "domain": "tooling",
                     "expected_trigger": "When AABB collision detected"}
        fixture2 = {"step": 2, "components": "CollisionSystem", "domain": "tooling",
                     "expected_trigger": "Nonexistent trigger text"}
        (eval_dir / "grading.jsonl").write_text(
            json.dumps(fixture1) + "\n" + json.dumps(fixture2) + "\n"
        )

        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.cli", "--eval"],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "Fixtures: 2" in result.stdout
        assert "Top-1 hit rate: 1/2" in result.stdout

    def test_eval_skips_comments_and_blanks(self, temp_project):
        """--eval skips comment lines and blank lines in fixture file."""
        memory_dir = temp_project / "memory"
        eval_dir = memory_dir / "eval"
        eval_dir.mkdir(exist_ok=True)
        (eval_dir / "grading.jsonl").write_text(
            "# This is a comment\n"
            "\n"
            '{"step": 1, "components": "TestComp", "domain": "tooling", "expected_trigger": "test"}\n'
        )

        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.cli", "--eval"],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "Fixtures: 1" in result.stdout

    def test_eval_mutual_exclusion(self, temp_project):
        """--eval cannot be combined with --stats."""
        result = subprocess.run(
            [sys.executable, "-m", "mnemoq.cli", "--eval", "--stats"],
            cwd=temp_project, capture_output=True, text=True
        )
        assert result.returncode != 0
