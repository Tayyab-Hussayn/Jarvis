---
name: update-state
description: Full manual refresh of user-space/STATE.md's repo/health snapshot. Manually triggered by the user via /update-state — never auto-invoke this. Note — per-phase task progress is updated inline during work, without this command; this is for a full repo-state refresh specifically.
disable-model-invocation: true
---

# /update-state

Run these steps in order, then report a short summary of what changed.

## 1. Live state

```
git status --short
git log -1 --format="%H %ci"
.venv/bin/python -m compileall -q kravein/
```

Local git only — no remote/branch state to check. Note whether the compile check currently
passes.

## 2. Rewrite the repo/health sections of `user-space/STATE.md`

Keep the "Current task" section as-is (that's maintained inline during work, not by this
command) — only refresh:
- `Last refreshed: <UTC timestamp>` banner, plus the caveat: "Snapshot only — re-verify via `git`
  before trusting for anything consequential."
- HEAD commit + date, uncommitted-paths summary.
- One line on compile health.

## 3. Report

Tell the user what changed since the last refresh, in a few lines.
