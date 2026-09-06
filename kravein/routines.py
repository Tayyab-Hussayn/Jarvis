"""Health & habits routines: a sequenced daily state machine.

Sibling to `jobs.py`, not part of it. They share the minutely systemd timer,
the storage idioms and `delivery.py`, and they share no state: a routine step
never becomes a row in `jobs.json`. Two sources of truth for the same
obligation is the one thing this must not become.

**Same import budget as the scheduler.** Stdlib, `config`, and `timeutil`.
Everything else — the notification backend, the TTS engine, urllib — is
imported inside `delivery.py`, and only once there is something to say. A pass
that finds nothing due must cost close to nothing, because it runs 1,440 times
a day forever.

**The engine is a pure function of (store, moment).** `tick()` takes the
current time as an argument, defaulted rather than read from a global, so every
case below is a unit test with no I/O and no waiting: a rollover across seven
skipped days is three lines and a fake clock.

Four rules the state machine is built around:

*Sequencing is data, not code.* A step lists the steps it `requires`. It sits
in `locked` until all of them are `done`, and while locked it cannot be
delivered, nagged, completed or expired. That is the whole "Task B cannot
trigger unless Task A is confirmed" mechanic, and expressing it as a field
rather than as branches is what keeps it testable.

*A relative time is materialised once.* A step scheduled `after` its
predecessor has no due time until that predecessor is actually finished. When
it unlocks, the due time is computed and **written into the day**. Recomputing
it on later ticks would make the timeline shift under the user while they look
at it, and would make history unreproducible.

*Rollover is lazy and idempotent.* Nothing is scheduled for midnight. Every
pass compares the current day key with the open one, and closes the old day
when they differ. A laptop shut from 23:00 to 09:00 rolls over on the first
tick after it wakes; a laptop shut for a week collapses to one gap record
rather than fabricating six days of failure or firing six days of stale
reminders. Same no-catch-up rule as the jobs grace window.

*Blocking is soft.* A stalled chain stops later steps and stops the score. It
does not touch the machine. Hard blocking — locking the screen, killing an app
until you confirm — was designed and rejected: it is a self-inflicted denial of
service, on a timer, driven by a state machine, with nobody watching, and it
would run with `confirmer=None` where `safety.enforce()` refuses that tier
anyway. The escape hatch would be confirming the step, and the reason you have
not confirmed is usually that you are not at the machine. `insist` is the
sanctioned alternative: one rung louder, still only a notification and speech.
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from typing import Any

from . import config
from .timeutil import (
    in_dst_gap,
    iso,
    local_moment,
    local_timezone,
    now_utc,
    parse_clock,
    parse_iso,
    zone,
)

STORE = config.STATE_DIR / "routines.json"
LOCK = config.STATE_DIR / "routines.lock"
HISTORY = config.STATE_DIR / "routine-days.jsonl"

# Where the day turns over, local wall-clock. Not midnight: a step at 01:00 is
# the tail of a long evening, not the start of a new morning, and a boundary at
# 00:00 cuts a night routine in half.
DEFAULT_DAY_START = "04:00"

# A day counts towards the streak at or above this share of its points. A
# single missed step ending a streak is too punishing to survive contact with
# an actual week.
STREAK_THRESHOLD = 0.8

# Steps a nag can be sent for, and states that end the step for the day.
LIVE_STATES = ("due", "nagging")
RESOLVED_STATES = ("done", "missed", "skipped")
STEP_STATES = ("locked", *LIVE_STATES, *RESOLVED_STATES)

DEFAULT_WINDOW_MINUTES = 60
DEFAULT_NAG = {"every_minutes": 10, "max": 3, "escalate": True}


class RoutineError(ValueError):
    """A routine or step could not be created or changed as asked."""


# ---------------------------------------------------------------------------
# Store — same idioms as jobs.py, including the separate lock file
# ---------------------------------------------------------------------------

@contextmanager
def locked(blocking: bool = True):
    """Hold the store lock, or yield False if another pass holds it.

    The lock is its own file because `routines.json` is written by atomic
    tmp+rename, which replaces the inode — a `flock` on the store would guard a
    path that no longer exists and two passes could both believe they hold it.
    """
    config.ensure_dirs()
    handle = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        os.close(handle)


def load() -> dict[str, Any]:
    """Read the store. Never raises — a corrupt file must not stop the timer."""
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"routines": [], "today": {}, "seq": 0}
    if not isinstance(data, dict):
        return {"routines": [], "today": {}, "seq": 0}
    data.setdefault("routines", [])
    data.setdefault("today", {})
    data.setdefault("seq", 0)
    return data


def save(data: dict[str, Any]) -> None:
    """Atomic write, matching `jobs.py` and `skills/memory.py`."""
    config.ensure_dirs()
    tmp = STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STORE)


def record_day(**fields: Any) -> None:
    """Append one line of closed-day history. Mirrors `job-runs.jsonl`."""
    try:
        config.ensure_dirs()
        line = json.dumps({"ts": iso(now_utc()), **fields}, ensure_ascii=False, default=str)
        with open(HISTORY, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def history(limit: int = 30, routine_id: str = "") -> list[dict[str, Any]]:
    """The most recent closed days, newest first."""
    try:
        lines = HISTORY.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if routine_id and row.get("routine") != routine_id:
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Day keys and due times
# ---------------------------------------------------------------------------

def day_bounds(routine: dict[str, Any]) -> tuple[int, int]:
    return parse_clock(routine.get("day_start") or DEFAULT_DAY_START) or (4, 0)


def day_key(routine: dict[str, Any], moment: datetime) -> str:
    """Which routine-day this instant belongs to, as a local date string.

    The local clock is wound back by `day_start` before the date is taken, so
    01:00 on the 7th with a 04:00 boundary is still the 6th's routine.
    """
    hour, minute = day_bounds(routine)
    local = moment.astimezone(zone(routine.get("timezone", "")))
    return (local - timedelta(hours=hour, minutes=minute)).date().isoformat()


def _key_date(key: str) -> date_cls:
    return date_cls.fromisoformat(key)


def step_calendar_date(routine: dict[str, Any], step: dict[str, Any],
                       key: str) -> tuple[date_cls, int, int] | None:
    """Which calendar date an absolute step actually falls on, and at what time.

    A step whose wall-clock time is before the day boundary belongs to the
    *following* calendar date — with a 04:00 boundary, "01:00" on the 6th's
    routine happens at 01:00 on the 7th. Getting this backwards schedules a
    late-night step seventeen hours early, in the past, where it is dropped.

    Split out from `step_due_at` because `dst_note` needs the same shift, and
    the first version did not have it: it looked for a daylight-saving gap on
    the key date, so it would warn about the wrong day and stay silent about
    the right one. Two callers deriving the same date separately is how that
    happens.
    """
    clock = parse_clock(step.get("at") or "")
    if clock is None:
        return None
    hour, minute = clock
    day = _key_date(key)
    if (hour, minute) < day_bounds(routine):
        day = day + timedelta(days=1)
    return day, hour, minute


def step_due_at(routine: dict[str, Any], step: dict[str, Any], key: str) -> datetime | None:
    """The UTC instant an absolute step is due on a given routine-day."""
    resolved = step_calendar_date(routine, step, key)
    if resolved is None:
        return None
    day, hour, minute = resolved
    return local_moment(day, hour, minute, zone(routine.get("timezone", "")))


def _window(step: dict[str, Any]) -> int:
    try:
        return max(0, int(step.get("window_minutes", DEFAULT_WINDOW_MINUTES)))
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_MINUTES


def _expiry(step: dict[str, Any], due: datetime) -> str:
    """When the chance is gone. A zero window means "until the day ends"."""
    minutes = _window(step)
    return iso(due + timedelta(minutes=minutes)) if minutes else ""


def _nag(step: dict[str, Any]) -> dict[str, Any]:
    raw = step.get("nag") or {}
    return {
        "every_minutes": max(1, int(raw.get("every_minutes", DEFAULT_NAG["every_minutes"]))),
        "max": max(0, int(raw.get("max", DEFAULT_NAG["max"]))),
        "escalate": bool(raw.get("escalate", DEFAULT_NAG["escalate"])),
    }


def _points(step: dict[str, Any]) -> int:
    try:
        return max(0, int(step.get("points", 0)))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Building and closing a day
# ---------------------------------------------------------------------------

def _is_rest_day(routine: dict[str, Any], key: str) -> bool:
    days = routine.get("days")
    if not days:
        return False
    return _key_date(key).weekday() not in set(days)


def open_day(routine: dict[str, Any], key: str, moment: datetime) -> dict[str, Any]:
    """Build a fresh day instance from the routine template.

    Every step starts `locked`. Absolute steps get their due time immediately,
    because it is knowable; relative steps get nothing until their predecessor
    actually finishes. `locked` therefore means "not actionable yet" for both
    reasons — not your turn, or not yet the time — and the panel tells them
    apart by whether a due time is present.
    """
    rest = _is_rest_day(routine, key)
    steps: dict[str, Any] = {}
    if not rest:
        for step in routine.get("steps", []):
            due = step_due_at(routine, step, key)

            expires = _expiry(step, due) if due else ""

            # A day whose steps were already unreachable when it opened starts
            # them stood down, not pending. Two ways to get here and both need
            # it: a routine created at 4pm would otherwise report its 07:00
            # step as missed before it had ever existed, and a laptop switched
            # off all morning would manufacture a morning's worth of failures
            # for reminders nobody was ever shown. `skipped` keeps them out of
            # the score — being asked and not doing it is a miss, never being
            # asked is not.
            #
            # The test is whether the *window* has closed, not whether the due
            # time has passed. Those differ by seconds at the boundary, and the
            # first version used the due time: a step scheduled for the current
            # minute was stood down by the tick that ran two seconds later.
            # While the window is open the user can still act, so the step
            # stays live. A zero window never closes, so it always does.
            closed = parse_iso(expires)
            late = closed is not None and closed < moment
            steps[step["id"]] = {
                "state": "skipped" if late else "locked",
                "due_at": iso(due) if due else "",
                "expires_at": expires,
                "done_at": "",
                "source": "day opened late" if late else "",
                "nags_sent": 0,
                "next_nag_at": "",
            }
    # The instant this routine-day ends, cached as UTC. Everything else in the
    # day instance is already UTC, so with this stored the steady-state pass
    # needs no timezone arithmetic at all — and therefore never imports
    # `zoneinfo`, which costs 5.2ms and was being paid on all 1,440 ticks to
    # answer a question whose answer does not change until tomorrow.
    start_hour, start_minute = day_bounds(routine)
    ends = local_moment(_key_date(key) + timedelta(days=1), start_hour, start_minute,
                        zone(routine.get("timezone", "")))
    return {
        "date": key,
        "opened_at": iso(moment),
        "ends_at": iso(ends),
        "rest": rest,
        "score": 0,
        "available": 0,
        "total": 0 if rest else sum(_points(s) for s in routine.get("steps", [])),
        "steps": steps,
    }


def _score_day(routine: dict[str, Any], day: dict[str, Any]) -> None:
    """Recompute the three counters, in place.

    Three of them, and the third is the reason there are three. `available` is
    what has actually come due so far, so the panel can show `score/available`
    — showing `score/total` reads as failure all morning, before you have had
    the chance to do the evening steps at all.
    """
    score = available = skipped = 0
    for step in routine.get("steps", []):
        state = day["steps"].get(step["id"], {}).get("state", "locked")
        points = _points(step)
        if state == "done":
            score += points
            available += points
        elif state in ("missed", *LIVE_STATES):
            available += points
        elif state == "skipped":
            # Tracked separately because the streak divides them out: standing
            # a habit down on purpose must not read as failing it.
            skipped += points
        # `locked` has simply not come due yet.
    day["score"] = score
    day["available"] = available
    day["skipped_points"] = skipped


def close_day(routine: dict[str, Any], day: dict[str, Any]) -> dict[str, Any]:
    """Resolve everything still open, score it, and return the history row."""
    for state in day.get("steps", {}).values():
        if state.get("state") in ("locked", *LIVE_STATES):
            state["state"] = "missed"
    _score_day(routine, day)

    # Skipped points come out of the denominator. A miss counts against the
    # day; a deliberate skip is not a failure, it is a decision, and scoring it
    # as one teaches people not to use the skip button — which costs you the
    # signal about what they actually intended.
    total = int(day.get("total", 0))
    judged = max(0, total - int(day.get("skipped_points", 0)))
    ratio = 1.0 if judged <= 0 else day["score"] / judged
    kept = bool(day.get("rest")) or ratio >= STREAK_THRESHOLD
    routine["streak"] = int(routine.get("streak", 0)) + 1 if kept else 0
    routine["best_streak"] = max(int(routine.get("best_streak", 0)), routine["streak"])

    counts = {"done": 0, "missed": 0, "skipped": 0}
    for state in day.get("steps", {}).values():
        name = state.get("state", "")
        if name in counts:
            counts[name] += 1

    return {
        "routine": routine["id"],
        "name": routine.get("name", ""),
        "date": day.get("date", ""),
        "rest": bool(day.get("rest")),
        "score": day["score"],
        "total": total,
        # What the ratio was actually measured against, so a history row can be
        # re-derived. `ratio` is score/judged, not score/total.
        "judged": judged,
        "skipped_points": int(day.get("skipped_points", 0)),
        "ratio": round(ratio, 3),
        "kept_streak": kept,
        "streak": routine["streak"],
        **counts,
        "steps": {k: v.get("state", "") for k, v in day.get("steps", {}).items()},
    }


# ---------------------------------------------------------------------------
# The passes
# ---------------------------------------------------------------------------

def _roll_day(data: dict[str, Any], routine: dict[str, Any], moment: datetime) -> bool:
    """Close a finished day and open the current one. Idempotent."""
    today = data.setdefault("today", {})
    open_instance = today.get(routine["id"])

    # Fast path, and the reason `ends_at` is stored: the overwhelmingly common
    # case is "the day has not turned over", and answering that from a cached
    # UTC instant avoids loading the timezone database. Falls through when the
    # instance predates this field, so an existing store still works.
    ends = parse_iso((open_instance or {}).get("ends_at", ""))
    if open_instance and ends is not None and moment < ends:
        return False

    key = day_key(routine, moment)
    if open_instance and open_instance.get("date") == key:
        return False

    if open_instance:
        record_day(event="closed", **close_day(routine, open_instance))

        # More than one day has passed. Do not fabricate the days in between:
        # a machine shut for a week would otherwise write six all-missed days
        # nobody was ever given the chance to do. One gap record, streak reset,
        # move on — the same no-catch-up rule the jobs grace window follows.
        try:
            gap = (_key_date(key) - _key_date(open_instance["date"])).days
        except ValueError:
            gap = 1
        if gap > 1:
            record_day(event="skipped_days", routine=routine["id"],
                       **{"from": open_instance["date"], "to": key, "days": gap - 1})
            routine["streak"] = 0

    today[routine["id"]] = open_day(routine, key, moment)
    return True


def _unlock(data: dict[str, Any], routine: dict[str, Any], moment: datetime) -> bool:
    """Move steps from `locked` to `due`, materialising relative times.

    Run to a fixed point, because unlocking one step can unlock the next in the
    same pass — a chain of `after` steps with zero offsets would otherwise
    advance by one link per minute.
    """
    day = data["today"][routine["id"]]
    steps = {s["id"]: s for s in routine.get("steps", [])}
    changed = False

    for _ in range(len(steps) + 1):
        moved = False
        for step in routine.get("steps", []):
            state = day["steps"].get(step["id"])
            if not state or state.get("state") != "locked":
                continue

            required = [steps.get(r) for r in step.get("requires", [])]
            prior = [day["steps"].get(r, {}) for r in step.get("requires", []) if r in steps]

            # A prerequisite that will never complete ends this step too, and
            # the distinction matters: a *missed* prerequisite means the chain
            # failed, so the dependent is missed and counts against the day. A
            # *skipped* one was a deliberate stand-down, so the dependent is
            # skipped and costs nothing. Leaving it locked would leave the step
            # pending for ever with no explanation anywhere.
            if any(p.get("state") == "missed" for p in prior):
                state["state"] = "missed"
                moved = changed = True
                continue
            if any(p.get("state") == "skipped" for p in prior):
                state["state"] = "skipped"
                moved = changed = True
                continue
            if not all(p.get("state") == "done" for p in prior):
                continue
            if None in required:
                continue

            if not state.get("due_at"):
                after = step.get("after") or {}
                anchor = day["steps"].get(str(after.get("step", "")), {}).get("done_at", "")
                base = parse_iso(anchor) or moment
                due = base + timedelta(minutes=int(after.get("minutes", 0) or 0))
                state["due_at"] = iso(due)
                state["expires_at"] = _expiry(step, due)
                moved = changed = True

            due = parse_iso(state.get("due_at", ""))
            if due is not None and moment >= due:
                state["state"] = "due"
                state["next_nag_at"] = iso(moment)
                moved = changed = True

        if not moved:
            break
    return changed


def _expire(data: dict[str, Any], routine: dict[str, Any], moment: datetime) -> bool:
    """Close the window on steps that were live and are now out of time."""
    day = data["today"][routine["id"]]
    changed = False
    for state in day["steps"].values():
        if state.get("state") not in LIVE_STATES:
            continue
        expires = parse_iso(state.get("expires_at", ""))
        if expires is not None and moment > expires:
            state["state"] = "missed"
            state["next_nag_at"] = ""
            changed = True
    return changed


def _settle(data: dict[str, Any], routine: dict[str, Any], moment: datetime) -> bool:
    """Run expiry and unlocking to a fixed point.

    Order matters and one pass of each is not enough, which cost a test to
    notice. Expiring a step can fail the chain behind it, and unlocking a step
    can immediately expire it when the tick is running late — a machine asleep
    for two hours wakes to a step that became due and ran out of time while it
    slept, and that must resolve inside one pass rather than dribbling out one
    state change per minute. Anything less leaves the panel showing `locked`
    for a step that can never run.

    Bounded by the chain length, because each round resolves at least one step.
    """
    changed = False
    for _ in range(len(routine.get("steps", [])) + 2):
        moved = _expire(data, routine, moment)
        moved = _unlock(data, routine, moment) or moved
        changed = changed or moved
        if not moved:
            break
    return changed


def _collect_nags(data: dict[str, Any], routine: dict[str, Any],
                  moment: datetime) -> list[dict[str, Any]]:
    """Decide what needs saying, and record it as sent.

    Everything here happens under the lock and is written to the store
    *before* anything is delivered, so a crash mid-notification costs one
    missed reminder instead of an endless loop of them. Same ordering as
    `jobs._claim` marking a job running before the Agent exists.
    """
    from . import delivery

    day = data["today"][routine["id"]]
    out: list[dict[str, Any]] = []

    for step in routine.get("steps", []):
        state = day["steps"].get(step["id"])
        if not state or state.get("state") not in LIVE_STATES:
            continue
        policy = _nag(step)
        sent = int(state.get("nags_sent", 0))
        if policy["max"] and sent >= policy["max"]:
            continue
        due = parse_iso(state.get("next_nag_at", "")) or parse_iso(state.get("due_at", ""))
        if due is None or moment < due:
            continue

        sent += 1
        state["nags_sent"] = sent
        state["state"] = "nagging"
        state["next_nag_at"] = iso(moment + timedelta(minutes=policy["every_minutes"]))

        messages = [m for m in (step.get("messages") or []) if str(m).strip()]
        text = str(messages[(sent - 1) % len(messages)]) if messages else step.get("label", "")
        insist = bool(step.get("insist"))
        out.append({
            "routine": routine["id"],
            "step": step["id"],
            "label": step.get("label", ""),
            "title": routine.get("name") or "Kravein",
            "text": text,
            "nag": sent,
            "insist": insist,
            "channels": delivery.escalation(sent, escalate=policy["escalate"], insist=insist),
        })
    return out


def tick(moment: datetime | None = None) -> int:
    """One routine pass. Returns how many reminders were sent.

    The lock is non-blocking on purpose: if a previous pass is still delivering,
    this one should step aside rather than queue up behind it and then repeat
    everything it just did.
    """
    from . import delivery

    now = moment or now_utc()
    events: list[dict[str, Any]] = []

    with locked(blocking=False) as got:
        if not got:
            return 0
        data = load()
        if not data.get("routines"):
            return 0

        changed = False
        for routine in data["routines"]:
            if not routine.get("enabled", True):
                continue
            changed = _roll_day(data, routine, now) or changed
            changed = _settle(data, routine, now) or changed
            nags = _collect_nags(data, routine, now)
            if nags:
                events.extend(nags)
                changed = True
            _score_day(routine, data["today"][routine["id"]])

        if changed:
            save(data)

    # Delivery happens outside the lock. Notification and speech both talk to
    # other processes, and holding a file lock across that would block the
    # panel's next write behind an audio device.
    for event in events:
        delivery.deliver(
            event["text"] or event["label"],
            title=event["title"],
            channels=event["channels"],
            urgency="critical" if event["insist"] else "normal",
            push_path="/api/routine-event",
            push_payload={
                "routine": event["routine"], "step": event["step"],
                "label": event["label"], "text": event["text"], "nag": event["nag"],
            },
        )
        record_day(event="nagged", routine=event["routine"], step=event["step"],
                   nag=event["nag"], channels=list(event["channels"]))

    return len(events)


# ---------------------------------------------------------------------------
# Mutating: routines, steps, and confirmations
# ---------------------------------------------------------------------------

def _find(data: dict[str, Any], routine_id: str) -> dict[str, Any] | None:
    for routine in data.get("routines", []):
        if routine.get("id") == routine_id:
            return routine
    return None


def _check_cycle(routine: dict[str, Any]) -> None:
    """Refuse a `requires` graph that cannot ever resolve.

    A cycle leaves every step in it permanently `locked` with no error raised
    anywhere and nothing on screen to explain why the routine simply stopped.
    Cheaper to refuse at write time than to debug at 7am.
    """
    edges = {s["id"]: [r for r in s.get("requires", [])] for s in routine.get("steps", [])}
    state: dict[str, int] = {}

    def walk(node: str) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            raise RoutineError(f"step {node!r} ends up requiring itself")
        state[node] = 1
        for nxt in edges.get(node, []):
            if nxt in edges:
                walk(nxt)
        state[node] = 2

    for node in edges:
        walk(node)


def _validate_step(routine: dict[str, Any], step: dict[str, Any]) -> None:
    has_at = bool(step.get("at"))
    after = step.get("after") or {}
    has_after = bool(after.get("step"))

    if has_at == has_after:
        raise RoutineError(
            "a step needs exactly one of `at` (a clock time) or `after` "
            "(an offset from another step)"
        )
    if has_at and parse_clock(step["at"]) is None:
        raise RoutineError(f"not a time of day: {step['at']!r} — use HH:MM")

    ids = {s["id"] for s in routine.get("steps", [])}
    if has_after:
        if after["step"] not in ids:
            raise RoutineError(f"no step called {after['step']!r} to come after")
        # An `after` step depends on its anchor by definition; requiring it
        # explicitly is what makes the due time computable at unlock.
        if after["step"] not in step.get("requires", []):
            step.setdefault("requires", []).append(after["step"])
    for required in step.get("requires", []):
        if required not in ids:
            raise RoutineError(f"no step called {required!r} to require")


def add_routine(name: str, *, day_start: str = DEFAULT_DAY_START,
                tz_name: str = "", days: list[int] | None = None) -> dict[str, Any]:
    if not (name or "").strip():
        raise RoutineError("a routine needs a name")
    if parse_clock(day_start) is None:
        raise RoutineError(f"day_start must be HH:MM, not {day_start!r}")
    if days is not None and any(d not in range(7) for d in days):
        raise RoutineError("days are 0 (Monday) to 6 (Sunday)")

    with locked() as got:
        if not got:
            raise RuntimeError("the routine store is busy")
        data = load()
        data["seq"] = int(data.get("seq", 0)) + 1
        routine = {
            "id": f"r{data['seq']}",
            "name": name.strip(),
            "enabled": True,
            "timezone": tz_name or local_timezone(),
            "day_start": day_start,
            "days": list(days) if days is not None else list(range(7)),
            "steps": [],
            "streak": 0,
            "best_streak": 0,
            "created_at": iso(now_utc()),
        }
        data["routines"].append(routine)
        save(data)
    return routine


def add_step(routine_id: str, *, label: str, at: str = "",
             after_step: str = "", after_minutes: int = 0,
             requires: list[str] | None = None, window_minutes: int = DEFAULT_WINDOW_MINUTES,
             points: int = 10, insist: bool = False,
             messages: list[str] | None = None,
             nag: dict[str, Any] | None = None) -> dict[str, Any]:
    """Append a step. The new step is live from the next routine-day.

    Deliberately not retro-fitted into today's open instance: a step appearing
    mid-morning already `missed` because its time passed an hour before it
    existed is a confusing way to start.
    """
    if not (label or "").strip():
        raise RoutineError("a step needs a label")

    with locked() as got:
        if not got:
            raise RuntimeError("the routine store is busy")
        data = load()
        routine = _find(data, routine_id)
        if routine is None:
            raise RoutineError(f"no routine called {routine_id!r}")

        existing = {s["id"] for s in routine["steps"]}
        index = len(routine["steps"]) + 1
        while f"s{index}" in existing:
            index += 1

        step = {
            "id": f"s{index}",
            "label": label.strip(),
            "at": at or None,
            "after": {"step": after_step, "minutes": int(after_minutes)} if after_step else None,
            "requires": list(requires or []),
            "window_minutes": int(window_minutes),
            "points": int(points),
            "insist": bool(insist),
            "messages": [str(m) for m in (messages or []) if str(m).strip()],
            "nag": {**DEFAULT_NAG, **(nag or {})},
        }
        _validate_step(routine, step)
        routine["steps"].append(step)
        _check_cycle(routine)
        save(data)
    return step


def edit_step(routine_id: str, step_id: str, **fields: Any) -> dict[str, Any]:
    """Change a step in place. Only the fields passed are touched."""
    with locked() as got:
        if not got:
            raise RuntimeError("the routine store is busy")
        data = load()
        routine = _find(data, routine_id)
        if routine is None:
            raise RoutineError(f"no routine called {routine_id!r}")
        for step in routine["steps"]:
            if step["id"] != step_id:
                continue
            for key, value in fields.items():
                if value is not None:
                    step[key] = value
            _validate_step(routine, step)
            _check_cycle(routine)
            save(data)
            return dict(step)
    raise RoutineError(f"no step called {step_id!r} in {routine_id}")


def remove_step(routine_id: str, step_id: str) -> bool:
    with locked() as got:
        if not got:
            raise RuntimeError("the routine store is busy")
        data = load()
        routine = _find(data, routine_id)
        if routine is None:
            return False
        before = len(routine["steps"])
        routine["steps"] = [s for s in routine["steps"] if s["id"] != step_id]
        # Anything that depended on it would sit locked for ever otherwise.
        for step in routine["steps"]:
            step["requires"] = [r for r in step.get("requires", []) if r != step_id]
            if (step.get("after") or {}).get("step") == step_id:
                raise RoutineError(
                    f"{step['id']} is scheduled after {step_id}; give it a time first"
                )
        data.get("today", {}).get(routine_id, {}).get("steps", {}).pop(step_id, None)
        save(data)
        return len(routine["steps"]) < before


def remove_routine(routine_id: str) -> bool:
    with locked() as got:
        if not got:
            raise RuntimeError("the routine store is busy")
        data = load()
        before = len(data.get("routines", []))
        data["routines"] = [r for r in data.get("routines", []) if r.get("id") != routine_id]
        data.get("today", {}).pop(routine_id, None)
        save(data)
        return len(data["routines"]) < before


def set_enabled(routine_id: str, enabled: bool) -> bool:
    with locked() as got:
        if not got:
            raise RuntimeError("the routine store is busy")
        data = load()
        routine = _find(data, routine_id)
        if routine is None:
            return False
        routine["enabled"] = bool(enabled)
        save(data)
        return True


def resolve(routine_id: str, step_id: str, *, state: str,
            source: str = "cli", moment: datetime | None = None) -> dict[str, Any]:
    """Mark a step done or skipped, and let the chain advance.

    A `locked` step is refused. That refusal *is* the sequencing mechanic — it
    is what "Task B cannot trigger unless Task A is confirmed" means when
    somebody presses the button anyway — so it is an error with a reason, not a
    silent no-op.
    """
    if state not in ("done", "skipped"):
        raise RoutineError("a step is either done or skipped")
    now = moment or now_utc()

    with locked() as got:
        if not got:
            raise RuntimeError("the routine store is busy")
        data = load()
        routine = _find(data, routine_id)
        if routine is None:
            raise RoutineError(f"no routine called {routine_id!r}")

        _roll_day(data, routine, now)
        _settle(data, routine, now)
        day = data["today"][routine_id]
        current = day["steps"].get(step_id)
        if current is None:
            raise RoutineError(f"no step called {step_id!r} today")

        if current["state"] == "locked":
            blockers = [r for r in _step_of(routine, step_id).get("requires", [])
                        if day["steps"].get(r, {}).get("state") != "done"]
            if blockers:
                names = ", ".join(_label(routine, b) for b in blockers)
                raise RoutineError(f"not yet — that one comes after {names}")
            raise RoutineError("not yet — it is not time for that one")
        if current["state"] in RESOLVED_STATES:
            raise RoutineError(f"already {current['state']} today")

        current["state"] = state
        current["done_at"] = iso(now)
        current["source"] = source
        current["next_nag_at"] = ""

        _settle(data, routine, now)
        _score_day(routine, day)
        save(data)
        result = {"routine": routine_id, "step": step_id, "state": state,
                  "score": day["score"], "available": day["available"],
                  "total": day["total"]}

    record_day(event=state, routine=routine_id, step=step_id, source=source)
    return result


def _step_of(routine: dict[str, Any], step_id: str) -> dict[str, Any]:
    for step in routine.get("steps", []):
        if step["id"] == step_id:
            return step
    return {}


def _label(routine: dict[str, Any], step_id: str) -> str:
    return _step_of(routine, step_id).get("label") or step_id


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def snapshot(moment: datetime | None = None) -> dict[str, Any]:
    """Everything the CLI and the panel need, as records rather than prose.

    Read-only and lock-free. It rolls the day *in memory* so a stale instance
    is never shown, but does not save — writing is the tick's job, and a panel
    refresh must not be able to close a day.
    """
    now = moment or now_utc()
    data = load()
    out = {"now": iso(now), "timezone": local_timezone(), "routines": []}

    for routine in data.get("routines", []):
        key = day_key(routine, now)
        day = data.get("today", {}).get(routine["id"])
        if not day or day.get("date") != key:
            day = open_day(routine, key, now)
        else:
            day = json.loads(json.dumps(day))
        scratch = {"today": {routine["id"]: day}}
        _settle(scratch, routine, now)
        _score_day(routine, day)

        out["routines"].append({
            "id": routine["id"], "name": routine.get("name", ""),
            "enabled": routine.get("enabled", True),
            "timezone": routine.get("timezone", ""),
            "day_start": routine.get("day_start", DEFAULT_DAY_START),
            "days": routine.get("days", list(range(7))),
            "streak": routine.get("streak", 0),
            "best_streak": routine.get("best_streak", 0),
            "steps": routine.get("steps", []),
            "today": day,
        })
    return out


def describe(routine: dict[str, Any]) -> list[str]:
    """One line per step, for the REPL and for skill output."""
    day = routine["today"]
    tz = zone(routine.get("timezone", ""))
    lines: list[str] = []
    for step in routine["steps"]:
        state = day["steps"].get(step["id"], {})
        due = parse_iso(state.get("due_at", ""))
        when = due.astimezone(tz).strftime("%H:%M") if due else "—"
        mark = {"done": "done", "missed": "missed", "skipped": "skipped",
                "nagging": f"nagging {state.get('nags_sent', 0)}", "due": "now",
                "locked": "locked"}.get(state.get("state", ""), state.get("state", ""))
        after = (step.get("after") or {}).get("step")
        note = f" (after {_label(routine, after)})" if after and not due else ""
        lines.append(
            f"  {when:>5}  {step['label'][:28]:<28} {_points(step):>3}pt  {mark}{note}"
        )
    return lines


def dst_note(routine: dict[str, Any], step: dict[str, Any], key: str) -> str:
    """Explain a time that the clocks moved. Empty when nothing happened."""
    resolved = step_calendar_date(routine, step, key)
    if resolved is None:
        return ""
    day, hour, minute = resolved
    if not in_dst_gap(day, hour, minute, zone(routine.get("timezone", ""))):
        return ""
    due = step_due_at(routine, step, key)
    landed = due.astimezone(zone(routine.get("timezone", ""))).strftime("%H:%M") if due else "?"
    return f"{step.get('at')} does not exist that day (clocks change); it runs at {landed}"


def main(argv: list[str] | None = None) -> int:
    """`python -m kravein.routines [tick|list]`, for debugging by hand."""
    import sys

    args = list(argv if argv is not None else sys.argv[1:])
    command = args[0] if args else "tick"

    if command == "tick":
        try:
            tick()
        except Exception as exc:
            record_day(event="tick_error", error=f"{type(exc).__name__}: {exc}")
            return 1
        return 0

    if command == "list":
        for routine in snapshot()["routines"]:
            day = routine["today"]
            print(f"{routine['id']}  {routine['name']}  "
                  f"{day['score']}/{day['available']} of {day['total']}  "
                  f"streak {routine['streak']}")
            for line in describe(routine):
                print(line)
        return 0

    print("usage: python -m kravein.routines [tick|list]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
