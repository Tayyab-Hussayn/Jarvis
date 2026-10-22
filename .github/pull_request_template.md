<!-- This PR description is the agent's report to the orchestrator. Fill every section. -->

## Task
Closes #<!-- issue -->  ·  Module: <!-- engine | skills | web | qa -->

## What changed and why
<!-- 3–8 bullets. Behavior, not file lists. -->

## Verification evidence
<!-- Paste the exact commands from kb/modules/<module>.md → Verify, with pass/fail counts. -->
```
```

## Tests added / changed
<!-- Which behaviors and failure paths are now covered. -->

## Docs / hard-won-knowledge conformance
<!-- CLAUDE.md sections or audit findings this follows (e.g. AGENT_AUDIT A1-1). Any deviation must be explained (and pre-approved). -->

## Guard checks
- [ ] `python3 .claude/team/bin/check_branch.py` → OK (ownership + no overlap with open PRs)
- [ ] All changed files are inside my module's ownership
- [ ] No secrets or user content logged; destructive paths still go through `safety.enforce()`; no subprocess outside `platform/shell.py`
- [ ] `kb/modules/<module>.md` updated (structure, interfaces, verify, gotchas) if they changed

## Risks, limitations, follow-ups
<!-- Honest list. "None" only if truly none. -->
