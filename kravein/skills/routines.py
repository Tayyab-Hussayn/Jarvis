"""Health and habits routines — the surface the model calls.

The store and the state machine live in :mod:`kravein.routines`; this is the
tool schema. Routines ride the same minutely systemd timer as scheduled jobs,
so they nag whether or not Kravein is open.

**The model parses language; it does not own the state.** Every skill here
takes structured arguments — a clock time, a step id, a number of minutes — and
the model's job is turning "wake me at seven, breakfast half an hour after I'm
actually up, and don't let me skip the meds" into those arguments. There is no
prose parser in the engine, for exactly the reason there is no second date
grammar in `jobs.parse_when`: two natural-language parsers competing is worse
than one.

**Reminder wording is generated here, once.** Phrasings are written into the
step when it is created or edited, and the tick rotates through them. The tick
must never call a model: a step nagging every ten minutes would otherwise be an
agent turn every ten minutes, forever, on a timer, against a metered provider.
"""

from __future__ import annotations

from .. import audit, routines
from ..registry import skill
from ..safety import Danger, Verdict, describe_targets, enforce


def _routine_or_error(routine_id: str, snapshot: dict) -> dict | None:
    for routine in snapshot["routines"]:
        if routine["id"] == routine_id:
            return routine
    return None


@skill(category="routines", danger=Danger.MODERATE)
def add_routine(
    name: str,
    day_start: str = "04:00",
    days: str = "",
    timezone: str = "",
) -> str:
    """Create a daily routine — a named, ordered chain of habits to track.

    A routine on its own does nothing; add steps to it with `add_routine_step`.

    Args:
        name: What to call it, e.g. "Morning" or "Evening wind-down".
        day_start: When the routine's day rolls over, as HH:MM local. The
            default of 04:00 means a step at 01:00 still counts towards the
            previous day rather than starting a new one. Rarely worth changing.
        days: Which weekdays it runs, as comma-separated numbers where 0 is
            Monday and 6 is Sunday, e.g. "0,1,2,3,4" for weekdays only. Leave
            empty for every day.
        timezone: IANA zone name. Leave empty to use this machine's.
    """
    try:
        wanted = [int(d) for d in days.replace(" ", "").split(",") if d] if days else None
        routine = routines.add_routine(
            name, day_start=day_start, tz_name=timezone, days=wanted
        )
    except ValueError as exc:
        return f"Could not create that routine: {exc}"

    audit.record("routines.added", id=routine["id"], name=routine["name"])
    return (
        f"Created routine {routine['id']} — {routine['name']}, "
        f"day starts {routine['day_start']} {routine['timezone']}. "
        f"Add steps with add_routine_step."
    )


