---
name: update-state
description: Full manual refresh of kb/state/project-state.md against live git/GitHub. Manually triggered by the owner via /update-state — never auto-invoke. Per-merge updates happen in /merge-pr without this command.
disable-model-invocation: true
---

# /update-state (orchestrator)

1. **Live state:**
   ```bash
   git fetch -q origin && git log --oneline origin/main -5
   gh pr list --state open && gh issue list --state open
   .venv/bin/python -m compileall -q kravein/
   .claude/team/bin/team status
   ```
2. **Rewrite `kb/state/project-state.md`** (keep it under 60 lines): Done (newest first, one line each), In flight (issue/PR + agent), Next, Open owner decisions, Blockers, Baseline test counts. Move long detail to `kb/lessons.md` or module notes.
3. **Commit** `kb: refresh project state` on `main` and push it (a `kb/`-only commit; commit and push in separate Bash calls).
4. **Report** to the owner in a few lines of Roman Urdu: what changed since the last refresh.
