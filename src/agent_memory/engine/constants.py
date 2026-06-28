"""Default constants for the memory engine.

All configurable defaults live here. filter.py builds a ctx dict
by copying DEFAULTS and overlaying load_config() results.
"""

import re

SESSION_EXPIRY_MINUTES = 10

DECAY_RATE = 0.995
SCORE_THRESHOLD = 0.15
COMPONENT_WEIGHT = 1.0
FILE_WEIGHT = 0.7
DOMAIN_WEIGHT = 0.4
NO_MATCH_WEIGHT = 0.1

MAX_WARNINGS = 5
MAX_PATTERNS = 15

BM25_K1 = 1.5   # term frequency saturation
BM25_B = 0.75   # document length normalization
RRF_K = 60      # reciprocal rank fusion constant

MINOR_RETENTION = 5
MAJOR_RETENTION = 20
ESCALATION_THRESHOLD = 30

SLEEP_CYCLE_DAYS = 1
SLEEP_CYCLE_QUARANTINE_THRESHOLD = 20

# Auto-learn detector thresholds.
#
# Lowered (2026-06) so detectors can fire on small corpora — at <100 entries
# the original values (fix_commit 3, reinforcement 5, access 10) are effectively
# unreachable, leaving auto-learn dormant. These are also mirrored in
# templates/config.json's tuning block; keep the two in sync (see the
# DEFAULTS-vs-template drift guard in tests).
#
# TODO: Consider scale-aware thresholds when the corpus exceeds ~500 entries.
# Scale-aware: effective_threshold = base * min(1.0, log10(n) / log10(500))
# This auto-lowers thresholds for small corpora and restores full thresholds
# as the corpus grows. Premature at current scale — revisit later.
AUTO_LEARN_ENABLED = True
AUTO_LEARN_GIT_SCAN_DEPTH = 20
AUTO_LEARN_FIX_COMMIT_THRESHOLD = 2
AUTO_LEARN_UNDER_RETRIEVED_ACCESS = 2
AUTO_LEARN_UNDER_RETRIEVED_REINFORCEMENT = 2
AUTO_LEARN_OVER_INJECTED_ACCESS = 5
AUTO_LEARN_OVER_INJECTED_REINFORCEMENT = 1
AUTO_LEARN_STALENESS_THRESHOLD = 500
AUTO_LEARN_MAX_FILES_PER_COMMIT = 5
AUTO_LEARN_MAX_PER_RUN = 20
AUTO_LEARN_RETRIEVAL_FAILURE_CAP = 100

EVALUATE_ENABLED = True
# Lowered 0.9 -> 0.5 (2026-06) so all detector signals auto-log: human
# correction (0.95), explicit remember (0.85), bug fixed (0.70), decision
# (0.60), workaround (0.55). At 0.9 only human corrections became entries.
# Configurable via config.json tuning.evaluate_auto_log_threshold.
EVALUATE_AUTO_LOG_THRESHOLD = 0.5
EVALUATE_MAX_PER_TURN = 3

AGENTS_CONFLICT_JACCARD_THRESHOLD = 0.1
AGENTS_CONFLICT_CONTAINMENT_THRESHOLD = 3

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_ALPHA = 0.5
EMBEDDING_CACHE_DIR = "~/.agent-memory/models/"
SEMANTIC_DEDUP_THRESHOLD = 0.85

RERANKER = "none"
RERANKER_TOP_N = 20
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-12-v2"
RERANKER_LLM_ENDPOINT = None
RERANKER_LLM_MODEL = None
API_KEY = None
VALID_RERANKERS = {"none", "cross-encoder", "llm-local"}

VALID_SOURCE_AGENTS = {"gm", "code-reviewer", "test-writer", "scout", "plan-reviewer",
                       "basic-reviewer", "meta-agent", "fuzzer", "docs-writer",
                       "security", "explorer", "refactorer", "system"}

# Universal schema constraints — not configurable per-project.
#
# Rationale: These define the fundamental structure of a learning entry.
# Making them configurable would allow project-specific types/severities
# but would break cross-project learning sharing.
#
# Decision: Keep hardcoded for now. Revisit if a concrete use case emerges
# where a project needs custom types (e.g., "feature_request", "documentation")
# or severities (e.g., "blocker", "trivial").
#
# Tradeoff: We value cross-project learning sharing over per-project flexibility.
# If all projects use the same schema, learnings can be shared between projects.
# If each project has custom schema, sharing breaks (a learning with type
# "feature_request" from Project A would fail validation in Project B).
VALID_TYPES = {"bug_fix", "optimization", "architectural_pattern", "meta_learning"}
VALID_DOMAINS = {"ui", "data", "tooling", "performance", "testing", "security",
                 "api", "backend", "frontend", "database", "deployment", "documentation"}
VALID_SEVERITIES = {"minor", "major", "critical"}
VALID_SCOPES = {"file", "module", "system"}
VALID_DEBT_LEVELS = {"proper", "workaround", "temporary"}
VALID_RETRIEVAL_ONLY_AGENTS = {"basic-reviewer"}

# Two-phase initialization:
# Phase 1 (module load): DOMAIN_MAPPINGS = None (default, use profile/hardcoded)
# Phase 2 (main() startup): load_config() may override via ctx dict
# This allows config.json to override the default at runtime.
DOMAIN_MAPPINGS = None  # None means "use profile.py's DEFAULT_DOMAIN_MAPPINGS"

STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "for", "on",
    "with", "at", "by", "from", "as", "into", "through", "during", "before",
    "after", "above", "below", "between", "and", "but", "or", "not", "so",
    "yet", "both", "either", "neither", "each", "every", "all", "any", "few",
    "more", "most", "other", "some", "such", "no", "only", "own", "same",
    "than", "too", "very", "just", "because", "until", "while", "if", "then",
    "else", "when", "where", "why", "how", "this", "that", "these", "those",
    "which", "who", "whom", "always", "never", "must", "required", "optional",
    "use", "using", "used", "make", "made", "get", "got", "set", "run",
    "new", "old", "first", "last", "long", "great", "little", "own",
    "its", "it", "he", "she", "they", "them", "his", "her", "their",
    "my", "your", "our", "we", "you", "i", "me", "him", "us",
}

TS_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# Flat dict of all defaults, keyed by the UPPERCASE names used in globals().update()
DEFAULTS = {
    "SESSION_EXPIRY_MINUTES": SESSION_EXPIRY_MINUTES,
    "DECAY_RATE": DECAY_RATE,
    "SCORE_THRESHOLD": SCORE_THRESHOLD,
    "COMPONENT_WEIGHT": COMPONENT_WEIGHT,
    "FILE_WEIGHT": FILE_WEIGHT,
    "DOMAIN_WEIGHT": DOMAIN_WEIGHT,
    "NO_MATCH_WEIGHT": NO_MATCH_WEIGHT,
    "MAX_WARNINGS": MAX_WARNINGS,
    "MAX_PATTERNS": MAX_PATTERNS,
    "BM25_K1": BM25_K1,
    "BM25_B": BM25_B,
    "RRF_K": RRF_K,
    "MINOR_RETENTION": MINOR_RETENTION,
    "MAJOR_RETENTION": MAJOR_RETENTION,
    "ESCALATION_THRESHOLD": ESCALATION_THRESHOLD,
    "VALID_SOURCE_AGENTS": VALID_SOURCE_AGENTS,
    "VALID_TYPES": VALID_TYPES,
    "VALID_DOMAINS": VALID_DOMAINS,
    "VALID_SEVERITIES": VALID_SEVERITIES,
    "VALID_SCOPES": VALID_SCOPES,
    "VALID_DEBT_LEVELS": VALID_DEBT_LEVELS,
    "VALID_RETRIEVAL_ONLY_AGENTS": VALID_RETRIEVAL_ONLY_AGENTS,
    "DOMAIN_MAPPINGS": DOMAIN_MAPPINGS,
    "STOP_WORDS": STOP_WORDS,
    "TS_PATTERN": TS_PATTERN,
    "EMBEDDING_MODEL": EMBEDDING_MODEL,
    "EMBEDDING_ALPHA": EMBEDDING_ALPHA,
    "EMBEDDING_CACHE_DIR": EMBEDDING_CACHE_DIR,
    "SEMANTIC_DEDUP_THRESHOLD": SEMANTIC_DEDUP_THRESHOLD,
    "RERANKER": RERANKER,
    "RERANKER_TOP_N": RERANKER_TOP_N,
    "RERANKER_MODEL": RERANKER_MODEL,
    "RERANKER_LLM_ENDPOINT": RERANKER_LLM_ENDPOINT,
    "RERANKER_LLM_MODEL": RERANKER_LLM_MODEL,
    "API_KEY": API_KEY,
    "VALID_RERANKERS": VALID_RERANKERS,
    "SLEEP_CYCLE_DAYS": SLEEP_CYCLE_DAYS,
    "SLEEP_CYCLE_QUARANTINE_THRESHOLD": SLEEP_CYCLE_QUARANTINE_THRESHOLD,
    "AUTO_LEARN_ENABLED": AUTO_LEARN_ENABLED,
    "AUTO_LEARN_GIT_SCAN_DEPTH": AUTO_LEARN_GIT_SCAN_DEPTH,
    "AUTO_LEARN_FIX_COMMIT_THRESHOLD": AUTO_LEARN_FIX_COMMIT_THRESHOLD,
    "AUTO_LEARN_UNDER_RETRIEVED_ACCESS": AUTO_LEARN_UNDER_RETRIEVED_ACCESS,
    "AUTO_LEARN_UNDER_RETRIEVED_REINFORCEMENT": AUTO_LEARN_UNDER_RETRIEVED_REINFORCEMENT,
    "AUTO_LEARN_OVER_INJECTED_ACCESS": AUTO_LEARN_OVER_INJECTED_ACCESS,
    "AUTO_LEARN_OVER_INJECTED_REINFORCEMENT": AUTO_LEARN_OVER_INJECTED_REINFORCEMENT,
    "AUTO_LEARN_STALENESS_THRESHOLD": AUTO_LEARN_STALENESS_THRESHOLD,
    "AUTO_LEARN_MAX_FILES_PER_COMMIT": AUTO_LEARN_MAX_FILES_PER_COMMIT,
    "AUTO_LEARN_MAX_PER_RUN": AUTO_LEARN_MAX_PER_RUN,
    "AUTO_LEARN_RETRIEVAL_FAILURE_CAP": AUTO_LEARN_RETRIEVAL_FAILURE_CAP,
    "EVALUATE_ENABLED": EVALUATE_ENABLED,
    "EVALUATE_AUTO_LOG_THRESHOLD": EVALUATE_AUTO_LOG_THRESHOLD,
    "EVALUATE_MAX_PER_TURN": EVALUATE_MAX_PER_TURN,
    "AGENTS_CONFLICT_JACCARD_THRESHOLD": AGENTS_CONFLICT_JACCARD_THRESHOLD,
    "AGENTS_CONFLICT_CONTAINMENT_THRESHOLD": AGENTS_CONFLICT_CONTAINMENT_THRESHOLD,
}