@skill(category="routines", danger=Danger.MODERATE)
def add_routine_step(
    routine_id: str,
    label: str,
    at: str = "",
    after_step: str = "",
    after_minutes: int = 0,
    requires: str = "",
    window_minutes: int = 60,
    points: int = 10,
    insist: bool = False,
    messages: str = "",
    nag_every_minutes: int = 10,
    nag_max: int = 3,
) -> str:
    """Add one habit to a routine, optionally chained behind another.

    A step has either a fixed time (`at`) or an offset from another step's
    actual completion (`after_step` + `after_minutes`) — never both. The second
    form is what makes a real routine work: breakfast thirty minutes after the
    user genuinely got up, not thirty minutes after they were supposed to.

    A step listed in `requires` must be confirmed done before this one can
    become due, be reminded about, or be marked done. Until then it is locked.

    Args:
        routine_id: Which routine, e.g. "r1".
        label: The habit itself, short — "Breakfast", "Meds", "Walk".
        at: A fixed clock time as HH:MM local, e.g. "07:00". Leave empty when
            using after_step.
        after_step: The id of the step this one follows, e.g. "s1". Its due
            time is only known once that step is actually confirmed done.
        after_minutes: How long after that step completes this one is due.
        requires: Step ids that must be done first, comma-separated. The
            after_step is added automatically; use this for extra
            prerequisites.
        window_minutes: How long the user has before the step counts as
            missed. 0 means it stays open until the routine's day ends.
        points: What it is worth towards the daily score.
        insist: For steps that must not be quietly missed. Reminders start one
            rung louder and use a critical, sticky notification. It never
            locks the screen, closes anything, or blocks the machine.
        messages: Reminder wordings, separated by "|". Write two or three
            natural variations so the reminders do not read identically every
            time; the tick rotates through them. Falls back to the label.
        nag_every_minutes: Gap between reminders while the step is open.
        nag_max: How many reminders before it gives up and waits for the
            window to close. 0 means never remind.
    """
    try:
        step = routines.add_step(
            routine_id,
            label=label,
            at=at.strip(),
            after_step=after_step.strip(),
            after_minutes=after_minutes,
            requires=[r for r in requires.replace(" ", "").split(",") if r],
            window_minutes=window_minutes,
            points=points,
            insist=insist,
            messages=[m.strip() for m in messages.split("|")] if messages else [],
            nag={"every_minutes": nag_every_minutes, "max": nag_max, "escalate": True},
        )
    except ValueError as exc:
        return f"Could not add that step: {exc}"

    audit.record("routines.step_added", routine=routine_id, step=step["id"])
    when = f"at {step['at']}" if step.get("at") else (
        f"{step['after']['minutes']}m after {step['after']['step']}"
    )
    note = ""
    snapshot = routines.snapshot()
    routine = _routine_or_error(routine_id, snapshot)
    if routine:
        warning = routines.dst_note(routine, step, routine["today"]["date"])
        note = f" Note: {warning}" if warning else ""
    return (
        f"Added {step['id']} — {step['label']}, {when}, worth {step['points']}pt. "
        f"It starts counting from the next routine day.{note}"
    )


@skill(category="routines", danger=Danger.SAFE)
def routine_status(routine_id: str = "") -> str:
    """Show today's routines: what is done, what is next, and the score.

    Args:
        routine_id: Limit to one routine, e.g. "r1". Leave empty for all.
    """
    snapshot = routines.snapshot()
    wanted = [r for r in snapshot["routines"] if not routine_id or r["id"] == routine_id]
    if not wanted:
        return "No routines set up yet." if not routine_id else f"No routine {routine_id!r}."

    lines: list[str] = []
    for routine in wanted:
        day = routine["today"]
        if day.get("rest"):
            lines.append(f"{routine['id']}  {routine['name']} — rest day.")
            continue
        # Against `available`, not `total`: measuring the morning against the
        # whole day's points reads as failure before the day has happened.
        lines.append(
            f"{routine['id']}  {routine['name']}  "
            f"{day['score']}/{day['available']} pts so far (day total {day['total']})  "
            f"streak {routine['streak']}"
        )
        lines.extend(routines.describe(routine))
    return "\n".join(lines)


@skill(category="routines", danger=Danger.SAFE)
def complete_step(routine_id: str, step_id: str) -> str:
    """Confirm a habit is done, which unlocks whatever comes after it.

    Refused if the step is still locked behind something the user has not
    confirmed yet — that refusal is the point of the sequencing, so it says
    what is blocking rather than quietly doing nothing.

    Args:
        routine_id: Which routine, e.g. "r1".
        step_id: Which step, e.g. "s2".
    """
    try:
        result = routines.resolve(routine_id, step_id, state="done", source="model")
    except ValueError as exc:
        return str(exc)
    audit.record("routines.completed", routine=routine_id, step=step_id)
    return (
        f"Marked {step_id} done. Score {result['score']}/{result['available']} "
        f"so far today."
    )


@skill(category="routines", danger=Danger.SAFE)
def skip_step(routine_id: str, step_id: str) -> str:
    """Stand a habit down for today without it counting as missed.

    Anything waiting on it is stood down too — the chain cannot continue past a
    step that never happened.

    Args:
        routine_id: Which routine, e.g. "r1".
        step_id: Which step, e.g. "s2".
    """
    try:
        result = routines.resolve(routine_id, step_id, state="skipped", source="model")
    except ValueError as exc:
        return str(exc)
    audit.record("routines.skipped", routine=routine_id, step=step_id)
    return f"Skipped {step_id} for today. Score {result['score']}/{result['available']}."


