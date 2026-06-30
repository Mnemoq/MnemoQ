"""Tests for handlers: metrics, schema migration, embeddings, semantic dedup."""
import importlib
import json
import subprocess
import sys

import pytest
from conftest import _make_ctx, _make_paths


class TestMetrics:
    """Test metrics logging and reporting."""

    def test_log_event_writes_jsonl(self, temp_project):
        """log_event appends a valid JSON line to metrics.jsonl."""
        paths = _make_paths(temp_project / "memory", temp_project)

        from mnemoq.engine.metrics import log_event, read_metrics

        log_event(paths, "retrieval", query_step=1, warnings_returned=2,
                  patterns_returned=1, top_score=0.85, latency_ms=3.2)
        log_event(paths, "log", outcome="ADDED", entry_type="bug_fix",
                  entry_domain="tooling", latency_ms=1.1)

        events = read_metrics(paths)
        assert len(events) == 2
        assert events[0]["event_type"] == "retrieval"
        assert events[0]["warnings_returned"] == 2
        assert events[0]["top_score"] == 0.85
        assert "ts" in events[0]
        assert "project_id" in events[0]
        assert events[1]["event_type"] == "log"
        assert events[1]["outcome"] == "ADDED"

    def test_log_event_never_raises(self, temp_project):
        """log_event silently ignores errors (best-effort)."""
        paths = _make_paths("/nonexistent/path/xyz", "/nonexistent")

        from mnemoq.engine.metrics import log_event
        log_event(paths, "retrieval", query_step=1)

    def test_read_metrics_filters_by_type(self, temp_project):
        """read_metrics filters by event_type."""
        paths = _make_paths(temp_project / "memory", temp_project)

        from mnemoq.engine.metrics import log_event, read_metrics

        log_event(paths, "retrieval", query_step=1)
        log_event(paths, "log", outcome="ADDED")
        log_event(paths, "retrieval", query_step=2)

        retrievals = read_metrics(paths, event_type="retrieval")
        assert len(retrievals) == 2
        assert all(e["event_type"] == "retrieval" for e in retrievals)

        logs = read_metrics(paths, event_type="log")
        assert len(logs) == 1
        assert logs[0]["outcome"] == "ADDED"

    def test_metrics_cli_summary(self, temp_project):
        """--metrics prints a summary report after events exist."""
        source_filter = ["-m", "mnemoq.cli"]

        learning = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["TestComponent"],
            "files_touched": ["test.py"], "trigger": "Metrics test",
            "action": "ALWAYS test metrics", "reason": "Testing",
            "importance": 7, "severity": "major"
        }
        learning_file = temp_project / "learning.json"
        learning_file.write_text(json.dumps(learning))

        subprocess.run(
            [sys.executable, *source_filter, "--log-file", str(learning_file)],
            cwd=temp_project, capture_output=True, text=True
        )

        result = subprocess.run(
            [sys.executable, *source_filter, "--metrics"],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0
        assert "METRICS" in result.stdout

    def test_metrics_cli_empty(self, temp_project):
        """--metrics handles no events gracefully."""
        source_filter = ["-m", "mnemoq.cli"]

        result = subprocess.run(
            [sys.executable, *source_filter, "--metrics"],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0
        assert "No metrics events" in result.stdout

    def test_metrics_cli_json_output(self, temp_project):
        """--metrics --metrics-json outputs valid JSON."""
        source_filter = ["-m", "mnemoq.cli"]

        learning = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["TestComponent"],
            "files_touched": ["test.py"], "trigger": "JSON metrics test",
            "action": "ALWAYS test json metrics", "reason": "Testing",
            "importance": 7, "severity": "major"
        }
        learning_file = temp_project / "learning.json"
        learning_file.write_text(json.dumps(learning))

        subprocess.run(
            [sys.executable, *source_filter, "--log-file", str(learning_file)],
            cwd=temp_project, capture_output=True, text=True
        )

        result = subprocess.run(
            [sys.executable, *source_filter, "--metrics", "--metrics-json"],
            cwd=temp_project, capture_output=True, text=True
        )

        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "summary" in data


class TestSchemaMigration:
    """Test schema versioning and migration runner."""

    def test_migration_v0_to_v1(self):
        """Unit test: v0 entries get migrated to v1 with correct fields."""
        from mnemoq.engine.migrate import CURRENT_SCHEMA_VERSION, migrate_entry

        v0_entry = {"step": 1, "type": "bug_fix", "trigger": "When test", "action": "ALWAYS test"}
        migrated = migrate_entry(dict(v0_entry))

        assert migrated["schema_version"] == CURRENT_SCHEMA_VERSION
        assert migrated["schema_version"] == 1
        assert migrated["embedding"] is None
        assert migrated["project_id"] is None
        assert migrated["origin_project"] is None
        assert migrated["contributing_projects"] == []

    def test_migrate_entry_noop_on_current(self):
        """migrate_entry is a no-op on already-current entries."""
        from mnemoq.engine.migrate import CURRENT_SCHEMA_VERSION, migrate_entry

        entry = {"schema_version": CURRENT_SCHEMA_VERSION, "step": 1, "embedding": [0.1, 0.2]}
        migrated = migrate_entry(dict(entry))

        assert migrated["schema_version"] == CURRENT_SCHEMA_VERSION
        assert migrated["embedding"] == [0.1, 0.2]

    def test_migrate_all_count(self):
        """migrate_all returns correct count of migrated entries."""
        from mnemoq.engine.migrate import CURRENT_SCHEMA_VERSION, migrate_all

        entries = [
            {"step": 1, "type": "bug_fix"},
            {"step": 2, "type": "bug_fix"},
            {"schema_version": CURRENT_SCHEMA_VERSION, "step": 3, "type": "bug_fix"},
        ]
        migrated, count = migrate_all(entries)

        assert count == 2
        assert all(e["schema_version"] == CURRENT_SCHEMA_VERSION for e in migrated)

    def test_read_learnings_auto_migrates(self, temp_project):
        """Integration: read_learnings auto-migrates v0 entries on read."""
        memory_dir = temp_project / "memory"
        learnings_path = memory_dir / "learnings.jsonl"

        v0_entry = {
            "step": 1, "source_agent": "gm", "type": "bug_fix", "domain": "tooling",
            "components": ["TestComp"], "files_touched": ["test.py"],
            "trigger": "When testing migration", "action": "ALWAYS test migration",
            "reason": "Testing auto-migrate", "importance": 7, "severity": "major"
        }
        learnings_path.write_text(json.dumps(v0_entry) + "\n")

        paths = _make_paths(memory_dir, temp_project)
        ctx = _make_ctx()
        from mnemoq.engine.handlers import stats_core
        result = stats_core(paths, ctx=ctx)
        assert result["exit_code"] == 0

        # Now run migration on disk
        from mnemoq.engine.migrate import run_migration
        run_migration(paths)

        lines = learnings_path.read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["schema_version"] == 1
        assert entry["embedding"] is None
        assert entry["project_id"] is None

    def test_handle_log_stamps_schema_version(self, temp_project):
        """Integration: --log stamps schema_version on new entries."""
        memory_dir = temp_project / "memory"
        paths = _make_paths(memory_dir, temp_project)
        ctx = _make_ctx()
        from mnemoq.engine.handlers import log_core

        learning = {
            "step": 1, "source_agent": "gm", "type": "bug_fix", "domain": "tooling",
            "components": ["TestComp"], "files_touched": ["test.py"],
            "trigger": "When testing stamp", "action": "ALWAYS test stamp",
            "reason": "Testing stamp", "importance": 7, "severity": "major"
        }

        result = log_core(json.dumps(learning), paths, ctx)
        assert result["exit_code"] == 0

        lines = (memory_dir / "learnings.jsonl").read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["schema_version"] == 1

    def test_migrate_schema_cli(self, temp_project, capsys):
        """Integration: --migrate-schema CLI flag works end-to-end."""
        memory_dir = temp_project / "memory"
        learnings_path = memory_dir / "learnings.jsonl"

        v0_entries = [
            {"step": 1, "source_agent": "gm", "type": "bug_fix", "domain": "tooling",
             "components": ["A"], "files_touched": ["a.py"], "trigger": "When a",
             "action": "ALWAYS a", "reason": "a", "importance": 5, "severity": "minor"},
            {"step": 2, "source_agent": "gm", "type": "bug_fix", "domain": "tooling",
             "components": ["B"], "files_touched": ["b.py"], "trigger": "When b",
             "action": "NEVER b", "reason": "b", "importance": 5, "severity": "minor"},
        ]
        with open(learnings_path, "w") as f:
            for e in v0_entries:
                f.write(json.dumps(e) + "\n")

        paths = _make_paths(memory_dir, temp_project)
        from mnemoq.engine.migrate import run_migration
        rc = run_migration(paths)
        assert rc == 0

        captured = capsys.readouterr()
        assert "SCHEMA MIGRATION COMPLETE" in captured.out
        assert "Migrated: 2" in captured.out

        lines = learnings_path.read_text().strip().split("\n")
        for line in lines:
            entry = json.loads(line)
            assert entry["schema_version"] == 1
            assert entry["embedding"] is None
            assert "contributing_projects" in entry


class TestEmbeddingRetrieval:
    """Test embedding-based retrieval functions and hybrid scoring."""

    def test_embedding_fallback(self, temp_project):
        """Retrieval works without sentence-transformers installed (embedder None, alpha=1.0)."""
        memory_dir = temp_project / "memory"
        paths = _make_paths(memory_dir, temp_project)
        ctx = _make_ctx()
        from mnemoq.engine.handlers import log_core
        from mnemoq.engine.retrieval import retrieve_core

        learning = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["CollisionSystem"],
            "files_touched": ["collision.py"], "trigger": "When AABB collision detected",
            "action": "ALWAYS use AABB broadphase", "reason": "AABB is efficient",
            "importance": 8, "severity": "major"
        }

        result = log_core(json.dumps(learning), paths, ctx)
        assert result["exit_code"] == 0

        result = retrieve_core(1, ["CollisionSystem"], [], "tooling", ctx, paths)
        assert result["total_entries"] >= 1

        lines = (memory_dir / "learnings.jsonl").read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert "embedding" in entry

    def test_embedding_encoding(self):
        """Base64 round-trip preserves vector values within float16 precision."""
        from mnemoq.engine.retrieval import decode_embedding, encode_embedding

        if importlib.util.find_spec("numpy") is None:
            pytest.skip("numpy not installed")

        original = [0.1, -0.2, 0.3, -0.4, 0.5, 0.0, 1.0, -1.0]
        encoded = encode_embedding(original)
        assert isinstance(encoded, str)
        decoded = decode_embedding(encoded)
        assert decoded is not None
        assert len(decoded) == len(original)
        for a, b in zip(original, decoded):
            assert abs(a - b) < 0.01

    def test_embedding_encoding_none(self):
        """encode_embedding(None) returns None, decode_embedding(None) returns None."""
        from mnemoq.engine.retrieval import decode_embedding, encode_embedding

        assert encode_embedding(None) is None
        assert decode_embedding(None) is None

    def test_embedding_encoding_plain_list_fallback(self):
        """encode_embedding falls back to plain list when numpy unavailable."""
        from mnemoq.engine.retrieval import decode_embedding

        original = [0.1, 0.2, 0.3]
        decoded = decode_embedding(original)
        assert decoded == original

    def test_cosine_similarity(self):
        """cosine_similarity: orthogonal -> 0.0, identical -> 1.0."""
        from mnemoq.engine.retrieval import cosine_similarity

        vec = [1.0, 0.0, 0.0]
        assert abs(cosine_similarity(vec, vec) - 1.0) < 1e-6

        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(cosine_similarity(a, b) - 0.0) < 1e-6

        assert cosine_similarity([], [1.0]) == 0.0
        assert cosine_similarity([1.0], [1.0, 2.0]) == 0.0
        assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_handle_log_stores_embedding(self, temp_project):
        """New entries get entry['embedding'] field (None if embedder unavailable)."""
        paths = _make_paths(temp_project / "memory", temp_project)
        ctx = _make_ctx()
        from mnemoq.engine.handlers import log_core

        learning = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["TestComp"],
            "files_touched": ["test.py"], "trigger": "When testing embedding storage",
            "action": "ALWAYS store embedding", "reason": "Embedding test",
            "importance": 7, "severity": "major"
        }

        result = log_core(json.dumps(learning), paths, ctx)
        assert result["exit_code"] == 0

        lines = (temp_project / "memory" / "learnings.jsonl").read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert "embedding" in entry

    def test_embedding_config_loaded(self, temp_project):
        """Custom embedding_alpha in config.json is loaded and applied without crash."""
        memory_dir = temp_project / "memory"
        config = {
            "project_name": "Test",
            "tuning": {
                "embedding_alpha": 0.8
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

    def test_handle_update_recomputes_embedding(self, temp_project):
        """handle_update re-computes embedding when trigger/action/reason changed, preserves when unchanged."""
        memory_dir = temp_project / "memory"
        paths = _make_paths(memory_dir, temp_project)
        ctx = _make_ctx()
        from mnemoq.engine.handlers import log_core, update_core

        learning = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["TestComp"],
            "files_touched": ["test.py"], "trigger": "When testing update",
            "action": "ALWAYS test update", "reason": "Update test",
            "importance": 7, "severity": "major"
        }

        result = log_core(json.dumps(learning), paths, ctx)
        assert result["exit_code"] == 0

        lines = (memory_dir / "learnings.jsonl").read_text().strip().split("\n")
        entry = json.loads(lines[0])
        ts = entry["ts"]

        updated = dict(entry)
        updated["trigger"] = "When testing updated trigger"
        updated.pop("ts", None)
        updated.pop("schema_version", None)
        updated.pop("embedding", None)

        result = update_core(ts, json.dumps(updated), paths, ctx)
        assert result["exit_code"] == 0

        lines = (memory_dir / "learnings.jsonl").read_text().strip().split("\n")
        updated_entry = json.loads(lines[0])
        assert "When testing updated trigger" in updated_entry["trigger"]
        assert "embedding" in updated_entry

        ts2 = updated_entry["ts"]

        updated2 = dict(updated_entry)
        updated2["importance"] = 9
        updated2.pop("ts", None)
        updated2.pop("schema_version", None)
        updated2.pop("embedding", None)

        result = update_core(ts2, json.dumps(updated2), paths, ctx)
        assert result["exit_code"] == 0

        lines = (memory_dir / "learnings.jsonl").read_text().strip().split("\n")
        final_entry = json.loads(lines[0])
        assert "embedding" in final_entry


class TestSemanticDedup:
    """Test embedding-based semantic dedup at log time."""

    def test_find_semantic_duplicate_no_embeddings(self):
        """find_semantic_duplicate returns (0.0, None) when no embeddings available."""
        from mnemoq.engine.retrieval import find_semantic_duplicate

        entry = {"domain": "tooling", "trigger": "When X", "action": "ALWAYS Y", "reason": "Z", "embedding": None}
        existing = [{"domain": "tooling", "trigger": "When X", "action": "ALWAYS Y", "reason": "Z", "embedding": None}]
        ctx = {"semantic_dedup_threshold": 0.85, "embedding_model": "fake-model", "embedding_cache_dir": "/tmp"}

        cos, match = find_semantic_duplicate(entry, existing, ctx)
        assert cos == 0.0
        assert match is None

    def test_find_semantic_duplicate_high_cosine(self):
        """find_semantic_duplicate detects match when embeddings are identical."""
        from mnemoq.engine.retrieval import encode_embedding, find_semantic_duplicate

        vec = [0.1, 0.2, 0.3, 0.4]
        emb = encode_embedding(vec)
        entry = {"domain": "tooling", "trigger": "When collision",
                 "action": "ALWAYS use AABB", "reason": "AABB is fast", "embedding": emb}
        existing = [{"domain": "tooling", "trigger": "When overlap",
                     "action": "NEVER skip AABB", "reason": "AABB detects overlap",
                     "embedding": emb, "resolved": False}]
        ctx = {"semantic_dedup_threshold": 0.85}

        cos, match = find_semantic_duplicate(entry, existing, ctx)
        assert cos >= 0.85
        assert match is not None
        assert match is existing[0]

    def test_find_semantic_duplicate_skips_resolved(self):
        """find_semantic_duplicate skips resolved entries."""
        from mnemoq.engine.retrieval import encode_embedding, find_semantic_duplicate

        vec = [0.1, 0.2, 0.3, 0.4]
        emb = encode_embedding(vec)
        entry = {"domain": "tooling", "trigger": "When X",
                 "action": "ALWAYS Y", "reason": "Z", "embedding": emb}
        existing = [{"domain": "tooling", "trigger": "When X",
                     "action": "ALWAYS Y", "reason": "Z",
                     "embedding": emb, "resolved": True}]
        ctx = {"semantic_dedup_threshold": 0.85}

        cos, match = find_semantic_duplicate(entry, existing, ctx)
        assert cos == 0.0
        assert match is None

    def test_find_semantic_duplicate_skips_different_domain(self):
        """find_semantic_duplicate only checks same-domain entries."""
        from mnemoq.engine.retrieval import encode_embedding, find_semantic_duplicate

        vec = [0.1, 0.2, 0.3, 0.4]
        emb = encode_embedding(vec)
        entry = {"domain": "tooling", "trigger": "When X",
                 "action": "ALWAYS Y", "reason": "Z", "embedding": emb}
        existing = [{"domain": "performance", "trigger": "When X",
                     "action": "ALWAYS Y", "reason": "Z",
                     "embedding": emb, "resolved": False}]
        ctx = {"semantic_dedup_threshold": 0.85}

        cos, match = find_semantic_duplicate(entry, existing, ctx)
        assert cos == 0.0
        assert match is None

    def test_find_semantic_duplicate_picks_highest(self):
        """find_semantic_duplicate returns the highest cosine match."""
        from mnemoq.engine.retrieval import encode_embedding, find_semantic_duplicate

        entry_vec = [1.0, 0.0, 0.0]
        close_vec = [0.99, 0.01, 0.0]
        far_vec = [0.5, 0.5, 0.5]
        entry = {"domain": "tooling", "trigger": "When X", "action": "ALWAYS Y",
                 "reason": "Z", "embedding": encode_embedding(entry_vec)}
        existing = [
            {"domain": "tooling", "trigger": "When A", "action": "ALWAYS B",
             "reason": "C", "embedding": encode_embedding(far_vec), "resolved": False},
            {"domain": "tooling", "trigger": "When D", "action": "ALWAYS E",
             "reason": "F", "embedding": encode_embedding(close_vec), "resolved": False},
        ]
        ctx = {"semantic_dedup_threshold": 0.85}

        cos, match = find_semantic_duplicate(entry, existing, ctx)
        assert match is existing[1]

    def test_provenance_fields_populated(self, temp_project):
        """Logging an entry should populate project_id, origin_project, contributing_projects, contributors."""
        paths = _make_paths(temp_project / "memory", temp_project)
        ctx = _make_ctx()
        from mnemoq.engine.handlers import log_core

        learning = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["TestComp"],
            "files_touched": ["test.py"], "trigger": "When testing provenance",
            "action": "ALWAYS test provenance", "reason": "Provenance test",
            "importance": 7, "severity": "major"
        }

        result = log_core(json.dumps(learning), paths, ctx)
        assert result["exit_code"] == 0

        lines = (temp_project / "memory" / "learnings.jsonl").read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert "project_id" in entry
        assert "origin_project" in entry
        assert "contributing_projects" in entry
        assert entry["contributing_projects"] == []
        assert "contributors" in entry
        assert "gm" in entry["contributors"]

    def test_semantic_dedup_graceful_without_model(self, temp_project):
        """Semantic dedup should gracefully fall back to Jaccard when no embedding model is available."""
        paths = _make_paths(temp_project / "memory", temp_project)
        ctx = _make_ctx()
        from mnemoq.engine.handlers import log_core

        learning1 = {
            "step": 1, "source_agent": "gm", "type": "bug_fix",
            "domain": "tooling", "components": ["CollisionSystem"],
            "files_touched": ["collision.py"], "trigger": "When AABB collision detected",
            "action": "ALWAYS use broadphase", "reason": "Broadphase is efficient",
            "importance": 8, "severity": "major"
        }

        result = log_core(json.dumps(learning1), paths, ctx)
        assert result["exit_code"] == 0

        learning2 = {
            "step": 2, "source_agent": "code-reviewer", "type": "bug_fix",
            "domain": "tooling", "components": ["CollisionSystem"],
            "files_touched": ["collision.py"], "trigger": "When bounding box overlap found",
            "action": "NEVER skip broadphase check", "reason": "Broadphase check is necessary for performance",
            "importance": 7, "severity": "major"
        }

        result = log_core(json.dumps(learning2), paths, ctx)
        assert result["exit_code"] == 0
        assert result["status"] in ("added", "duplicate", "conflict")
