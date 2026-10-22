---
name: orient
description: Orchestrator — run at the start of every session. Re-checks live git, GitHub and team state instead of trusting a stale mental model of what is done. Module agents do not need it; their SessionStart briefing covers the same ground.
---

# Orient (orchestrator, every session)

1. **Briefing first.** Read the SessionStart briefing (role, open PRs, blocked issues, agent tasks, guard blocks). Then `kb/state/project-state.md` and `kb/state/orchestrator-handoff.md`.
2. **Live repo state:**
   ```bash
   git status --short && git log --oneline -3
   git fetch -q origin && git log --oneline origin/main -1
   git log --oneline origin/main..lead/batch 2>/dev/null   # unpushed batch work
   ```
3. **Team state:** `.claude/team/bin/team status` (tmux windows, worktrees, guard blocks). For each running agent, check its pane before sending work: `tmux capture-pane -t kravein-team:<agent> -p | tail -15` — a pending permission prompt means it is waiting for you.
4. **Health:** `.venv/bin/python -m compileall -q kravein/`. Deeper checks happen in review, not here.
5. **Reconcile.** If `project-state.md` disagrees with git or GitHub, git/GitHub win; fix the state file in the next `kb:` commit.
6. Update this file when a session discovers a process mistake worth not repeating.
