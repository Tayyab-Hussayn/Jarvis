---
type: state
owner: orchestrator
updated: 2026-10-22
---

# Orchestrator handoff (read after every compaction)

## Owner working style
- Owner writes **Roman Urdu**; reply in simple Roman Urdu with a clear recommendation. Code, docs, issues, PRs, commits in English.
- Owner reads little: keep summaries short. Correct a bad request before implementing it.
- Solo, local-first project; GitHub is a **private** remote (`Tayyab-Hussayn/Jarvis`). Confirm before the first push of a session.

## Starting the orchestrator
- Always start as the orchestrator with `.claude/team/bin/start-lead.sh` (persona appended, session named `orchestrator`, resumes `runtime/orchestrator-session`). A plain `claude` session has no persona and agents cannot reach it by name.

## Review fixes
- **Owner rule (2026-10-22): never fix a delivered PR yourself.** Every review finding, however small, goes back to the delivering agent: `gh pr comment` + label `status:changes-requested` + SendMessage; it fixes, removes the label, pings; re-review. Out-of-scope problems become new issues. `gh pr list --label status:changes-requested` shows what is back with agents. (PR #8 had an orchestrator `review:` commit — that is no longer allowed.)

## Approvals
- The owner delegated routine agent approvals to the orchestrator (2026-10-22): read the pane, then `tmux send-keys -t kravein-team:<agent> 1`. Refuse list is in the persona.

## Routines
- Review each PR in a separate worktree: `git fetch origin pull/N/head:review/pr-N && git worktree add .claude/worktrees/review-N review/pr-N`; run the module's Verify; clean up with `git worktree remove` + `git branch -D`.
- Merge a local agent's PR with `gh pr merge N --squash` then `git push origin --delete agent/X`. **Never `--delete-branch`** (it removed a worktree under a running agent in the project this workflow came from).
- Guard reminder: commit in one Bash call and push in a separate call (the guard checks before the command runs). Only `kb/`-only commits may go directly to `main`.
- Update `kb/state/project-state.md` after every merge; mirror a short note into `user-space/STATE.md` only if useful.

## Safety notes specific to Kravein
- Agents never get `--dangerously-skip-permissions`. Anything that kills processes, closes windows or deletes files in a test must use temp dirs / mocks, never the live desktop session.
- Do not run `kravein serve` or real-desktop skill tests from an agent session that shares the terminal with the user's server.

## In flight right now
- See [[project-state]].
