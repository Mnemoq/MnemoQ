---
description: Stages changes cleanly and produces a well-structured Conventional Commit message with branch hygiene checks.
---

## Steps

### 1. Check branch hygiene

Run the following and surface any issues before touching the index:

```bash
git status
git diff --stat
```

- Warn if the current branch is `main` or `master` — ask the user to confirm or switch to a feature branch first.
- Warn if there are untracked files that look like they should be committed (e.g. new source files, not build artefacts).

---

### 2. Analyse the diff

```bash
git diff
```

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

Once the user approves:

```bash
git commit -m "<approved message>"
```

If a body or footer is needed, use:

```bash
git commit -F- <<'EOF'
feat(auth): add OAuth2 Google login

Implements the Google OAuth2 flow using Passport.js.
Redirects unauthenticated users to /login on protected routes.

Closes #42
EOF
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

---

### 7. Repeat for remaining changes (if split was needed)

If multiple logical groups were identified in Step 2, loop back to **Step 3** and handle the next group. Continue until `git status` shows a clean working tree or only intentionally unstaged changes remain.

---

### 8. Push (optional)

Ask the user if they want to push:

```bash
git push origin <current-branch>
```

If the branch has no upstream yet:

```bash
git push --set-upstream origin <current-branch>
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
