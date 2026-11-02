---
type: plan
owner: orchestrator
updated: 2026-11-01
---

# Orchestrator + sub-agents (Kravein v2 architecture)

Owner-approved 2026-11-01. Audit and research: https://claude.ai/artifact/N9v8J4xPi2w22dteAvoj2S (summary below). Decision: **no framework migration**. Evolve the current loop; keep registry, safety, platform, audit, LLM failover, voice, web, phone, jobs, routines.

## Owner requirements (verbatim intent)
1. **One Orchestrator** the owner talks to (text, voice, phone). It **handles fast, everyday tasks itself** (workspaces/windows, open/close apps, phone control, volume/brightness/media, reminders, memory, quick lookups). It **delegates long tasks** that need thinking or planning to specialist sub-agents, and it may delegate any task on its own judgement.
2. **Sub-agents**: `system-ops` (deep system work: packages, services, processes, logs, shell, files in bulk, terminal, mouse/keyboard, multi-step browser), `research` (research, comparison, planning, documents), `growth` (business development: leads, outreach drafts, market research; never sends anything without owner approval), `reviewer` (read-only; checks a sub-agent's work against the audit log and the real effect and sends it back once). Each has an identity, a purpose, its own `SKILL.md`, its own tool set and its own persistent context.
3. The Orchestrator gets **short reports** back, not raw tool output, so its context stays clean. It proposes next steps with reasons ("sir, do this, not that"); **major decisions wait for owner approval**.
4. **Reuse what exists, no parallel logic.** Daily routines, reminders and jobs are used through the existing `routines.py` / `jobs.py` / notify skills — no agent gets its own scheduling or reminder logic. Share code and components wherever it is safe; do not share where it creates a conflict risk (e.g. mutable per-session state, the thread-local confirmer).
5. **Clean, production-grade, easy to extend**: adding an agent = adding a profile folder, no core change (same principle as `@skill`).
6. **Intelligence over micro-skills.** The agent should reason about *how* to do a task (edit a file, work inside a sub-directory) with a few capable, general tools rather than many tiny ones. **Consolidate** near-duplicate skills into one skill with an `action` parameter (like `phone_flashlight(on)`) — fewer tools = fewer tokens and a cleaner context.

## Architecture
```
Owner (text / voice / phone) -> Orchestrator (fast tools + delegate + propose + memory)
   delegate(agent, task) -> sub-agent turn -> bounded report back
   every tool call, any agent: registry.dispatch -> safety.enforce -> audit
```
- `kravein/agents/<name>/SKILL.md`: YAML frontmatter (`name`, `purpose`, `tools` = categories and/or skill names, `model` optional, `report_max_chars`) + body (how to do this job excellently). Loaded by one generic loader. The Orchestrator is a profile too.
- One `Agent` class parameterised by a profile. A profile can only call the tools in its set — **enforced in code** (dispatch refuses others), not by prompt.
- Per-agent persistent history (SQLite under `config.STATE_DIR`), token-budgeted, with a rolling summary on overflow; survives restart.
- `delegate` runs the sub-agent on the same thread inside the caller's `confirmer_scope`: the **owner's confirmer passes through**, nothing gains a way to approve itself, and every failure path still denies. Confirmation cards name the sub-agent. A decline inside a sub-agent ends the delegated task.
- Escalation in code: if the Orchestrator has used N rounds (default 3) on a task without finishing, it hands the task to the right sub-agent.
- `propose`: a proposals queue (approve/decline from web/phone/voice); major actions run only after approval, enforced by a state machine.
- Live voice later talks to the Orchestrator only (closes the voice-parity gap).

## Orchestrator's own tools (≈75)
windows (all), apps open/close/list/search, phone (all), settings (volume/brightness/media/wifi/bluetooth/dark mode), notify (reminders, notifications, speak), memory, weather/battery/system_status/disk/network, open_url/search_and_open/web_search, lock/suspend (shutdown/reboot with confirmation), routine_status/complete_step/skip_step, list_jobs/schedule_status, clipboard, screenshot, delegate, propose.

## Phases (one PR each unless noted)
1. engine — profiles + SKILL.md loader + per-agent persistent history + token budget/summary + tool-result truncation. (#132)
1b. skills — skill consolidation + general file editing, in parallel with 1. (#133)
2. engine — `delegate`, report contract, Orchestrator profile, escalation, confirmer pass-through (safety-critical).
3. skills — sub-agent profiles: system-ops, research, reviewer (SKILL.md + small eval set each).
4. engine + web + android — proposals queue + approvals + daily proactive review job (uses existing jobs).
5. skills — growth agent.
6. engine — Live voice on the Orchestrator.
7. optional — LangGraph spike behind `delegate` if checkpoint/time-travel is needed.
