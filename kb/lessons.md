---
type: log
owner: orchestrator
updated: 2026-10-22
---

# Lessons Learned

Corrections made during review. When the same lesson appears twice, turn it into a rule in `.claude/rules/` and link it here. Product/platform lessons live in `CLAUDE.md` "Hard-won knowledge".

| Date | PR | Module | Lesson | Promoted to rule? |
|---|---|---|---|---|
| 2026-10-22 | — | process | (inherited) `gh pr merge --delete-branch` removes the local branch and worktree under a running agent | yes — `merge-pr` skill |
| 2026-10-22 | — | process | (inherited) a guard checks before the command runs: commit and push in separate Bash calls | handoff note |
