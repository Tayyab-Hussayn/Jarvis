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

from typing import Literal

from .. import audit, routines
from ..registry import skill
from . import _merge
from ..safety import Danger, Verdict, describe_targets, enforce


def _routine_or_error(routine_id: str, snapshot: dict) -> dict | None:
    for routine in snapshot["routines"]:
        if routine["id"] == routine_id:
            return routine
    return None


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


@skill(
    category="routines", danger=Danger.MODERATE,
    aliases={
        "add_routine_step": {"action": "add"},
        "edit_routine_step": {"action": "edit"},
        "delete_routine_step": {"action": "delete"},
        "complete_step": {"action": "complete"},
        "skip_step": {"action": "skip"},
    },
)
def routine_step(
    action: Literal["add", "edit", "delete", "complete", "skip"],
    routine_id: str,
    step_id: str | None = None,
    label: str | None = None,
    at: str | None = None,
    after_step: str | None = None,
    after_minutes: int | None = None,
    requires: str | None = None,
    window_minutes: int | None = None,
    points: int | None = None,
    insist: bool | None = None,
    messages: str | None = None,
    nag_every_minutes: int | None = None,
    nag_max: int | None = None,
) -> str:
    """Work on one step (habit) of a routine: add, edit, delete, mark done, or skip it.


    Args:
        action: add (needs label, plus at, or after_step + after_minutes, never both); edit, delete, complete, skip (need step_id; edit changes only the fields given).
        routine_id: Which routine, e.g. "r1".
        step_id: Which step, e.g. "s2" (edit, delete, complete, skip).
        label: For add: name of the habit; for edit: its new name.
        at: Fixed time HH:MM local (add, edit).
        after_step: For add: step this one follows.
        after_minutes: For add: minutes after after_step was actually done.
        requires: For add: step that must be confirmed done before this one unlocks.
        window_minutes: How long the step stays open after it is due (add, edit).
        points: Score value of the step (add, edit).
        insist: For add: keep nagging until done.
        messages: Reminder wording variants (add, edit).
        nag_every_minutes: For add: minutes between nags.
        nag_max: For add: most nags.
    """
    if action == "add":
        if not label:
            return "add needs a label."
        return _merge.run(
            add_routine_step, routine_id=routine_id, label=label, at=at,
            after_step=after_step, after_minutes=after_minutes, requires=requires,
            window_minutes=window_minutes, points=points, insist=insist,
            messages=messages, nag_every_minutes=nag_every_minutes, nag_max=nag_max,
        )
    if action in ("edit", "delete", "complete", "skip") and not step_id:
        return f"{action} needs step_id."
    if action == "edit":
        return _merge.run(
            edit_routine_step, routine_id=routine_id, step_id=step_id, label=label,
            at=at, window_minutes=window_minutes, points=points, messages=messages,
        )
    if action == "delete":
        return delete_routine_step(routine_id, step_id)
    if action == "complete":
        return complete_step(routine_id, step_id)
    if action == "skip":
        return skip_step(routine_id, step_id)
    return _merge.unknown(action, ["add", "edit", "delete", "complete", "skip"])


@skill(
    category="routines", danger=Danger.MODERATE,
    aliases={"add_routine": {"action": "add"}, "pause_routine": {"action": "pause"}},
)
def routine(
    action: Literal["add", "pause"],
    name: str | None = None,
    day_start: str | None = None,
    days: str | None = None,
    timezone: str | None = None,
    routine_id: str | None = None,
    resume: bool | None = None,
) -> str:
    """Create a daily routine, or pause/resume one. Steps are added with routine_step; deleting uses delete_routine.


    Args:
        action: add (needs name); pause (needs routine_id; resume=True resumes).
        name: For add: what to call it, e.g. "Morning".
        day_start: For add: when the routine's day rolls over, HH:MM local (default 04:00).
        days: For add: which weekdays it runs; empty for every day.
        timezone: For add: IANA timezone; empty for the machine's.
        routine_id: For pause: which routine, e.g. "r1".
        resume: For pause: True to resume a paused routine instead.
    """
    if action == "add":
        if not name:
            return "add needs a name."
        return _merge.run(add_routine, name=name, day_start=day_start, days=days,
                          timezone=timezone)
    if action == "pause":
        if not routine_id:
            return "pause needs routine_id."
        return _merge.run(pause_routine, routine_id=routine_id, resume=resume)
    return _merge.unknown(action, ["add", "pause"])


@skill(
    category="routines", danger=Danger.SAFE,
    aliases={"routine_history": {"history": True}},
)
def routine_status(routine_id: str = "", history: bool = False, limit: int | None = None) -> str:
    """Show today's routines (what is done, what is next, the score), or how recent days went.

    Args:
        routine_id: Limit to one routine, e.g. "r1". Leave empty for all.
        history: True to show past days instead: score, what was missed, streak.
        limit: With history: how many days back (default 14).
    """
    if history:
        return _merge.run(routine_history, limit=limit, routine_id=routine_id or None)
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


def delete_routine_step(routine_id: str, step_id: str) -> str:
    """Remove one step from a routine.

    Refused if another step is scheduled to run after this one, because that
    step would have no time left to compute and would sit locked for ever.
    Give it its own time first.

    Args:
        routine_id: Which routine, e.g. "r1".
        step_id: Which step, e.g. "s2".
    """
    try:
        removed = routines.remove_step(routine_id, step_id)
    except ValueError as exc:
        return str(exc)
    if not removed:
        return f"No step {step_id!r} in {routine_id}."
    audit.record("routines.step_deleted", routine=routine_id, step=step_id)
    return f"Removed {step_id} from {routine_id}."


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
