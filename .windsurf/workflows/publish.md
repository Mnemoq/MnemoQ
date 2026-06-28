---
description: Bumps VERSION, commits, tags, and pushes from main to trigger the PyPI publish workflow.
---

## Steps

### 0. Git session lock

Acquire the git session lock before proceeding. See `_git-lock.md` for the full lock-check snippet.

Run the lock-check command. If `LOCKED:`, stop and inform the user. If `STALE:`, override. If `FREE`, acquire:

```powershell
"$([DateTime]::UtcNow.ToString('o')) /publish" | Set-Content ".git/.windsurf-git-lock"
```

---

### 1. Run tests

Ensure your project venv is active, then install dev dependencies:
```bash
pip install -e ".[dev]"
```

Then run the test suite:
```bash
python -m pytest tests/
```

Abort if any tests fail. Do not publish a broken release — PyPI does not allow re-uploading the same version.

---

### 2. Read current version

```bash
cat VERSION
```

Note the current version number (e.g. `1.20.6`).

---

### 3. Propose the next version

Ask the user which version to bump to:
- **Patch** (e.g. `1.20.6` → `1.20.7`) — bug fixes, minor changes
- **Minor** (e.g. `1.20.6` → `1.21.0`) — new features, backward compatible
- **Major** (e.g. `1.20.6` → `2.0.0`) — breaking changes

Present the proposed version and ask for confirmation or a custom version string.

---

### 4. Verify on main with clean tree

```bash
git fetch origin
git branch --show-current
git status --short
git rev-list --left-right --count origin/main...main
```

The `rev-list` output is `<behind>\t<ahead>`.

- **Must be on `main`.** If not, abort and tell the user to switch to `main` and merge any feature branches first.
- If there are uncommitted changes, warn the user and ask whether to stash, commit, or abort.
- If `main` is behind `origin/main` (first number > 0), rebase before proceeding:
  ```bash
  git pull --rebase origin main
  ```
  Abort if the rebase fails. Re-check `git status --short` after rebase.
  If the rebase brought in new commits, re-run Step 1 (tests) before proceeding.
- If `main` is ahead of `origin/main` (second number > 0), warn the user — unpushed commits will go out with the release tag. Ask whether to proceed or push first.

---

### 5. Verify tag doesn't already exist

```bash
git tag --list "v<NEW_VERSION>"
```

Abort if the tag already exists.

---

### 6. Bump VERSION file

Write the new version string to `VERSION` (single trailing newline, no extra blank lines).

---

### 7. Update CHANGELOG.md

In `CHANGELOG.md`, match the exact heading `## [Unreleased]` (case-sensitive, with brackets). If not found, **abort** — do not guess or fuzzy-match.

Rename it to `## [NEW_VERSION] - <UTC_DATE>` where `<UTC_DATE>` is obtained from (cross-platform):
```bash
python -c "from datetime import datetime, timezone; print(datetime.now(timezone.utc).strftime('%Y-%m-%d'))"
```

Then insert a fresh `## [Unreleased]` section above it with `### Added`, `### Changed`, `### Fixed` subsections (Keep a Changelog format). Preserve existing released entries as-is — only apply the subsection template to the new `[Unreleased]` section.

Example:
```markdown
## [Unreleased]

### Added

### Changed

### Fixed

## [1.20.7] - 2026-06-27

### Added
- `--no-profile` CLI flag to skip developer profile loading during retrieval
```

**Empty changelog check:** The `[Unreleased]` section is considered empty if, between the `## [Unreleased]` header and the next `## ` heading, there are no non-whitespace lines excluding the subsection headers (`### Added`, `### Changed`, `### Fixed`) and HTML comments. If empty, warn the user and ask whether to proceed with an empty changelog entry.

---

### 8. Commit the version bump and changelog

```bash
git add VERSION CHANGELOG.md
git commit -m "chore(release): v<NEW_VERSION>"
git log --oneline -5
```

Show the user the recent log so they can sanity-check what's going into the release.

---

### 9. Tag the release (annotated)

```bash
git tag -a v<NEW_VERSION> -m "Release v<NEW_VERSION>"
```

Verify the tag was created:
```bash
git tag --list "v<NEW_VERSION>"
```
Abort if the tag doesn't appear.

---

### 10. Final review (dry-run gate)

Show the user a summary of everything that will be pushed:
- The release commit (`git show --stat HEAD`)
- The tag (`git tag -l --format='%(objecttype) %(refname:short)' v<NEW_VERSION>`)
- The target branch and remote

Ask for explicit go/no-go confirmation before pushing. This is the last chance to abort without external side effects.

---

### 11. Push

```bash
git push origin main
```

Verify the main push succeeded before proceeding. If it fails (e.g., remote moved), abort and re-sync.

```bash
git push origin v<NEW_VERSION>
```

If the main push succeeded but the tag push fails, the release commit is already on `origin/main` but no workflow triggered. Re-run `git push origin v<NEW_VERSION>` once the issue is resolved.

This triggers the GitHub Actions workflow at `.github/workflows/publish.yml`, which builds and publishes to PyPI using OIDC trusted publishing.

---

### 12. Verify the workflow triggered

Capture the run ID scoped to the release commit (GitHub may take a few seconds to register the run):
```bash
gh run list --workflow=publish.yml --commit $(git rev-parse HEAD) --limit=1 --json databaseId --jq ".[0].databaseId"
```
If the command returns empty, wait a few seconds and retry — the run may not be queued yet.

Then watch that specific run:
```bash
gh run watch <RUN_ID>
```

Streams the run live and exits non-zero on failure. If the run fails, inspect logs:

```bash
gh run view --log-failed <RUN_ID>
```

---

## Prerequisites (one-time)

- GitHub environment named `pypi` — ✓ configured
- PyPI trusted publisher: owner `Mnemoq`, repo `MnemoQ`, workflow `publish.yml`, environment `pypi` — **needs manual verification on pypi.org → Account settings → Publishing**
- `gh` CLI authenticated — ✓ v2.94.0
- VERSION is read dynamically by setuptools via `pyproject.toml`: `dynamic = ["version"]` + `version = {file = "VERSION"}`. If that wiring breaks, the published version will be stale.

---

## Rollback (if publish fails)

If the PyPI upload fails and the tag is already pushed:
- **PyPI does not allow re-uploading the same version.** You must bump again.
- Delete the remote tag:
  ```bash
  git push --delete origin v<NEW_VERSION>
  ```
- Delete the local tag:
  ```bash
  git tag -d v<NEW_VERSION>
  ```
- Revert the release commit on main:
  ```bash
  git revert HEAD --no-edit
  git push origin main
  ```
- Fix the issue, then repeat from Step 1 with a new version number.

---

## Cleanup

**On any exit** — whether the workflow completes successfully, the user aborts, or an error occurs — always release the git session lock before ending:

```powershell
Remove-Item ".git/.windsurf-git-lock" -ErrorAction SilentlyContinue
```
