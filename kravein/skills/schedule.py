"""Scheduled jobs and reminders.

The store and the tick live in :mod:`kravein.jobs`; this is the surface the
model calls. A systemd user timer runs the tick every minute, so jobs fire
whether or not Kravein is running — there is no always-on process here, and a
reminder that only works while the assistant happens to be open is not a
reminder.

Two kinds of job, and the difference matters:

* ``notify`` just shows a message. No model call, so it cannot fail, cost
  anything, or do something unexpected at 3am. This is what a reminder is.
* ``agent`` runs a full turn through :meth:`Agent.ask`, tools and all.

An ``agent`` job runs unattended, so it runs with ``confirmer=None`` — the same
policy as the daemon. `safety.enforce()` refuses destructive actions outright
rather than approving them. A job that tries to delete something fails loudly
and says why; it does not get to authorise itself because nobody is watching.
"""

from __future__ import annotations

from .. import audit, config, jobs
from ..registry import skill
from ..safety import Danger, describe_targets, enforce


def _describe(job: dict) -> str:
    when = jobs.parse_iso(job.get("next_run", ""))
    local = (
        when.astimezone(jobs._zone(job.get("timezone", ""))).strftime("%a %d %b %H:%M")
        if when else "—"
    )
    repeat = f" · repeats {job['cron']}" if job.get("cron") else ""
    state = job.get("state", "")
    mark = "" if state == "pending" else f" [{state}]"
    return f"{job['id']}  {local}{repeat}{mark}  {job.get('prompt', '')[:60]}"


