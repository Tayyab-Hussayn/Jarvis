---
paths:
  - "kravein/jobs.py"
  - "kravein/routines.py"
  - "kravein/delivery.py"
  - "kravein/timeutil.py"
  - "kravein/skills/schedule.py"
  - "kravein/skills/routines.py"
  - "systemd/**"
---

# Hard-won knowledge: Scheduler: jobs and routines

Moved verbatim from `CLAUDE.md`. Each paragraph is a bug that already happened; do not rediscover it.

**A refused confirmation is not a failed turn, and a scheduled job must treat it as one.** The
agent answers normally, explaining it could not act — which reads as success to anything watching
`turn.error`. For a job the user is not sitting in front of, that is the wrong signal: the whole
point is finding out it did not happen. `jobs.run_job()` watches tool results for a refusal and
reports the run as failed.

**Check pid identity, not just liveness, before trusting a recorded pid.** Linux recycles pids, so
`os.kill(pid, 0)` on a number recorded an hour ago can succeed against something entirely
unrelated. `jobs._pid_alive()` reads `/proc/<pid>/cmdline` and requires "kravein" in it; otherwise
a crashed runner leaves its job pinned in `running` for ever — never reaped, never retried, and
invisible unless somebody reads the store by hand.

**The tick must not import the agent.** A systemd timer runs `python -m kravein.jobs tick` every
minute. Routing that through `cli.py` costs ~250ms because that module imports the Agent, the LLM
facade and the registry at import time; the dedicated entry point costs ~50ms and pulls the heavy
machinery in only once a job is due. Measured, and asserted by a test that fails if anything heavy
appears in `sys.modules` after an empty tick. An import added at the top of `jobs.py` is paid 1,440
times a day.

**Lock a file you do not replace.** `jobs.json` is written with the usual atomic tmp+rename, which
swaps the inode — a `flock` held on it guards a path that no longer exists, and two ticks can both
think they hold it. `jobs.lock` is never replaced, so it is a stable thing to lock. A job is also
marked `running` *before* the Agent is built, so the next minute's tick skips it; and `running`
records a pid, so a crashed run is reaped instead of wedging the job for ever.

**`zoneinfo` costs 5.2ms, so do not ask it anything the store already knows.** The routine engine
first decided "has the day rolled over?" by converting the current instant to local wall-clock —
which loads the timezone database on every one of the 1,440 ticks a day, to answer a question
whose answer changes at 04:00. The day instance now caches its own end as a UTC instant
(`ends_at`), and the steady-state pass does no timezone arithmetic at all: **5.0ms → 0.9ms**,
measured against a populated 30-step store. A test fails if `zoneinfo` reappears in `sys.modules`
after a steady-state pass. The general rule: everything stored is UTC, and local wall-clock is
touched exactly once, when a day is opened.

**Measure the pass that does nothing, separately from the pass that does something.** The first
benchmark of that same tick said the routine pass added 14.3ms and looked like a serious
regression. It was measuring `notify-send` being spawned, because the fixture had steps genuinely
due — real delivery, not engine cost. The engine was 0.37ms throughout. A scheduler benchmark
that does not separate "found nothing" from "found something and told the user" is measuring the
notification daemon.

**"Never asked" is not "asked and failed."** A routine created at 4pm reported its 07:00 step as
missed before that step had ever existed, and a laptop switched off all morning manufactured a
morning of failures for reminders nobody was shown. A day opened after a step was already
unreachable starts it `skipped`, which keeps it out of the score entirely. The test is whether the
**window has closed**, not whether the due time has passed: the first version compared against the
due time, and a step scheduled for the current minute was stood down by the tick that ran two
seconds later. That one only showed up by running it for real — the suite was green.

**Expiry and unlocking have to run to a fixed point.** One pass of each is not enough in either
order. Expiring a step fails the chain behind it, and unlocking a step can immediately expire it
when the tick is running late. With a single pass a broken chain dribbles out one state change per
minute and the panel shows `locked` for a step that can never run. `routines._settle` loops until
nothing moves, bounded by the chain length.
