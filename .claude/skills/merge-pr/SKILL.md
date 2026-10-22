---
name: merge-pr
description: Orchestrator — squash-merge a reviewed, verified PR, notify its agent, unblock agents that were waiting on it, and update the project state in the knowledge base.
---

# Merge a pull request (orchestrator)

Precondition: `/review-pr` passed; CI (when present) is green; the PR's files are all owned by its agent.

1. Merge: `gh pr merge <n> --squash` (subject `<module>: <outcome> (#<n>)`), then `git push origin --delete agent/<name>`. Do **not** pass `--delete-branch`: it removes the local branch and the worktree under a running agent.
2. Notify the author: `SendMessage` to the agent: `PR #<n> merged. Run /sync-after-merge, then /task-start for #<next>` (or "no new task yet").
3. **Unblock waiters:** `gh issue list --label status:blocked --state open`. For each conflict issue that waited on this PR: comment "Unblocked by #<n>", remove `status:blocked`, and `SendMessage` that agent: `Go ahead on #<issue>: PR #<n> is merged. Run /sync-after-merge first.` Never send go-ahead before the merge has happened.
4. **Record** in `kb/state/project-state.md`: move the item from "In flight" to "Done", update the milestone checklist and next steps. Add to `kb/lessons.md` if you had to correct a recurring mistake (and consider turning it into a rule in `.claude/rules/`).
5. Commit the `kb/` update on `main` (`kb: record merge of #<n>`) and push — `kb/`-only commits may go directly to `main`.
