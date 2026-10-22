---
name: review-pr
description: Orchestrator — review a module agent's pull request: ownership, correctness, docs conformance, tests, safety, and local verification in a review worktree. Ends in fix-and-merge, request-changes, or reject.
---

# Review a pull request (orchestrator)

1. **Read the report:** `gh pr view <n> --comments` and the linked issue. Missing report sections → request changes.
2. **Scope check:** `gh pr diff <n> --name-only` — every file must be owned by the PR's agent (roster). Anything else → request changes or split.
3. **Check out for verification** in a separate worktree (keep the main checkout clean):
   ```bash
   git fetch origin pull/<n>/head:review/pr-<n>
   git worktree add .claude/worktrees/review-<n> review/pr-<n>
   ```
   Run the module's Verify commands from `kb/modules/<agent>.md` there. Evidence must match the PR's claims.
4. **Review the diff** (`gh pr diff <n>`), in this order:
   - correctness against acceptance criteria and the cited docs,
   - tests: new behavior covered, failure paths covered, no weakened tests,
   - safety: no bypass of `safety.enforce()`, no direct subprocess outside `platform/shell.py`, no secrets or user content in the audit log, confirmations still deny on every failure path,
   - architecture: skills stay plain `@skill` functions, platform concerns stay in `platform/`, transport code does not leak into skills, the Windows-original tree untouched,
   - simplicity: no dead code, speculative abstractions, or unrelated changes.
   For a large PR, delegate focused passes to subagents (e.g. one for tests, one for security) and keep only their findings.
5. **Decide:**
   - **Small/mechanical fixes** → make them yourself on the PR branch in the review worktree, commit `review: <fix>`, `git push origin HEAD:agent/<name>`, re-verify.
   - **Design-level problems** → `gh pr review <n> --request-changes --body-file <tmpfile>` with exact instructions; `SendMessage` the agent.
   - **Ready** → `/merge-pr`.
6. Clean up: `git worktree remove .claude/worktrees/review-<n>` and `git branch -D review/pr-<n>`.
