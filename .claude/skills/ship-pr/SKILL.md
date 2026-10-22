---
name: ship-pr
description: Module agent — after a task is complete and verified locally, check ownership/overlap, push the agent branch, open the pull request with the full report, and notify the orchestrator.
---

# Ship a pull request (module agent)

Precondition: `/verify` passed on the current HEAD and all work is committed.

1. **Integrate latest main.** `git fetch origin`. If not yet pushed: `git rebase origin/main`; otherwise `git merge origin/main`. Re-run `/verify` if main brought changes.
2. **Guard check.** `python3 .claude/team/bin/check_branch.py`
   - `ownership:` problem → you changed a file you do not own. Revert that change, then `/conflict-report` if you really need it.
   - `overlap:` problem → another open PR touches the same file. **Stop.** Run `/conflict-report`; do not push.
3. **Push your branch only.** `git push -u origin agent/<you>` (never force) — as its **own Bash call, with no pipe, `2>&1` or `&&`**. In auto mode each piece of a compound command must match an allow rule on its own; `| tail -1` matches none, so the whole push falls to the classifier, which denies it as publication. Same for `gh pr create` below.
4. **Open the PR** against `main`, filling every section of `.github/pull_request_template.md`:
   ```bash
   gh pr create --base main --head agent/<you> --title "<module>: <outcome>" --body-file <tmpfile>
   ```
   Write the body to a temp file first. It is your report: what changed and why, verification evidence, tests added, doc references, risks, follow-ups. Include `Closes #<issue>`.
5. **Notify.** `SendMessage` to `orchestrator`: `PR #<n> ready for review: <title> (closes #<issue>)`.
6. **Wait.** Answer review questions. If the orchestrator returns findings (PR comment + label `status:changes-requested`): fix every numbered finding on your branch as new commits (no force), run `/verify`, push, `gh pr edit <n> --remove-label status:changes-requested`, and `SendMessage` the orchestrator `PR #<n> fixed: <findings addressed>` with the new verification counts. Do not start code for the next task until the orchestrator confirms the merge.
