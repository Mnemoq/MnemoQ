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

## [1.20.6] - 2026-06-25

Initial public release.
