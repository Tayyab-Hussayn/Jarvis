# SCHEDULING_AUDIT.md

Design record for the scheduled-jobs subsystem. Written before implementation
on 2026-09-06, amended afterwards with what testing actually found.

Status: **built**. See `kravein/jobs.py`, `kravein/skills/schedule.py`,
`systemd/kravein-jobs.{service,timer}` and `web/components/JobsView.tsx`.

---

## 1. Codebase audit

**Natural language.** There is exactly one NL parser and it is the LLM:

```
input → Agent.ask() → llm.LLM.complete() → tool_calls
      → registry.dispatch() → skill → safety.enforce() → result → repeat
```

`registry.py` builds every tool schema from the function signature and
docstring. No hand-written tool list exists anywhere. Three surfaces share the
pipeline: the REPL, the web socket, and the daemon.

**Confirmers are per-surface and thread-local.** The REPL prompts a terminal,
the web blocks a worker on `events.Session.confirm`, and the daemon supplies
**none** — so background requests cannot approve their own destructive actions.
That policy is what a scheduled job has to inherit.

**Background work.** `server.py` (asyncio, only during `kravein serve`),
`daemon.py` (threading socketserver, only during `kravein daemon`), plus ad-hoc
daemon threads with a `threading.Event` stop flag — `gentle.py` is the
reference. **None of these runs continuously**; no user service was installed.

**Persistence.** No database. Small JSON files written atomically
(`tmp.write_text()` → `tmp.replace()`, as in `skills/memory.py`), plus
append-only JSONL for history (`audit.py`). All XDG.

**Prior art.** `set_reminder` already scheduled notifications through
`systemd-run --on-active=`. Those are *transient* units, which do not survive a
reboot — a reminder set for the morning vanished if the machine restarted
overnight. So this was an extension with a bug to fix, not a greenfield build.

---

## 2. OpenClaw research

Sources: [docs](https://openclaw.cc/en/automation/cron-jobs) ·
[Team 400](https://team400.ai/blog/2026-04-openclaw-cron-scheduled-ai-agent-jobs) ·
[open-claw.bot](https://open-claw.bot/docs/tools/automation/cron-jobs/)

**Correction to the brief:** OpenClaw does **not** convert prose to cron.
Schedules are given explicitly (`--at`, `--every`, `--cron` + `--tz`), parsed by
**croner**. The NL step is the agent calling a job-creation tool.

- **Persistence** — SQLite, shared with the rest of its state. Legacy
  `~/.openclaw/cron/jobs.json` migrated via `openclaw doctor --fix`.
- **Missed runs** — **no catch-up.** Overdue jobs are rescheduled *forward* on
  startup, explicitly to avoid a backlog.
- **Retries** — one-shot: 3 attempts, `[60s, 120s, 300s]`, transient errors
  only; recurring: exponential 30s → 60min, reset after success. Gated by
  `cron.retry.retryOn: ["rate_limit","overloaded","network","server_error"]`.
- **Timezones** — no tz → host tz; `--at` without tz → UTC; `--cron --tz` →
  that zone. Top-of-hour jobs staggered up to 5 min unless `--exact`.
- **Execution** — a job stores a *prompt*; firing spins up an agent run, in
  either the main session or an isolated one.
- **Delivery** — `announce` (fallback-deliver final text), `webhook`, or
  `none`. Suppressed if the agent already sent to the target.

---

## 3. Comparative analysis

**Borrowed directly:** store a prompt not code; isolated session per run;
reschedule-forward instead of catch-up; retry only on transient errors (we
already classify these in `llm._is_quota_error`); run history as JSONL.

**Deliberately different:**

| OpenClaw | Here | Why |
|---|---|---|
| SQLite | `jobs.json` | The whole persistence layer is atomic JSON. A database for one feature is the foreign architecture we were told to avoid. |
| Always-on gateway owns the trigger | systemd owns it | **We have no always-on process.** This is the binding constraint. |
| Slack/Telegram/webhook delivery | notification, speech, web push | `notify.send()` is the only one that works with nothing running, so it is the default rather than a fallback. |
| croner | croniter | JS vs Python. |

---

## 4. Candidates, and what was chosen

- **A — one systemd timer per job.** Maximum robustness, but N unit files, a
  `daemon-reload` per mutation, and two sources of truth that drift.
- **B — in-process scheduler in `kravein daemon`.** Cleanest architecture, one
  source of truth — and it only works if the daemon is running, which it was
  not. A missed reminder because a service was down is a silent, trust-
  destroying failure.
- **C — JSON store + one minutely tick (CHOSEN).** All of A's robustness with
  one unit file. Retry state persists across reboots for free. Adding a job is
  a JSON write, which makes it testable and trivial to expose in the UI.

Cost of C: up to 60s of imprecision, irrelevant for reminders.

---

## 5. What implementation and testing actually found

Recorded because these were not in the original analysis.

**A confirmation bypass, and it was not ours.** `registry.dispatch()` harvested
a `confirm` flag from the *model's own arguments* and OR'd it into
`pre_confirmed`, which short-circuits `safety.enforce()` before the
no-confirmer check. A scheduled job refused on `delete_path` retried as
`run_command "rm -f …"` with `confirm=true` and deleted the file, unattended.
This affected the daemon too. Fixed: a `confirm` keyword from a trusted caller
still works, the model's own is ignored and audited.

**Refusals were not remembered.** `enforce()` raised at the `confirmer is
_deny` branch *before* recording the target, so the per-turn denial memory —
built precisely to catch a retry under a different verb — never saw the first
refusal.

**A refused confirmation is not a failed turn.** The agent answers normally,
explaining it could not act, which reads as success. For an unattended job that
is the wrong signal, so `run_job` watches tool results and reports it failed.

**Cold start is real.** Routing the tick through `cli.py` costs ~250ms because
that module imports the Agent, LLM facade and registry at import time. A
dedicated `python -m kravein.jobs` entry point costs ~50ms. Over 1,440 ticks a
day that is the difference between 6 minutes of CPU and 1. Asserted by a test.

**You cannot flock a file you replace.** `jobs.json` is written by atomic
rename, so a lock on it guards a dead inode and two ticks can both hold it.
`jobs.lock` is never replaced.

**Pid liveness is not pid identity.** Linux recycles pids; `os.kill(pid, 0)` on
a number recorded an hour ago can succeed against something unrelated, pinning
a job in `running` for ever. `/proc/<pid>/cmdline` must contain `kravein`.

**The clock belongs in the messages, not the system prompt.** The system prompt
is the cached prefix; a value changing every second invalidates it on every
request. `Agent._with_context()` appends it as a trailing `[context]` message
instead, and it is never stored in history. This replaced a hand-rolled date
grammar that was a second NL parser and would have failed on "next Tuesday".
