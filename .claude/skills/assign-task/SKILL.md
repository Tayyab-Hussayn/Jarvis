---
name: assign-task
description: Orchestrator — create a complete task packet as a GitHub issue for one module agent, label it, and notify the agent. Use for every unit of work handed to engine, skills, web, or qa.
---

# Assign a task (orchestrator)

1. **Check readiness** (CLAUDE.md "How we work"): requirement clear, owner module known, relevant docs/audit findings identified, dependencies merged or explicitly sequenced, acceptance criteria testable. A change that crosses modules (e.g. a new `@skill` parameter the web UI renders) → split it so the interface lands first, or `team grant` the one file.
2. **Size it:** one PR, reviewable in one sitting (roughly ≤ 400 changed lines of non-generated code). Split larger work.
3. **Create the issue** from `.github/ISSUE_TEMPLATE/task.md`:
   ```bash
   gh issue create --title "<module>: <outcome>" --label "agent:<name>,type:task" --body-file <tmpfile>
   ```
   The packet must contain: goal, acceptance criteria (checklist), owned paths the work touches, read-only inputs (docs with section names), dependencies (issue/PR numbers), out of scope, verification expected.
4. **Model and effort are fixed:** every agent runs Sonnet at effort medium (owner rule). A hard task (`safety.py`, `webauth.py`, confirmation flow, concurrency) gets the stricter packet from the orchestrator persona instead (hard-won rules to read, plan comment before code, failure paths to test, self-review fork). Start it with `team up [--fresh] <agent>`. Only one or two agents should be active at once.
5. **Grant cross-module paths up front** if the task needs them and no other agent's open work touches them: `.claude/team/bin/team grant <agent> <path> <hours> <issue>`. List the grant in the task packet.
6. **Notify:** start the agent with the task already in hand — `team up [--fresh] [--model m] --task <n> <agent>` — so nothing waits on the session to come up. If the agent is already running, `SendMessage` it: `New task #<n>: <title>. Run /task-start.`
7. **Record** the assignment in the "In flight" list of `kb/state/project-state.md`.
