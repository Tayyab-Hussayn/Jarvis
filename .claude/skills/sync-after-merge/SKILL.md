---
name: sync-after-merge
description: Module agent — after the orchestrator merges your PR (or tells you another PR you waited on is merged), reset your branch onto the new main safely and re-verify.
---

# Sync after a merge (module agent)

1. Make sure nothing is lost: `git status` must be clean and `git log origin/main..HEAD` must contain only commits that are already in the merged PR. If you have extra unpushed work, stop and tell the orchestrator.
2. Fetch: `git fetch origin --prune`
3. **Your own PR was merged** (squash merge deleted your remote branch):
   `git switch -C agent/<you> origin/main`
   **You were waiting on someone else's PR** (your branch has unmerged work of your own):
   `git merge origin/main`
4. Run `/verify` to confirm main is green for your module.
5. Continue with the next task (`/task-start`).
