---
description: Rebase feature branch onto main, squash/reorder commits, and resolve conflicts with structured guidance.
---

## Steps

### 1. Pre-flight checks

```bash
git status
git branch --show-current
```

- Warn if the working tree is dirty — ask the user to commit, stash, or use `--autostash` (rebases and reapplies uncommitted changes automatically).
- Warn if the current branch is `main` or `master` — rebasing main is almost never intended. Ask the user to switch to a feature branch.

---

### 2. Ask the user what kind of rebase they want

Ask the user:

> **What kind of rebase do you want?**
>
1. **Rebase onto main** — bring your feature branch up to date with the latest `origin/main`
2. **Interactive rebase** — squash, reorder, or edit commits on the current branch
3. **Rebase onto another branch** — rebase onto a different base branch (e.g. `develop`, another feature branch)

---

### 3a. Rebase onto main

Fetch the latest main and check how far behind the feature branch is:

```bash
git fetch origin
git rev-list --count HEAD..origin/main
```

- If **behind by 0**: tell the user the branch is already up to date with main. Nothing to do.
- If **behind by >0**: proceed with the rebase.

Ask the user:

> **Your branch is N commits behind main. Rebase now?**
> 1. **Yes, rebase onto origin/main**
> 2. **No, abort**

If yes:

```bash
git rebase origin/main
```

---

### 3b. Interactive rebase

Ask the user:

> **How many commits back do you want to edit?**
> - Enter a number (e.g. `3` to edit the last 3 commits)
> - Or enter `origin/main` to rebase all commits since branching off main

Then ask:

> **What do you want to do?**
> 1. **Squash all into one** — combine all selected commits into a single commit
> 2. **Reorder/edit** — pick, reword, squash, or fix individual commits
> 3. **Just reword the latest commit** — quick message fix

**Note:** `git rebase -i` opens an interactive editor that Cascade can't control directly. Use `GIT_SEQUENCE_EDITOR` to automate it, or ask the user to run it in their terminal.

For option 1 (squash all) — automate without an editor:

```bash
GIT_SEQUENCE_EDITOR="sed -i '2,\$s/^pick/squash/'" git rebase -i HEAD~<N>
```

Or guide the user to run it manually in their terminal:

```bash
git rebase -i HEAD~<N>
```

Tell them: set all commits except the first to `squash` (or `fixup` to discard their messages).

For option 2 (reorder/edit):

Ask the user to run in their terminal:

```bash
git rebase -i HEAD~<N>
```

Guide them through the editor actions: `pick`, `reword`, `squash`, `fixup`, `drop`, `edit`.

For option 3 (reword latest):

```bash
git commit --amend -m "<new message>"
```

No rebase needed — just amend.

---

### 3c. Rebase onto another branch

Ask the user:

> **Which branch do you want to rebase onto?**
> - Enter the branch name (e.g. `develop`, `feature/other-branch`)

Then:

```bash
git fetch origin
git rebase origin/<target-branch>
```

---

### 4. Conflict resolution

If the rebase stops due to conflicts:

```bash
git status
git diff --name-only --diff-filter=U
```

Show the user which files have conflicts. For each conflicted file:

1. Open the file and locate the conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`)
2. Ask the user which side to keep, or help them merge manually
3. After resolving, stage the file:

```bash
git add <resolved-file>
```

Once all conflicts are resolved:

```bash
git rebase --continue
```

Ask the user at each conflict:

> **Conflict in `<file>`:**
> 1. **Keep ours** (current branch changes)
> 2. **Keep theirs** (incoming changes)
> 3. **Merge manually** — I'll help you resolve it

If a commit becomes empty after rebase (no changes left), offer to skip it:

```bash
git rebase --skip
```

If the user wants to abort the rebase:

```bash
git rebase --abort
```

---

### 5. Verify the rebase

```bash
git log --oneline -10
git status
```

Show the user:
- The commit history after rebase
- That the working tree is clean

Ask the user: *"Does this look right?"*

If something went wrong and the user wants to undo:

```bash
git reflog
```

Help the user find the state before the rebase and reset to it:

```bash
git reset --hard <ref-before-rebase>
```

---

### 6. Push after rebase

After a rebase, the local history has diverged from the remote. A force-push is required.

Ask the user:

> **Rebase complete. Push now?**
> 1. **Yes, force-push with lease** (safe — fails if remote moved since last fetch)
> 2. **No, I'll push later** (suggest running `/push` when ready)

If yes:

```bash
git push --force-with-lease origin <current-branch>
```

**Force-push guard:**
- Never use `--force` on `main` or `master`.
- Never use bare `--force` — always `--force-with-lease`.
- `--force-with-lease` fails if the remote moved since the last fetch, preventing accidental overwrites.

---

## Reference: Rebase Quick Reference

| Action | Command |
|--------|---------|
| Rebase onto main | `git rebase origin/main` |
| Interactive rebase (last N commits) | `git rebase -i HEAD~N` |
| Interactive rebase (since branch point) | `git rebase -i origin/main` |
| Continue after resolving conflicts | `git rebase --continue` |
| Skip current commit (keep theirs) | `git rebase --skip` |
| Abort rebase | `git rebase --abort` |
| Undo a completed rebase | `git reset --hard <ref-from-reflog>` |
| Force-push after rebase | `git push --force-with-lease origin <branch>` |