@skill(category="schedule", danger=Danger.MODERATE)
def schedule_job(
    prompt: str,
    when: str = "",
    repeat: str = "",
    kind: str = "notify",
    title: str = "Kravein",
    speak: bool = False,
) -> str:
    """Schedule something to happen later, once or on a repeating schedule.

    Fires even if Kravein is closed, because a systemd timer drives it.

    Args:
        prompt: For kind=notify, the message to show. For kind=agent, the
            request to run — write it exactly as the user would say it.
        when: An absolute ISO 8601 timestamp, e.g. "2026-09-07T17:00".
            Resolve any relative phrasing yourself — "tomorrow", "next
            Tuesday", "in two weeks" — using the current date and time given
            in the context, and pass the resulting absolute timestamp. A plain
            duration ("20m", "1h30m", "2d") is also accepted. Leave empty when
            using repeat.
        repeat: A 5-field cron expression for a recurring job, e.g.
            "0 9 * * *" for every day at 9am, "0 9 * * 1" for Mondays.
        kind: "notify" to just show a message — use this for reminders. "agent"
            to actually run the request with tools when it fires.
        title: Heading on the notification.
        speak: Also say it out loud when it fires.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return "Nothing to schedule — say what the job should do."

    kind = (kind or "notify").strip().lower()
    if kind not in jobs.VALID_KINDS:
        return f"kind must be 'notify' or 'agent', not {kind!r}."

    repeat = (repeat or "").strip()
    tz_name = jobs.local_timezone()

    if repeat:
        if not jobs.valid_cron(repeat):
            return (
                f"'{repeat}' is not a valid cron expression. Use five fields: "
                "minute hour day-of-month month day-of-week, e.g. '0 9 * * *'."
            )
        moment = None
    else:
        moment = jobs.parse_when(when, tz_name)
        if moment is None:
            return (
                f"{when!r} is not a time I can read. Work out the absolute "
                "date and time yourself from the current time in the context, "
                "then pass it as ISO 8601 — e.g. '2026-09-07T17:00'. A plain "
                "duration like '20m' or '1h30m' also works."
            )
        if moment <= jobs.now_utc():
            return "That time has already passed."

    # An agent job runs tools unattended on a schedule, which is a bigger
    # commitment than a reminder — it is confirmed like one.
    if kind == "agent":
        from ..safety import Verdict

        verdict = Verdict(
            danger=Danger.DESTRUCTIVE,
            reasons=[
                f"schedules an unattended agent turn: {prompt[:120]!r}. It will "
                "run with tools and no confirmer, so destructive actions will "
                "be refused rather than approved"
            ],
        )
        enforce(
            verdict, f"schedule an unattended job: {prompt[:60]}",
            skill="schedule_job", source="local",
            targets=describe_targets(f"job:{prompt[:40]}"),
        )

    try:
        job = jobs.add(
            prompt=prompt, when=moment, cron=repeat, kind=kind,
            title=title or "Kravein", tz_name=tz_name, speak=speak,
        )
    except (ValueError, RuntimeError) as exc:
        return f"Could not schedule that: {exc}"

    audit.record("schedule.added", id=job["id"], kind=kind, next_run=job["next_run"])
    due = jobs.parse_iso(job["next_run"])
    local = due.astimezone(jobs._zone(tz_name)).strftime("%a %d %b at %H:%M") if due else "?"

    warning = "" if _timer_active() else (
        "\n\nWarning: the scheduled-jobs timer is not running, so this will not "
        "fire. Start it with:  systemctl --user enable --now kravein-jobs.timer"
    )
    if repeat:
        return f"Scheduled {job['id']}, repeating ({repeat}). Next: {local}.{warning}"
    return f"Scheduled {job['id']} for {local}: {prompt[:70]}{warning}"


@skill(category="schedule", danger=Danger.SAFE)
def list_jobs(include_finished: bool = False) -> str:
    """List scheduled jobs and reminders.

    Args:
        include_finished: Also show jobs that already ran or failed.
    """
    every = jobs.all_jobs()
    if not include_finished:
        every = [j for j in every if j.get("state") in ("pending", "running")]
    if not every:
        return "Nothing scheduled."

    every.sort(key=lambda j: j.get("next_run") or "")
    lines = [f"{len(every)} job(s):"]
    lines += [f"  {_describe(j)}" for j in every]
    if not _timer_active():
        lines.append("")
        lines.append("The tick timer is NOT running — none of these will fire.")
        lines.append("  systemctl --user enable --now kravein-jobs.timer")
    return "\n".join(lines)


@skill(category="schedule", danger=Danger.MODERATE)
def cancel_job(job_id: str = "", _source: str = "local", _confirm: bool = False) -> str:
    """Cancel a scheduled job, or all of them.

    Args:
        job_id: Which job, e.g. "j3". Leave empty to cancel everything pending.
    """
    job_id = (job_id or "").strip()
    if not job_id:
        outstanding = [j for j in jobs.all_jobs() if j.get("state") in ("pending", "running")]
        if not outstanding:
            return "Nothing scheduled to cancel."
        from ..safety import Verdict

        enforce(
            Verdict(
                danger=Danger.DESTRUCTIVE,
                reasons=[f"cancels all {len(outstanding)} scheduled job(s)"],
            ),
            f"cancel all {len(outstanding)} scheduled jobs",
            skill="cancel_job", source=_source, pre_confirmed=_confirm,
            targets=describe_targets("jobs:all"),
        )

    try:
        removed = jobs.cancel(job_id)
    except RuntimeError as exc:
        return f"Could not cancel: {exc}"

    if not removed:
        return f"No pending job matches {job_id!r}." if job_id else "Nothing to cancel."
    audit.record("schedule.cancelled", id=job_id or "all", count=removed)
    return f"Cancelled {removed} job(s)."


@skill(category="schedule", danger=Danger.SAFE)
def job_history(limit: int = 15) -> str:
    """Show what scheduled jobs recently did, including failures.

    Args:
        limit: How many entries to show.
    """
    import json

    try:
        lines = jobs.HISTORY.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "No job history yet."
    if not lines:
        return "No job history yet."

    out = [f"Last {min(limit, len(lines))} job events:"]
    for raw in lines[-max(1, min(int(limit), 200)):]:
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        stamp = str(entry.get("ts", ""))[:19].replace("T", " ")
        detail = entry.get("error") or entry.get("next_run") or ""
        out.append(
            f"  {stamp}  {entry.get('event', ''):10} {entry.get('id', ''):5} {str(detail)[:60]}"
        )
    return "\n".join(out)


@skill(category="schedule", danger=Danger.SAFE)
def schedule_status() -> str:
    """Whether scheduled jobs will actually fire, and where they are stored."""
    active = _timer_active()
    lines = ["Scheduled jobs"]
    lines.append(f"  tick timer : {'running' if active else 'NOT RUNNING'}")
    lines.append(f"  store      : {jobs.STORE}")
    lines.append(f"  history    : {jobs.HISTORY}")
    lines.append(f"  timezone   : {jobs.local_timezone()}")
    lines.append(
        f"  grace      : {config.get('jobs.grace_seconds', jobs.DEFAULT_GRACE_SECONDS)}s "
        "— a job later than this is skipped rather than fired late"
    )
    counts: dict[str, int] = {}
    for job in jobs.all_jobs():
        counts[job.get("state", "?")] = counts.get(job.get("state", "?"), 0) + 1
    lines.append("  jobs       : " + (", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "none"))
    if not active:
        lines.append("")
        lines.append("Nothing will fire until the timer runs:")
        lines.append("  systemctl --user enable --now kravein-jobs.timer")
    return "\n".join(lines)


def _timer_active() -> bool:
    """Is the tick timer actually going to run?

    Worth checking on every path that schedules something. A job written to a
    store that nothing reads is the most user-hostile failure this feature has:
    it looks like it worked and then silently never happens.
    """
    from ..platform import shell

    if not shell.have("systemctl"):
        return False
    result = shell.run(
        ["systemctl", "--user", "is-active", "kravein-jobs.timer"], timeout=8
    )
    return result.stdout.strip() == "active"


@skill(category="schedule", danger=Danger.MODERATE)
def pause_job(job_id: str, resume: bool = False) -> str:
    """Pause a scheduled job, or resume a paused one.

    A paused job keeps its schedule and history but never fires. This is
    deliberately not the same as cancelling: "not now" and "never again" are
    different, and the only way to quieten a recurring job otherwise would be
    to delete it and rebuild it from memory.

    Args:
        job_id: Which job, e.g. "j3".
        resume: True to resume a paused job instead of pausing it.
    """
    job_id = (job_id or "").strip()
    if not job_id:
        return "Say which job to pause, e.g. j3."
    try:
        state = jobs.set_paused(job_id, not resume)
    except RuntimeError as exc:
        return f"Could not change {job_id}: {exc}"
    if not state:
        return f"No job {job_id!r} that can be paused or resumed."
    audit.record("schedule.paused", id=job_id, state=state)
    return f"{job_id} is now {state}."


@skill(category="schedule", danger=Danger.MODERATE)
def edit_job(
    job_id: str,
    prompt: str = "",
    when: str = "",
    repeat: str = "",
    title: str = "",
) -> str:
    """Change an existing scheduled job.

    Only the fields you pass are changed, so editing the message does not
    require restating the schedule. Editing a finished job puts it back in
    play.

    Args:
        job_id: Which job, e.g. "j3".
        prompt: New message or request.
        when: New absolute ISO 8601 time, e.g. "2026-09-07T17:00". Resolve any
            relative phrasing yourself from the current time in the context.
        repeat: New 5-field cron expression. Leave empty to keep the current
            one — to turn a repeating job into a one-shot, pass `when`
            instead, which clears the repeat.
        title: New notification heading.
    """
    job_id = (job_id or "").strip()
    if not job_id:
        return "Say which job to edit, e.g. j3."

    existing = next((j for j in jobs.all_jobs() if j.get("id") == job_id), None)
    if existing is None:
        return f"No job {job_id!r}."

    moment = None
    if when.strip():
        moment = jobs.parse_when(when, existing.get("timezone", ""))
        if moment is None:
            return (
                f"{when!r} is not a time I can read. Pass an absolute ISO 8601 "
                "timestamp such as '2026-09-07T17:00'."
            )

    try:
        updated = jobs.edit(
            job_id,
            prompt=prompt.strip() or None,
            when=moment,
            cron=repeat.strip() or None,
            title=title.strip() or None,
        )
    except (ValueError, RuntimeError) as exc:
        return f"Could not edit {job_id}: {exc}"

    if updated is None:
        return f"No job {job_id!r}."
    audit.record("schedule.edited", id=job_id)
    return f"Updated {job_id}. Next run: {updated.get('next_run', '?')}."
