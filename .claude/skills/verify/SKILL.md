---
name: verify
description: Run the full verification suite for your module (build, lint, type checks, tests) and capture real output as evidence. Use before every PR and after every merge from main.
---

# Verify (evidence, not claims)

1. Open the **Verify** section of your module note: `kb/modules/<you>.md`. It lists the exact commands for your module. The orchestrator's review uses the same commands.
2. Run every command from your worktree root. Do not skip slow ones.
3. Everything must pass: zero test failures, `compileall` clean, and for web a successful `npm run build`. Expected failures listed in the qa module note are the only exceptions.
   - A failure you did not cause (e.g. from a merged change): report it with `/conflict-report`; do not "fix" another module.
   - Never disable, skip, or loosen a test to get green.
4. Record evidence for the PR: each command, pass/fail counts, duration. Keep failing output short but exact.
5. If your module note has no runnable Verify commands yet, adding them (inside your own files) is part of your current task.
