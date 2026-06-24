# Contributing to MnemoQ

Thank you for your interest in contributing! This document covers everything you need to get started.

## How to Contribute

1. **Fork** the repository
2. **Branch** from `main` (e.g., `git checkout -b fix/my-bugfix`)
3. **Commit** your changes with clear messages
4. **Open a PR** against `main`
5. **Review** — maintainers will review and merge

## CLA Acceptance

Submitting a pull request constitutes acceptance of the [Contributor License Agreement](CLA.md). You do not need to sign anything separately — the PR itself is the signal.

## Development Setup

```bash
git clone https://github.com/Mnemoq/MnemoQ.git
cd MnemoQ
pip install -e ".[dev]"
pytest
```

## Code Style

- **Linter**: [ruff](https://docs.astral.sh/ruff/) — line-length 120, target py310
- Run `ruff check .` before committing
- Follow PEP 8 where ruff doesn't enforce

## Testing

```bash
python -m pytest tests/
```

All tests must pass before a PR can be merged. Add tests for new functionality.

## Open-Core Boundary

MnemoQ is an open-core project. The AGPL-licensed core lives in this repo. A proprietary Pro tier (cloud sync, multi-tenant, billing) lives in a separate private repo.

See [docs/open-core-architecture.md](docs/open-core-architecture.md) for the full boundary description and module classification.

## Issue Guidelines

- **Bug reports**: Include reproduction steps, expected vs actual behavior, and your environment (OS, Python version, `mnemoq --version` output).
- **Feature requests**: Describe the use case and proposed solution. Check existing issues first to avoid duplicates.
