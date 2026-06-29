# Changelog

All notable changes to MnemoQ will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Auto-learning system: three detection sources (meta-learnings, git history, retrieval-failure correlation) that self-improve the memory corpus without agent intervention
- `--auto-learn` CLI flag for on-demand auto-learning with verbose output
- `POST /api/auto-learn` HTTP API endpoint
- Auto-learning integrated into `--consolidate` (compact summary in report)
- `meta_learning` type and `system` source agent for auto-generated entries
- 11 `auto_learn_*` tuning parameters in `config.json`
- `check_staleness()` now accepts `ctx` for configurable staleness threshold
- `entry_components` and `entry_files_touched` added to log metrics events
- `--no-profile` CLI flag to skip developer profile loading during retrieval (deterministic output for baselines/CI)
- Per-prompt evaluation: `evaluate_core` with 5 heuristic detectors (human correction, explicit remember, bug fixed, decision, workaround) and threshold-gated auto-log via `log_core`
- `evaluate_prompt` MCP tool for programmatic per-turn evaluation
- `--evaluate` / `--evaluate-file` CLI flags for standalone prompt evaluation
- 3 `evaluate_*` tuning parameters in `config.json` (`evaluate_enabled`, `evaluate_auto_log_threshold`, `evaluate_max_per_turn`)
- Pre-commit debt scanning: `detect_debt_markers()` and `scan_staged_core()` for TODO/FIXME/HACK/XXX marker detection in staged files
- `--scan-staged` CLI flag for pre-commit debt scanning
- CI evaluation module (`engine/ci.py`) with `evaluate_ci_core()` for pytest JUnit XML parsing and `CI_WRITEBACK` modes (pr/artifact/commit)
- `--evaluate-ci` CLI flag for CI evaluation pipeline
- Hook templates: pre-commit (debt scan) and post-commit (auto-learn)
- CI workflow template: `mnemoq-learn.yml`
- `POST /api/agents-review` endpoint for reviewing and managing agent memory entries
- Dashboard project switcher for multi-project navigation
- `--dry-run` and `--confirm` safety guards for `generate_fakes.py`
- `/ship` meta-workflow with branch protection awareness
- `/fast-ship` workflow for quick safe edits
- `/commit` (professional-commit) workflow with Conventional Commit formatting
- Fake data generator dashboard tab with batch management (create, stream, stop, delete, toggle active)
- `sim_dialogue.py` script for dialogue simulation with evaluate_core integration
- Fake batch manifest system in `io.py` with active/inactive batch merging and legacy `fakes.jsonl` fallback
- PATCH and DELETE helpers in dashboard `api.js`
- Conversation capture module (`engine/capture.py`): three-tier extraction (online LLM, offline LLM, heuristic) that converts raw conversation text into structured memories via `evaluate_core`
- `capture_interaction` MCP tool for programmatic conversation capture
- `--capture-file` CLI flag for batch conversation capture
- 4 `capture_*` tuning parameters in `config.json` (`capture_enabled`, `capture_always_log`, `capture_max_summaries`, `capture_mode`)
- Capture heuristic improvements: negation-aware outcome detection, sentence-boundary gist extraction, deduplication, outcome-weighted summary ranking, and signal-gated none-outcome logging
- `capture_none_log_requires_signal` tuning parameter in `config.json`

### Fixed
- Hooks now return an error when a foreign hook refuses overwrite (#15)
- `--hooks-path` relative paths now anchor to repo root via `git rev-parse`
- Ruff lint errors (I001 import sorting, E741 ambiguous variable name)
- `generate_fakes.py` updated for `agent_memory` package; removed legacy `original_filter.py` and `test.json`

### Changed
- Bumped dependencies: httpx >=0.28.1, ruff >=0.15.20, setuptools >=82.0.1, fastapi >=0.138.1, pytest-xdist >=3.8.0

## [1.20.6] - 2026-06-25

Initial public release.
