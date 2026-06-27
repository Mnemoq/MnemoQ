---
description: Pre-flight checks, remote sync, clean push, and post-push verification with CI status and PR creation.
---

## Steps

### 1. Pre-flight checks

Run the following and surface any issues before pushing:

```bash
git status
git branch --show-current
```

- Warn if the working tree is dirty (uncommitted changes) — ask the user to commit or stash first.
- Warn if the current branch is `main` or `master` — many repos have branch protection rules that reject direct pushes. If the push is rejected with "push declined due to repository rule violations", tell the user to create a feature branch, commit there, and use `/pr` to merge via PR. Do not retry the direct push.
- Validate branch naming against the convention in `commit.md` § Branch Naming Convention.

Then run local checks to catch issues before CI does:

```bash
ruff check .
python -m pytest
```

Also scan the committed diff for unresolved conflict markers:

```bash
git diff HEAD~1 HEAD | grep -E "^(<<<<<<<|=======|>>>>>>>)"
```

If conflict markers are found, stop and surface them — do not push.

- If **lint fails**: stop, surface the failure, do not push.
- If **tests fail**: stop, surface the failure, do not push.

---

### 2. Remote sync

Fetch the latest remote state and check if the local branch is behind:

```bash
git fetch origin
git ls-remote --exit-code origin <current-branch>
```

- If `ls-remote` **fails** (branch doesn't exist on remote yet): skip Step 2 entirely — there's nothing to sync against. Jump to Step 3 and use `--set-upstream`.
- If `ls-remote` **succeeds**: check how far behind the local branch is:

```bash
git rev-list --count HEAD..origin/<current-branch>
```

- If **behind by 0 commits**: proceed to Step 3.
- If **behind by >0 commits**: offer `git rebase origin/<current-branch>` (preferred) or `git merge origin/<current-branch>`. Ask the user which they prefer.
- If **rebase has conflicts**: stop and guide the user through conflict resolution. Do not proceed until conflicts are resolved and the rebase is complete.

---

### 3. Push

Determine the push command based on whether a rebase occurred in Step 2:

**Normal path** (no rebase occurred):

```bash
git push origin <current-branch>
```

If the branch has no upstream yet:

```bash
git push --set-upstream origin <current-branch>
```

**After rebase** — use `--force-with-lease` (safe variant, auto-used without extra confirmation — the user already approved the rebase):

```bash
git push --force-with-lease origin <current-branch>
```

`--force-with-lease` fails if the remote moved since the last fetch, preventing accidental overwrites.

**After rebase, first push (no upstream yet)** — combine both flags:

```bash
git push --set-upstream --force-with-lease origin <current-branch>
```

**Force-push guard:**
- Never use `--force` on `main` or `master`.
- `--force-with-lease` on feature branches is allowed after rebase or with explicit user confirmation.
- Never use bare `--force` — always `--force-with-lease`.

**Tag awareness:**
- If a `v*` tag is staged for push, warn that it triggers the PyPI publish workflow (`.github/workflows/publish.yml`).
- Require explicit confirmation before pushing tags.
- If the user hasn't bumped `VERSION` yet, point them to `/publish` — do not handle version bumping or tagging here.

---

### 4. Post-push verification

Confirm the push landed on the remote:

```bash
git fetch origin
git log --oneline -3 origin/<current-branch>
```

Show the user the latest commits on the remote.

**CI status:**

If `gh` CLI is available:

```bash
sleep 3
gh run list --branch <current-branch> --limit=1
```

The brief sleep lets GitHub register the push and queue the workflow run. Show the user the latest CI run status for this branch.

Otherwise, construct the GitHub Actions URL from the remote:

```bash
git remote get-url origin
```

Parse the remote URL (SSH or HTTPS) into `https://github.com/<org>/<repo>/actions`. If unparseable, show the raw remote URL and let the user navigate manually.

**Offer to create a PR:**

If the current branch is not `main`/`master`:

```bash
git log origin/main..<current-branch> --oneline
```

Show the user the commits that would go into a PR. Ask the user if they want to create a PR.

If yes, suggest running `/pr` — it handles PR creation with options for base branch, draft mode, and auto-generated description.

---

## Reference: Branch Naming Convention

See `commit.md` § Branch Naming Convention for the full list of branch prefixes and examples.
