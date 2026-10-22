---
name: task-start
description: Module agent — begin a GitHub-issue task: sync the branch, load the task packet and relevant specs, post a plan. Use at the start of every assigned task.
---

# Start a task (module agent)

1. **Identify the task.** `gh issue list --label agent:<you> --state open` → pick the issue the orchestrator named. `gh issue view <N>` (the packet) and then `gh issue view <N> --comments` (later instructions). `--comments` alone prints **only** comments, so on a fresh issue it prints nothing — never conclude the packet is empty from it. If both fail, message the orchestrator.
2. **Check readiness.** The packet must have: goal, acceptance criteria, owned paths, doc references, dependencies. If anything is missing or a dependency PR is not merged, run `/conflict-report` instead of starting.
3. **Sync.** Your branch must be clean.
   - Not pushed yet: `git fetch origin && git rebase origin/main`
   - Already pushed: `git fetch origin && git merge origin/main`
4. **Load context, narrowly.** Read the referenced docs (`CLAUDE.md` hard-won knowledge, audit/design docs named in the packet), `kb/modules/<you>.md`, and the code the packet points at. Use a subagent for broad code searches.
5. **Plan.** Post one issue comment: approach in 3–8 bullets, files you will touch (all inside your ownership), tests you will add, risks. Then send the orchestrator a one-line `SendMessage` with the issue link only if the plan changes scope or raises a question; otherwise just proceed.
6. **Implement** in small commits: `<module>: <what>` + `Refs #<N>`.
