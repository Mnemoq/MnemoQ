---
description: Stages changes cleanly and produces a well-structured Conventional Commit message with branch hygiene checks.
---

## Steps

### 0. Git session lock

Acquire the git session lock before proceeding. See `_git-lock.md` for the full lock-check snippet.

Run the lock-check command. If `LOCKED:`, stop and inform the user. If `STALE:`, override. If `FREE`, acquire:

```powershell
"$([DateTime]::UtcNow.ToString('o')) /commit" | Set-Content ".git/.claude-git-lock"
```

---

### 1. Check branch hygiene

Run the following and surface any issues before touching the index:

```bash
git status
git diff --stat
```

- Warn if the current branch is `main` or `master` — ask the user to confirm or switch to a feature branch first.
- Warn if the branch name doesn't match any prefix from the Branch Naming Convention (see Reference section below). This is advisory — do not block.
- Warn if there are untracked files that look like they should be committed (e.g. new source files, not build artefacts).

---

### 2. Analyse the diff

```bash
git diff HEAD
```

If this is the initial commit (no HEAD yet), use `git diff --staged` instead.

Read the full diff and silently identify:

- **Logical change groups** — distinct concerns in the diff (e.g. "new feature code", "refactor of existing helper", "updated tests", "docs update").
- **Suggested split** — if there is more than one logical group, propose separate commits. Ask the user which they want to do first.

---

### 3. Stage the right changes

Based on the logical group chosen, stage precisely:

- For **whole files**: `git add <file>`
- For **partial files**: use `git add -p <file>` and guide the user through hunk selection with `y / n / s` prompts.
- For **new files**: `git add <file>`

After staging, always run:

```bash
git diff --staged
```

Show the user a summary and ask: *"Does this look right to stage?"* before proceeding.

```bash
ruff check .
```

- If **lint fails**: stop, surface the failure, do not commit. The user must fix lint errors before proceeding.
- If **lint passes**: proceed to compose the commit message.

---

### 4. Compose the commit message

Using the Conventional Commits spec, propose a message in this format:

```
<type>(<scope>): <short imperative description>

[optional body — explain WHY, not WHAT]

[optional footer — BREAKING CHANGE: ..., Closes #123]
```

**Type reference:**

| Type       | When to use                                      |
|------------|--------------------------------------------------|
| `feat`     | New feature visible to users                     |
| `fix`      | Bug fix                                          |
| `refactor` | Code change that isn't a fix or feature          |
| `docs`     | Documentation only                               |
| `style`    | Formatting, whitespace — no logic change         |
| `test`     | Adding or fixing tests                           |
| `chore`    | Build, deps, tooling, config                     |
| `perf`     | Performance improvement                          |
| `ci`       | CI/CD pipeline changes                           |
| `revert`   | Reverts a previous commit                        |

**Rules enforced:**
- Subject line ≤ 72 characters
- Present tense, imperative mood (`add` not `added`)
- No period at end of subject
- Scope is lowercase and matches the module/area changed

Present the proposed message to the user and ask for approval or edits before committing.

---

### 5. Commit

For a single-line message:

```bash
git commit -m "<approved message>"
```

For a multi-line message (body and/or footer), write to a temp file and commit with `-F` — this avoids shell escaping issues with quotes and backticks:

```powershell
# Write the commit message to a temp file
"feat(auth): add OAuth2 Google login`n`nImplements the Google OAuth2 flow.`n`nCloses #42" | Set-Content -Path "$env:TEMP\commit-msg.txt" -Encoding utf8

# Commit using the temp file
git commit -F "$env:TEMP\commit-msg.txt"

# Clean up
Remove-Item "$env:TEMP\commit-msg.txt"
```

---

### 6. Verify the commit

```bash
git log --oneline -5
git show --stat HEAD
```

Show the user:
- The new commit in the log
- Which files were changed and the line count

If the commit needs fixing (wrong message, forgot a file), use `git commit --amend` before pushing.

---

### 7. Repeat for remaining changes (if split was needed)

If multiple logical groups were identified in Step 2, loop back to **Step 3** and handle the next group. Continue until `git status` shows a clean working tree or only intentionally unstaged changes remain.

---

### 8. Push (optional)

Ask the user if they want to push. If yes, suggest running `/push` — it handles pre-flight checks, remote sync, CI verification, and PR creation.

---

## Cleanup

**On any exit** — whether the workflow completes successfully, the user aborts, or an error occurs — always release the git session lock before ending:

```powershell
Remove-Item ".git/.claude-git-lock" -ErrorAction SilentlyContinue
```

---

## Reference: Branch Naming Convention

```
feature/<short-description>      # new functionality
fix/<short-description>          # bug fixes
hotfix/<short-description>       # urgent production fixes
refactor/<short-description>     # code improvements
docs/<short-description>         # documentation only
chore/<short-description>        # tooling, deps, config
release/<version>                # release prep  e.g. release/2.1.0
```

Examples:
```
feature/stripe-payment-integration
fix/login-redirect-loop
hotfix/null-pointer-checkout
docs/update-api-readme
chore/bump-dependencies
```

---

## Reference: Quick Commit Examples

```bash
feat(auth): add OAuth2 Google login
fix(api): handle null response from payment gateway
refactor(utils): simplify date formatting logic
docs(readme): add Windows setup instructions
chore(deps): bump lodash to 4.17.21
test(auth): add unit tests for token refresh
perf(db): add index on users.email column
ci(github): add automated release workflow
style(components): apply prettier formatting
revert: revert feat(auth) OAuth2 Google login
```
