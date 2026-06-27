---
description: Full ship cycle — commit, push, rebase if needed, create PR, verify CI, and merge. Delegates to individual workflows at each step.
---

## Steps

### 1. Commit

Delegate to `/commit` — it handles branch hygiene, staging, lint, and Conventional Commit message.

**If the working tree is already clean** (user pre-committed), skip `/commit` and jump straight to Step 2. Tell the user: *"No uncommitted changes — skipping commit step."*

After `/commit` completes (or is skipped), ask the user:

> **Committed. Continue to push?**
> 1. **Yes, push now** — continue to Step 2
> 2. **No, stop here** — run `/push` later when ready

If the user stops here, end the workflow.

---

### 2. Push

Delegate to `/push` — it handles pre-flight checks (lint + tests), remote sync, and push.

If `/push` detects the branch is behind `origin/<current-branch>`:

> **Your branch is behind the remote. Rebase or merge?**
> 1. **Rebase** — delegate to `/rebase` (onto `origin/<current-branch>`), then continue push
> 2. **Merge** — let `/push` handle the merge inline
> 3. **Abort** — stop and let the user resolve manually

After `/push` completes, ask the user:

> **Pushed. Continue to create a PR?**
> 1. **Yes, create a PR** — continue to Step 3
> 2. **No, stop here** — run `/pr` later when ready

If the user stops here, end the workflow.

---

### 3. Create a PR

Delegate to `/pr` → "Create a PR" — it handles branch checks, generates a title and body from commit messages, and asks for PR options (base branch, draft, title, body).

After the PR is created, ask the user:

> **PR created. Wait for CI and merge?**
> 1. **Yes, wait for CI** — continue to Step 4
> 2. **No, stop here** — check CI and merge later with `/pr`

If the user stops here, end the workflow.

---

### 4. Wait for CI

Poll CI status until all checks complete:

```bash
gh pr view <PR-NUMBER> --json statusCheckRollup --jq ".statusCheckRollup[] | {name, status, conclusion}"
```

- If any checks are **pending**: wait 10 seconds and re-check. Show the user which checks are still running.
- If all checks **passed**: proceed to Step 5.
- If any checks **failed**: stop and surface the failures.

Ask the user:

> **CI checks failed. What do you want to do?**
> 1. **Fix and re-push** — go back to Step 1 (commit the fix), then re-push
> 2. **Merge anyway** — continue to Step 5 with a warning
> 3. **Stop** — fix manually and merge later with `/pr`

---

### 5. Merge

Delegate to `/pr` → "Merge a PR" — it handles pre-merge checks, asks for merge method (squash/merge/rebase), and branch deletion.

Ask the user:

> **Merge method?**
> 1. **Squash and merge** — combine all commits into one (recommended for feature branches)
> 2. **Merge commit** — preserve all commits with a merge commit
> 3. **Rebase and merge** — replay commits onto base without a merge commit

Ask the user:

> **Delete the branch after merge?**
> 1. **Yes** — delete both local and remote branch
> 2. **No** — keep the branch

After merge, perform post-merge cleanup:

```bash
git checkout <base-branch>
git pull origin <base-branch>
```

If the user chose to delete the branch, clean up the local branch:

```bash
git branch -d <merged-branch-name>
```

---

### 6. Done

Show the user a summary:

- **PR number and URL**
- **Merge method used**
- **Branch status** (deleted or kept)
- **Current branch** (should be on the base branch, up to date)

Remind the user:

> If you're ready to release, run `/publish` — it handles version bump, tagging, and PyPI publication. `/publish` is always separate from `/ship`.
