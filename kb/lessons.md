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
| 2026-10-22 | #4 | process | `gh issue view N --comments` prints only comments; a fresh packet looked empty to the agent | yes — `task-start` skill |
| 2026-10-22 | #3 | process | `.gitignore` lacked the team's per-machine dirs; a worktree was staged as an embedded repo | yes — `.gitignore` |
| 2026-10-22 | #3 | process | agents in `default` mode prompted on every write and heredoc | yes — `acceptEdits` default + Write/Edit rule |
| 2026-10-22 | #8 | qa | an empty `tests/web/` is not in git, so a Verify command naming it errors on a clean checkout | review fix |
| 2026-10-22 | #8 | process | a session started as plain `claude` (no `crossSessionInbound: accept`) holds agent messages for approval; start the orchestrator with `start-lead.sh` | handoff note |
| 2026-10-22 | — | process | (inherited) a guard checks before the command runs: commit and push in separate Bash calls | handoff note |
