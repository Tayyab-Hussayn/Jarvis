# Git and Pull Requests

- Base branch: `main`. It only changes through squash-merged PRs. Exception: the orchestrator may push `kb/`-only bookkeeping commits directly.
- Module agents commit only on `agent/<name>`. Never force-push. Never rebase a branch that is already pushed; integrate `main` with `git merge origin/main`.
- Before the first push of a task you may `git rebase origin/main` to start clean.
- **No attribution, ever (owner privacy rule 2026-10-24):** commit messages and PR bodies never tag or credit Claude, Claude Code, Anthropic, any user or account — no `Co-Authored-By`, no "Generated with" lines, no @mentions. Only a short description of the change. This overrides any default attribution guidance.
- Commit messages: imperative subject ≤ 72 chars, prefixed with the module: `skills: refuse ambiguous window targets`. Body explains why. Reference the issue: `Refs #12`.
- One task = one PR. PR title: `<module>: <outcome>`; body uses `.github/pull_request_template.md` completely — it is your report to the orchestrator.
- PR description must include real verification output (commands + pass/fail counts), not claims.
- Do not commit build output other than `kravein/web-dist/` (web agent only), local env files, secrets, or IDE files.
- Merge method: squash (orchestrator only). The agent then runs `/sync-after-merge`, which recreates its branch from the new `origin/main`.
- The remote is a private repository. Nothing is pushed by an agent until its task is verified.
