# Engineering Bar (all modules)

- A task is done only when its acceptance criteria pass with automated tests. Compiling is not evidence.
- New behavior → new tests. Bug fix → regression test first. Never delete or weaken a test to get green.
- Follow `CLAUDE.md` "Hard-won knowledge" exactly; if a rule there seems wrong, stop and report — do not silently deviate.
- Safety is not advisory: state-changing skills call `safety.enforce()` themselves; nothing is allowed to approve its own destructive action; every confirmation failure path denies. No subprocess outside `platform/shell.py`.
- No secrets in code or in the audit log. Do not touch the Windows-original tree (frozen).
- Prefer the smallest correct change. No speculative abstractions, no unrelated refactors in a task PR.
- Comments explain *why*, especially a platform quirk; name the failure it prevents.
- New third-party dependency → justify it in the PR (maintenance, license, size, alternatives).
- Library/API usage: when unsure of a current API, look it up (context7 MCP or official docs) instead of guessing.
- Verify light, don't loop: run the specific tests you touched plus your module's Verify commands once, then report.
