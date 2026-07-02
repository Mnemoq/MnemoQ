# Copyright (C) 2026 Mnemoq
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for R6 metrics honesty: latency percentiles, retrieval-quality
buckets, and hit-rate-by-domain. These exercise the pure aggregator
functions directly (no paths / no I/O)."""

from mnemoq.engine.metrics import (
    _percentiles,
    _retrieval_stats,
    _logging_stats,
)


class TestPercentiles:
    def test_known_list(self):
        # 1..100, nearest-rank: p50=ceil(.5*100)=50 -> xs[49]=50, etc.
        p = _percentiles(list(range(1, 101)))
        assert p["p50"] == 50
        assert p["p95"] == 95
        assert p["p99"] == 99
        assert p["count"] == 100

    def test_empty(self):
        p = _percentiles([])
        assert p == {"p50": 0.0, "p95": 0.0, "p99": 0.0, "count": 0}

    def test_single_value(self):
        p = _percentiles([42])
        assert p["p50"] == 42 and p["p95"] == 42 and p["p99"] == 42
        assert p["count"] == 1

    def test_drops_none_and_bad(self):
        # None and non-numeric are dropped, not fatal.
        p = _percentiles([None, "x", 10, 20, 30])
        assert p["count"] == 3
        assert p["p50"] == 20

    def test_all_none(self):
        p = _percentiles([None, None])
        assert p["count"] == 0
        assert p["p95"] == 0.0


class TestRetrievalStats:
    def _events(self):
        return [
            # hit, high score, ui
            {"warnings_returned": 2, "patterns_returned": 0, "top_score": 0.90,
             "query_domain": "ui", "latency_ms": 5},
            # hit, mid score, ui
            {"warnings_returned": 1, "patterns_returned": 1, "top_score": 0.60,
             "query_domain": "ui", "latency_ms": 15},
            # miss (no results), api
            {"warnings_returned": 0, "patterns_returned": 0, "top_score": 0.10,
             "query_domain": "api", "latency_ms": 25},
            # hit, low-ish score, api
            {"warnings_returned": 3, "patterns_returned": 0, "top_score": 0.30,
             "query_domain": "api", "latency_ms": 35},
        ]

    def test_latency_present(self):
        s = _retrieval_stats(self._events())
        assert s["latency"]["count"] == 4
        assert s["latency"]["p50"] > 0

    def test_score_buckets_sum_to_scored(self):
        s = _retrieval_stats(self._events())
        sb = s["score_buckets"]
        assert sb["n_scored"] == 4
        assert sum(sb["values"]) == sb["n_scored"]
        # 0.10 -> bucket0, 0.30 -> bucket1, 0.60 -> bucket2, 0.90 -> bucket3
        assert sb["values"] == [1, 1, 1, 1]

    def test_hit_rate_by_domain(self):
        s = _retrieval_stats(self._events())
        by_dom = {d["domain"]: d for d in s["hit_rate_by_domain"]}
        assert by_dom["ui"]["runs"] == 2 and by_dom["ui"]["hits"] == 2
        assert by_dom["ui"]["hit_rate"] == 1.0
        assert by_dom["api"]["runs"] == 2 and by_dom["api"]["hits"] == 1
        assert by_dom["api"]["hit_rate"] == 0.5

    def test_empty(self):
        assert _retrieval_stats([]) == {}


class TestLoggingStats:
    def test_latency_present(self):
        events = [
            {"outcome": "ADDED", "latency_ms": 2},
            {"outcome": "DUPLICATE", "latency_ms": 4},
        ]
        s = _logging_stats(events)
        assert s["latency"]["count"] == 2
        assert s["latency"]["p95"] >= s["latency"]["p50"]
