---
name: orient
description: Invoke before starting any work in this project, every session. Re-checks live git/test state instead of trusting a stale mental model of what's done. Use whenever work touches Kravein or anything under ~/code/Kravein.
---

# Orient — run this first, every session

## 1. Live repo state

```
git status
git diff --stat
git log -1
```

Local git only — no remote, no branches, no PRs to check. Don't assume the last session's summary
of "what's done" is still accurate — check.

## 2. Fast health check

```
.venv/bin/python -m compileall -q kravein/
```

Enough for orientation. Deeper verification (targeted `pytest` runs, manual checks) happens
after implementing something, once or twice — not as a standing pre-check, and not looped.

## 3. Read `user-space/STATE.md`

If a big/long-running task is in progress, this is its live phase-by-phase log — treat it as the
actual current progress record, not just a stale hint. Still worth a quick sanity check against
live `git status` before continuing a task from where it left off.

## 4. Architecture

Already fully documented in this repo's `CLAUDE.md` — "Two codebases live here" (the
`kravein/` vs. Windows-original split) and "Architecture" (registry/safety/platform/audit). Read
those before touching code; this skill is about process, not re-explaining what's already
written there.

## 5. Self-improvement

Update this file directly when a session discovers a process mistake worth not repeating.
