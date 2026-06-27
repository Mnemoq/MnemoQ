---
description: Create, check, update, and merge pull requests via gh CLI.
---

## Steps

### 0. Check `gh` CLI availability

```bash
gh --version
```

If `gh` is not installed or not authenticated, stop and tell the user to install it (`winget install GitHub.cli`) and authenticate (`gh auth login`). All PR operations require `gh`.

---

### 1. Ask the user what they want to do

Ask the user:

> **What do you want to do?**
> 1. **Create a PR** — open a new pull request from the current branch
> 2. **Check PR status** — view reviews, CI checks, and mergeable state for an existing PR
> 3. **Update a PR** — update title or body after new commits
> 4. **Merge a PR** — merge an existing PR and optionally clean up the branch
> 5. **Close/reopen a PR** — close without merging or reopen a closed PR

---

### 2a. Create a PR

#### Prerequisites

- Current branch must not be `main` or `master`. If it is, warn the user and ask them to switch to a feature branch.
- The branch must be pushed to the remote. If not, tell the user to run `/push` first.

```bash
git branch --show-current
git ls-remote --exit-code origin <current-branch>
```

If `ls-remote` fails (branch not on remote), stop and tell the user to push first.

#### Generate PR description

```bash
git log origin/main..<current-branch> --oneline
```

Generate a PR title and body from the Conventional Commit messages in the log.

- **Title**: derive from the most significant commit (prefer `feat` > `fix` > `refactor` > others). Format: `<type>(<scope>): <summary>`
- **Body**: group changes by type and list the commit messages. Include a summary section if there are many commits.

#### Ask the user for PR options

Ask the user:

> **PR options:**
> 1. **Base branch** — which branch to merge into? (default: `main`)
> 2. **Draft?** — open as draft PR or ready for review?
> 3. **Title** — confirm or edit the generated title
> 4. **Body** — confirm or edit the generated description

#### Create the PR

Write the body to a temp file to avoid shell escaping issues with quotes and backticks:

```powershell
"<approved body>" | Set-Content -Path "$env:TEMP\pr-body.md" -Encoding utf8
gh pr create --base <base-branch> --title "<approved title>" --body-file "$env:TEMP\pr-body.md" [--draft]
Remove-Item "$env:TEMP\pr-body.md"
```

Show the user the PR URL from the output.

---

### 2b. Check PR status

Ask the user:

> **Which PR?**
> - Enter a PR number, or press Enter for the PR on the current branch

```bash
gh pr view <PR-NUMBER> --json title,state,mergeable,reviewDecision,statusCheckRollup
```

Show the user:
- **Title and state** (open/closed/merged)
- **Mergeable** status (mergeable, conflicting, unknown)
- **Review decision** (approved, changes requested, review required, no reviews)
- **CI checks** — list each check name and status (pass/fail/pending)

If any CI checks are failing, surface them and suggest fixing before merging.

If `gh pr view` fails (no PR for this branch), tell the user no PR exists and offer to create one (redirect to Step 2a).

---

### 2c. Update a PR

Ask the user:

> **Which PR?**
> - Enter a PR number, or press Enter for the PR on the current branch

Then ask:

> **What do you want to update?**
> 1. **Title** — change the PR title
> 2. **Body** — change the PR description
> 3. **Both** — update title and body

For title:

```bash
gh pr edit <PR-NUMBER> --title "<new title>"
```

For body — write to a temp file to avoid escaping issues:

```powershell
"<new body>" | Set-Content -Path "$env:TEMP\pr-body.md" -Encoding utf8
gh pr edit <PR-NUMBER> --body-file "$env:TEMP\pr-body.md"
Remove-Item "$env:TEMP\pr-body.md"
```

Show the user the updated PR and confirm it looks right.

---

### 2d. Merge a PR

Ask the user:

> **Which PR?**
> - Enter a PR number, or press Enter for the PR on the current branch

#### Pre-merge checks

```bash
gh pr view <PR-NUMBER> --json state,mergeable,reviewDecision,statusCheckRollup
```

- If **state is not open**: stop — cannot merge a closed/merged PR.
- If **mergeable is conflicting**: stop — tell the user to resolve conflicts first (suggest `/rebase` onto the base branch).
- If **CI checks are failing**: warn the user and ask if they want to merge anyway.
- If **review is required and not approved**: warn the user and ask if they want to merge anyway.

#### Ask the user for merge options

Ask the user:

> **Merge method?**
> 1. **Squash and merge** — combine all commits into one (recommended for feature branches)
> 2. **Merge commit** — preserve all commits with a merge commit
> 3. **Rebase and merge** — replay commits onto base without a merge commit

Ask the user:

> **Delete the branch after merge?**
> 1. **Yes** — delete both local and remote branch
> 2. **No** — keep the branch

#### Merge

```bash
gh pr merge <PR-NUMBER> --squash --delete-branch
```

Or without branch deletion:

```bash
gh pr merge <PR-NUMBER> --squash
```

Replace `--squash` with `--merge` or `--rebase` based on the user's choice.

#### Post-merge cleanup

After merge, offer to switch to the base branch and pull:

```bash
git checkout <base-branch>
git pull origin <base-branch>
```

If the user chose to delete the remote branch, the local branch may still exist. Offer to clean up:

```bash
git branch -d <merged-branch-name>
```

---

### 2e. Close/reopen a PR

Ask the user:

> **Which PR?**
> - Enter a PR number, or press Enter for the PR on the current branch

Then ask:

> **Close or reopen?**
> 1. **Close** — close the PR without merging
> 2. **Reopen** — reopen a previously closed PR

For close:

```bash
gh pr close <PR-NUMBER>
```

For reopen:

```bash
gh pr reopen <PR-NUMBER>
```

Optionally offer to delete the branch after closing:

```bash
git push origin --delete <branch-name>
git branch -d <branch-name>
```

---

## Reference: gh CLI Quick Reference

| Action | Command |
|--------|---------|
| Create PR | `gh pr create --base main --title "..." --body-file <file> [--draft]` |
| View PR | `gh pr view <N> --json title,state,mergeable,reviewDecision,statusCheckRollup` |
| List PRs | `gh pr list --state open` |
| Edit PR title | `gh pr edit <N> --title "..."` |
| Edit PR body | `gh pr edit <N> --body-file <file>` |
| Merge (squash) | `gh pr merge <N> --squash [--delete-branch]` |
| Merge (merge commit) | `gh pr merge <N> --merge [--delete-branch]` |
| Merge (rebase) | `gh pr merge <N> --rebase [--delete-branch]` |
| Close PR (no merge) | `gh pr close <N>` |
| Reopen PR | `gh pr reopen <N>` |
| Check out PR locally | `gh pr checkout <N>` |
