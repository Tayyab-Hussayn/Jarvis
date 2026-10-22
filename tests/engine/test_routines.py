"""Tests: routines.

Split out of the legacy tests/test_linux_port.py with no change to any test body.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import llm, registry, safety  # noqa: E402,F401
from kravein.safety import Danger  # noqa: E402,F401
from conftest import _at, _morning, _states, habits  # noqa: E402,F401


# Health & habits routines
# ---------------------------------------------------------------------------

# --- sequencing ------------------------------------------------------------

def test_a_locked_step_cannot_be_completed(habits):
    """The refusal *is* the sequencing mechanic, so it must be an error."""
    rid = _morning(habits)
    habits.tick(_at(6, 7, 0))
    assert _states(habits, rid) == {"s1": "nagging", "s2": "locked", "s3": "locked"}

    with pytest.raises(habits.RoutineError) as exc:
        habits.resolve(rid, "s2", state="done", moment=_at(6, 7, 5))
    assert "Wake up" in str(exc.value)


def test_a_locked_step_is_never_nagged_or_expired(habits):
    """A step waiting on a confirmation must not burn its window in the meantime."""
    rid = _morning(habits)
    # Stay inside s1's own 60-minute window: this is about s2 sitting behind an
    # unconfirmed step, not about what happens once the chain has failed.
    for minute in (0, 10, 20, 30, 40, 50):
        habits.tick(_at(6, 7, minute))

    states = _states(habits, rid)
    assert states["s2"] == "locked"
    stored = habits.load()["today"][rid]["steps"]
    assert stored["s2"]["nags_sent"] == 0
    assert stored["s2"]["due_at"] == ""      # not knowable yet
    assert [e for e in habits.sent if "Breakfast" in e["text"]] == []


def test_confirming_a_step_unlocks_the_next_one(habits):
    rid = _morning(habits)
    habits.tick(_at(6, 7, 0))
    habits.resolve(rid, "s1", state="done", moment=_at(6, 7, 4))

    stored = habits.load()["today"][rid]["steps"]
    assert stored["s1"]["state"] == "done"
    # 30 minutes after it was *actually* done, not after it was due.
    assert stored["s2"]["due_at"] == habits.iso(_at(6, 7, 34))
    assert stored["s3"]["due_at"] == ""      # still behind s2


def test_a_materialised_time_does_not_drift(habits):
    """Recomputing on later ticks would shift the timeline under the user."""
    rid = _morning(habits)
    habits.tick(_at(6, 7, 0))
    habits.resolve(rid, "s1", state="done", moment=_at(6, 7, 4))
    due = habits.load()["today"][rid]["steps"]["s2"]["due_at"]

    for minute in (10, 20, 33, 34, 40):
        habits.tick(_at(6, 7, minute))
    assert habits.load()["today"][rid]["steps"]["s2"]["due_at"] == due


def test_a_missed_prerequisite_fails_the_chain(habits):
    rid = _morning(habits)
    habits.tick(_at(6, 7, 0))
    habits.tick(_at(6, 8, 30))            # past s1's 60m window
    assert _states(habits, rid) == {"s1": "missed", "s2": "missed", "s3": "missed"}


def test_a_skipped_prerequisite_stands_the_chain_down(habits):
    """Deliberately skipping must not read the same as failing."""
    rid = _morning(habits)
    habits.tick(_at(6, 7, 0))
    habits.resolve(rid, "s1", state="skipped", moment=_at(6, 7, 5))
    habits.tick(_at(6, 7, 6))
    assert _states(habits, rid) == {"s1": "skipped", "s2": "skipped", "s3": "skipped"}

    day = habits.load()["today"][rid]
    assert day["available"] == 0          # nothing was ever on the hook
    assert day["score"] == 0


def test_a_cycle_in_requires_is_refused_at_write_time(habits):
    """A cycle leaves every step locked for ever with nothing to explain it."""
    routine = habits.add_routine("Loop")
    habits.add_step(routine["id"], label="A", at="07:00")
    habits.add_step(routine["id"], label="B", after_step="s1", after_minutes=5)
    with pytest.raises(habits.RoutineError):
        habits.edit_step(routine["id"], "s1", requires=["s2"])


# --- the day boundary ------------------------------------------------------

def test_the_day_starts_at_day_start_not_midnight(habits):
    """01:00 is the tail of a long evening, not a new morning."""
    rid = _morning(habits)
    routine = habits.load()["routines"][0]
    assert habits.day_key(routine, _at(6, 23, 0)) == "2026-09-06"
    assert habits.day_key(routine, _at(7, 1, 0)) == "2026-09-06"
    assert habits.day_key(routine, _at(7, 5, 0)) == "2026-09-07"


def test_a_late_night_step_lands_on_the_following_date(habits):
    """With a 04:00 boundary, 01:00 belongs to the day but happens tomorrow."""
    routine = habits.add_routine("Night", tz_name="UTC")
    habits.add_step(routine["id"], label="Lights out", at="01:00")
    stored = habits.load()["routines"][0]
    due = habits.step_due_at(stored, stored["steps"][0], "2026-09-06")
    assert due == _at(7, 1, 0)


def test_rollover_closes_the_old_day_and_opens_the_new_one(habits):
    rid = _morning(habits)
    habits.tick(_at(6, 7, 0))
    habits.resolve(rid, "s1", state="done", moment=_at(6, 7, 10))

    habits.tick(_at(7, 6, 0))             # next routine day
    day = habits.load()["today"][rid]
    assert day["date"] == "2026-09-07"
    assert set(_states(habits, rid).values()) == {"locked"}

    closed = [r for r in habits.history(50) if r.get("event") == "closed"]
    assert closed[0]["date"] == "2026-09-06"
    assert closed[0]["score"] == 10 and closed[0]["missed"] == 2


def test_rollover_is_idempotent(habits):
    """Two passes in the same day must not close it twice."""
    rid = _morning(habits)
    habits.tick(_at(6, 7, 0))
    habits.tick(_at(7, 6, 0))
    habits.tick(_at(7, 6, 1))
    habits.tick(_at(7, 6, 2))
    closed = [r for r in habits.history(50) if r.get("event") == "closed"]
    assert len(closed) == 1


def test_a_week_asleep_collapses_to_one_gap_record(habits):
    """No catch-up: six days of failure nobody was given the chance to do."""
    rid = _morning(habits)
    habits.tick(_at(6, 7, 0))
    habits.tick(_at(13, 6, 0))            # a week later, first tick after waking

    closed = [r for r in habits.history(50) if r.get("event") == "closed"]
    gaps = [r for r in habits.history(50) if r.get("event") == "skipped_days"]
    assert len(closed) == 1 and len(gaps) == 1
    assert gaps[0]["days"] == 6
    assert habits.load()["routines"][0]["streak"] == 0
    assert habits.load()["today"][rid]["date"] == "2026-09-13"


def test_a_rest_day_has_no_steps_and_keeps_the_streak(habits):
    from datetime import datetime, timezone

    routine = habits.add_routine("Weekdays", days=[0, 1, 2, 3, 4], tz_name="UTC")
    habits.add_step(routine["id"], label="Standup", at="09:00", points=10)
    rid = routine["id"]

    habits.tick(datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc))   # Friday
    habits.resolve(rid, "s1", state="done",
                   moment=datetime(2026, 9, 4, 9, 1, tzinfo=timezone.utc))
    habits.tick(datetime(2026, 9, 5, 6, 0, tzinfo=timezone.utc))   # Saturday

    day = habits.load()["today"][rid]
    assert day["rest"] is True and day["steps"] == {} and day["total"] == 0
    assert habits.load()["routines"][0]["streak"] == 1

    habits.tick(datetime(2026, 9, 6, 6, 0, tzinfo=timezone.utc))   # Sunday
    assert habits.load()["routines"][0]["streak"] == 2


# --- daylight saving -------------------------------------------------------

def test_a_step_inside_the_spring_forward_gap_resolves_forward(habits):
    """02:30 does not exist that day; the step must fire, not vanish."""
    from datetime import datetime

    routine = habits.add_routine("Night", tz_name="Europe/London", day_start="04:00")
    habits.add_step(routine["id"], label="Odd one", at="01:30")
    stored = habits.load()["routines"][0]

    # 2027-03-28: London jumps 01:00 GMT straight to 02:00 BST, so the gap is
    # 01:00–02:00 and 01:30 is the time that does not exist.
    assert habits.in_dst_gap(datetime(2027, 3, 28).date(), 1, 30,
                             habits.zone("Europe/London"))
    due = habits.step_due_at(stored, stored["steps"][0], "2027-03-27")
    local = due.astimezone(habits.zone("Europe/London"))
    assert (local.hour, local.minute) == (2, 30)      # after the transition
    assert habits.dst_note(stored, stored["steps"][0], "2027-03-27")


def test_a_step_inside_the_fall_back_repeat_fires_on_the_first_pass(habits):
    """01:30 happens twice; it must be one step, on the earlier occurrence."""
    from datetime import datetime, timezone

    routine = habits.add_routine("Night", tz_name="Europe/London", day_start="04:00")
    habits.add_step(routine["id"], label="Odd one", at="01:30")
    stored = habits.load()["routines"][0]

    # 2027-10-31: London falls 02:00 BST → 01:00 GMT, so 01:30 occurs twice.
    due = habits.step_due_at(stored, stored["steps"][0], "2027-10-30")
    assert due == datetime(2027, 10, 31, 0, 30, tzinfo=timezone.utc)   # BST, the first
    assert not habits.dst_note(stored, stored["steps"][0], "2027-10-30")


# --- nagging ---------------------------------------------------------------

def test_nags_are_capped_and_escalate(habits):
    rid = _morning(habits)
    for minute in (0, 10, 20, 30, 40, 50):
        habits.tick(_at(6, 7, minute))

    wake = [e for e in habits.sent if "Wake up" in e["text"]]
    assert len(wake) == 3                                    # nag_max
    assert [e["channels"] for e in wake] == [
        ("notify",), ("notify", "speak"), ("notify", "speak", "whatsapp"),
    ]


def test_insist_starts_one_rung_louder_and_never_blocks(habits):
    """`insist` is the sanctioned alternative to hard blocking."""
    routine = habits.add_routine("Meds", tz_name="UTC")
    habits.add_step(routine["id"], label="Take meds", at="07:00", insist=True,
                    nag={"every_minutes": 10, "max": 1})
    habits.tick(_at(6, 7, 0))

    sent = habits.sent[-1]
    assert sent["channels"] == ("notify", "speak")
    assert sent["urgency"] == "critical"


def test_reminder_wording_rotates_through_the_stored_messages(habits):
    """The tick must never call a model to write a reminder."""
    routine = habits.add_routine("Morning", tz_name="UTC")
    habits.add_step(routine["id"], label="Wake up", at="07:00",
                    messages=["Time to get up.", "Morning. Out of bed."],
                    nag={"every_minutes": 10, "max": 3})
    for minute in (0, 10, 20):
        habits.tick(_at(6, 7, minute))
    assert [e["text"] for e in habits.sent] == [
        "Time to get up.", "Morning. Out of bed.", "Time to get up.",
    ]


def test_an_open_step_expires_into_missed(habits):
    rid = _morning(habits)
    habits.tick(_at(6, 7, 0))
    habits.tick(_at(6, 7, 59))
    assert _states(habits, rid)["s1"] == "nagging"
    habits.tick(_at(6, 8, 1))
    assert _states(habits, rid)["s1"] == "missed"


def test_a_zero_window_stays_open_until_the_day_ends(habits):
    routine = habits.add_routine("Loose", tz_name="UTC")
    habits.add_step(routine["id"], label="Whenever", at="07:00", window_minutes=0,
                    nag={"every_minutes": 60, "max": 1})
    rid = routine["id"]
    habits.tick(_at(6, 7, 0))
    habits.tick(_at(6, 23, 0))
    assert _states(habits, rid)["s1"] == "nagging"


# --- scoring and streaks ---------------------------------------------------

def test_available_grows_through_the_day(habits):
    """Scoring the morning against the whole day reads as failure."""
    rid = _morning(habits)
    habits.tick(_at(6, 7, 0))
    day = habits.load()["today"][rid]
    assert (day["score"], day["available"], day["total"]) == (0, 10, 30)

    habits.resolve(rid, "s1", state="done", moment=_at(6, 7, 5))
    day = habits.load()["today"][rid]
    assert (day["score"], day["available"]) == (10, 10)

    habits.tick(_at(6, 7, 40))
    day = habits.load()["today"][rid]
    assert (day["score"], day["available"]) == (10, 20)
    assert day["score"] <= day["available"] <= day["total"]


def test_the_streak_survives_at_eighty_percent(habits):
    """A single missed step ending a streak is too punishing to survive a week."""
    routine = habits.add_routine("Five", tz_name="UTC")
    for hour in (7, 8, 9, 10, 11):
        habits.add_step(routine["id"], label=f"Step {hour}", at=f"{hour:02d}:00",
                        points=10, window_minutes=30, nag={"max": 0})
    rid = routine["id"]

    habits.tick(_at(6, 7, 0))
    for step, hour in zip(("s1", "s2", "s3", "s4"), (7, 8, 9, 10)):
        habits.tick(_at(6, hour, 0))
        habits.resolve(rid, step, state="done", moment=_at(6, hour, 5))
    habits.tick(_at(7, 6, 0))             # roll over with s5 missed: 40/50 = 80%

    closed = [r for r in habits.history(50) if r.get("event") == "closed"][0]
    assert closed["ratio"] == 0.8 and closed["kept_streak"] is True
    assert habits.load()["routines"][0]["streak"] == 1


def test_the_streak_breaks_below_the_threshold(habits):
    routine = habits.add_routine("Five", tz_name="UTC")
    for hour in (7, 8, 9, 10, 11):
        habits.add_step(routine["id"], label=f"Step {hour}", at=f"{hour:02d}:00",
                        points=10, window_minutes=30, nag={"max": 0})
    rid = routine["id"]

    habits.tick(_at(6, 7, 0))
    for step, hour in zip(("s1", "s2", "s3"), (7, 8, 9)):
        habits.tick(_at(6, hour, 0))
        habits.resolve(rid, step, state="done", moment=_at(6, hour, 5))
    habits.tick(_at(7, 6, 0))             # 30/50 = 60%

    closed = [r for r in habits.history(50) if r.get("event") == "closed"][0]
    assert closed["kept_streak"] is False
    assert habits.load()["routines"][0]["streak"] == 0


# --- the import budget -----------------------------------------------------

def test_the_routine_pass_imports_nothing_heavy(habits):
    """Routines ride the tick, so they inherit its budget: 1,440 runs a day."""
    import subprocess
    import sys

    code = (
        "import sys\n"
        "from kravein import routines\n"
        "routines.tick()\n"
        "heavy = sorted(m for m in sys.modules if m.startswith(("
        "'kravein.agent','kravein.llm','kravein.registry','kravein.cline',"
        "'kravein.jobs','google','requests','fastapi','urllib.request')))\n"
        "print('HEAVY:' + ','.join(heavy))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
    ).stdout
    assert "HEAVY:" in out
    loaded = out.split("HEAVY:")[1].strip()
    assert loaded == "", f"the routine pass pulled in heavy modules: {loaded}"


def test_a_steady_state_pass_does_not_load_the_timezone_database(habits, tmp_path):
    """`zoneinfo` costs 5.2ms, and the day's end does not move.

    The first version recomputed the day key from local wall-clock on every
    pass, which meant loading the timezone database 1,440 times a day to ask a
    question whose answer changes at 04:00. The day instance caches its own end
    as a UTC instant instead; this fails if anyone reintroduces the wall-clock
    comparison on the hot path.
    """
    import subprocess
    import sys

    env = dict(os.environ, XDG_STATE_HOME=str(tmp_path / "state"),
               XDG_CONFIG_HOME=str(tmp_path / "cfg"),
               XDG_DATA_HOME=str(tmp_path / "data"))
    setup = (
        "from kravein import routines\n"
        "r = routines.add_routine('Morning', tz_name='Asia/Karachi')\n"
        "routines.add_step(r['id'], label='Wake', at='23:30')\n"
        "routines.tick()\n"
    )
    subprocess.run([sys.executable, "-c", setup], env=env, check=True, timeout=60)

    out = subprocess.run(
        [sys.executable, "-c",
         "import sys\n"
         "from kravein import routines\n"
         "routines.tick()\n"
         "print('TZ:' + ('yes' if 'zoneinfo' in sys.modules else 'no'))\n"],
        env=env, capture_output=True, text=True, timeout=60,
    ).stdout
    assert "TZ:no" in out, "the steady-state routine pass loaded zoneinfo"


def test_a_routine_created_after_its_time_does_not_report_a_miss(habits):
    """A step that did not exist at 07:00 cannot have been missed at 07:00."""
    routine = habits.add_routine("Morning", tz_name="UTC")
    habits.add_step(routine["id"], label="Wake up", at="07:00", points=10)
    rid = routine["id"]

    habits.tick(_at(6, 16, 0))            # created and first seen at 4pm
    day = habits.load()["today"][rid]
    assert day["steps"]["s1"]["state"] == "skipped"
    assert day["steps"]["s1"]["source"] == "day opened late"
    assert (day["score"], day["available"]) == (0, 0)
    assert habits.sent == []


def test_a_machine_switched_off_all_morning_manufactures_no_failures(habits):
    """The laptop being shut is not the user failing a habit."""
    rid = _morning(habits)
    habits.tick(_at(6, 11, 0))            # first tick of the day, hours late
    assert _states(habits, rid)["s1"] == "skipped"
    assert habits.load()["today"][rid]["available"] == 0

    habits.tick(_at(7, 6, 0))             # roll over: a skipped day is not a failed one
    closed = [r for r in habits.history(50) if r.get("event") == "closed"][0]
    assert closed["missed"] == 0 and closed["skipped"] == 3


def test_a_step_due_this_minute_is_not_stood_down(habits):
    """The boundary case the first version of the late-open rule got wrong.

    A step scheduled for the current minute was stood down by the tick that
    ran two seconds later, because the rule compared against the due time
    rather than the end of the window. Caught by running it for real, not by
    the suite — hence this test.
    """
    routine = habits.add_routine("Now", tz_name="UTC")
    habits.add_step(routine["id"], label="Right now", at="07:00", window_minutes=60,
                    nag={"every_minutes": 10, "max": 1})
    rid = routine["id"]

    habits.tick(_at(6, 7, 0) + __import__("datetime").timedelta(seconds=2))
    assert _states(habits, rid)["s1"] == "nagging"
    assert len(habits.sent) == 1


def test_a_deliberate_skip_is_divided_out_of_the_streak(habits):
    """A skip is a decision, not a failure. Scoring it as one kills the button."""
    routine = habits.add_routine("Five", tz_name="UTC")
    for hour in (7, 8, 9, 10, 11):
        habits.add_step(routine["id"], label=f"Step {hour}", at=f"{hour:02d}:00",
                        points=10, window_minutes=30, nag={"max": 0})
    rid = routine["id"]

    habits.tick(_at(6, 7, 0))
    # Three done, one skipped on purpose, one genuinely missed.
    for step, hour in zip(("s1", "s2", "s3"), (7, 8, 9)):
        habits.tick(_at(6, hour, 0))
        habits.resolve(rid, step, state="done", moment=_at(6, hour, 5))
    habits.tick(_at(6, 10, 0))
    habits.resolve(rid, "s4", state="skipped", moment=_at(6, 10, 5))
    habits.tick(_at(7, 6, 0))                      # s5 rolls over as missed

    closed = [r for r in habits.history(50) if r.get("event") == "closed"][0]
    assert closed["total"] == 50 and closed["skipped_points"] == 10
    assert closed["judged"] == 40                  # the skip left the denominator
    assert closed["ratio"] == 0.75                 # 30/40, not 30/50
    assert closed["kept_streak"] is False          # the *miss* still counts


def test_skipping_everything_but_one_step_still_judges_that_step(habits):
    routine = habits.add_routine("Two", tz_name="UTC")
    habits.add_step(routine["id"], label="A", at="07:00", points=10,
                    window_minutes=30, nag={"max": 0})
    habits.add_step(routine["id"], label="B", at="08:00", points=10,
                    window_minutes=30, nag={"max": 0})
    rid = routine["id"]

    habits.tick(_at(6, 7, 0))
    habits.resolve(rid, "s1", state="done", moment=_at(6, 7, 5))
    habits.tick(_at(6, 8, 0))
    habits.resolve(rid, "s2", state="skipped", moment=_at(6, 8, 5))
    habits.tick(_at(7, 6, 0))

    closed = [r for r in habits.history(50) if r.get("event") == "closed"][0]
    assert closed["judged"] == 10 and closed["ratio"] == 1.0
    assert closed["kept_streak"] is True


# ---------------------------------------------------------------------------
