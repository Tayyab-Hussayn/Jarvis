---
name: conflict-report
description: Stop work and report a blocker — needing a file you do not own, overlapping another open PR, a missing/unmerged interface change, ambiguous spec, a missing product decision, or a guard that repeatedly blocks correct work (guard friction). Then wait for the orchestrator.
---

# Conflict / blocker report

1. **Stop editing.** Commit any safe work-in-progress on your branch (do not push unfinished work unless asked).
2. **Open an issue** (or comment on your task issue if it is purely a question):
   ```bash
   gh issue create --title "conflict(<you>): <short description>" \
     --label "type:conflict,status:blocked,agent:<you>" --body-file <tmpfile>
   ```
   Body (English, concise):
   - **Task:** #<issue>
   - **Blocked by:** file(s) / PR #n / interface / doc section / decision
   - **Owner of the blocking item:** agent or orchestrator
   - **What I need:** the exact change or answer
   - **Proposed resolution:** your recommendation (e.g. "orchestrator grants kravein/registry.py for this task", "wait for PR #14")
   - **Can I continue meanwhile?** yes (what part) / no
   - **Guard friction only:** the exact blocked command or file path, the block message, and why the work is correct and in scope. Title: `guard friction(<you>): <short>`.
3. **Notify.** `SendMessage` to `orchestrator`: `BLOCKED: <issue link> — <one line>`.
4. **Wait.** Do not touch the blocked files. Continue only the independent part you listed, if any.
5. Resume the blocked work only after the orchestrator's explicit go-ahead; then run `/sync-after-merge` if a merge happened.