@skill(category="routines", danger=Danger.MODERATE)
def edit_routine_step(
    routine_id: str,
    step_id: str,
    label: str = "",
    at: str = "",
    window_minutes: int = 0,
    points: int = 0,
    messages: str = "",
) -> str:
    """Change one step of a routine. Only the fields given are touched.

    Args:
        routine_id: Which routine, e.g. "r1".
        step_id: Which step, e.g. "s2".
        label: New name for the habit.
        at: New fixed clock time as HH:MM. Only for steps that have one.
        window_minutes: New window before it counts as missed.
        points: New value towards the daily score.
        messages: New reminder wordings, separated by "|".
    """
    fields: dict = {}
    if label:
        fields["label"] = label
    if at:
        fields["at"] = at
    if window_minutes:
        fields["window_minutes"] = window_minutes
    if points:
        fields["points"] = points
    if messages:
        fields["messages"] = [m.strip() for m in messages.split("|") if m.strip()]
    if not fields:
        return "Nothing to change — say what should be different."

    try:
        step = routines.edit_step(routine_id, step_id, **fields)
    except ValueError as exc:
        return f"Could not change that step: {exc}"
    audit.record("routines.step_edited", routine=routine_id, step=step_id,
                 fields=sorted(fields))
    return f"Updated {step['id']} — {step['label']}."


@skill(category="routines", danger=Danger.MODERATE)
def pause_routine(routine_id: str, resume: bool = False) -> str:
    """Pause a routine so it stops nagging, or resume a paused one.

    A paused routine keeps its steps, history and streak. Its day does not roll
    over while paused, so resuming picks up cleanly on the next tick.

    Args:
        routine_id: Which routine, e.g. "r1".
        resume: True to resume instead of pausing.
    """
    if not routines.set_enabled(routine_id, resume):
        return f"No routine {routine_id!r}."
    audit.record("routines.paused", id=routine_id, enabled=resume)
    return f"{routine_id} is now {'active' if resume else 'paused'}."


@skill(category="routines", danger=Danger.DESTRUCTIVE)
def delete_routine(routine_id: str, _source: str = "local", _confirm: bool = False) -> str:
    """Delete a routine and everything it tracks. The history log survives.

    Args:
        routine_id: Which routine, e.g. "r1".
    """
    snapshot = routines.snapshot()
    routine = _routine_or_error(routine_id, snapshot)
    if routine is None:
        return f"No routine {routine_id!r}."

    enforce(
        Verdict(
            danger=Danger.DESTRUCTIVE,
            reasons=[f"deletes the routine {routine['name']!r} and its "
                     f"{len(routine['steps'])} step(s)"],
        ),
        f"delete routine {routine['name']!r} ({routine_id}, "
        f"{len(routine['steps'])} steps, streak {routine['streak']})",
        skill="delete_routine", source=_source, pre_confirmed=_confirm,
        targets=describe_targets(f"routine:{routine_id}"),
    )

    routines.remove_routine(routine_id)
    audit.record("routines.deleted", id=routine_id, name=routine["name"])
    return f"Deleted {routine_id} — {routine['name']}."


@skill(category="routines", danger=Danger.SAFE)
def routine_history(limit: int = 14, routine_id: str = "") -> str:
    """Show how recent days went: score, what was missed, streak.

    Args:
        limit: How many days back.
        routine_id: Limit to one routine, e.g. "r1".
    """
    rows = [r for r in routines.history(limit=limit * 4, routine_id=routine_id)
            if r.get("event") == "closed"][:limit]
    if not rows:
        return "No completed routine days recorded yet."

    lines = []
    for row in rows:
        if row.get("rest"):
            lines.append(f"{row['date']}  {row.get('name', '')}  rest day")
            continue
        mark = "kept" if row.get("kept_streak") else "broke"
        lines.append(
            f"{row['date']}  {row.get('name', '')}  "
            f"{row['score']}/{row['total']} ({int(row.get('ratio', 0) * 100)}%)  "
            f"done {row.get('done', 0)} missed {row.get('missed', 0)}  "
            f"streak {mark} at {row.get('streak', 0)}"
        )
    return "\n".join(lines)
