"""Tests: jobs.

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


# Scheduled jobs
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the job store at a temp dir, and never deliver for real."""
    from kravein import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "STORE", tmp_path / "jobs.json")
    monkeypatch.setattr(jobs_mod, "LOCK", tmp_path / "jobs.lock")
    monkeypatch.setattr(jobs_mod, "HISTORY", tmp_path / "job-runs.jsonl")
    monkeypatch.setattr(jobs_mod, "deliver", lambda *a, **k: None)
    return jobs_mod


def test_tick_imports_nothing_heavy(store):
    """The tick runs 1,440 times a day; it must not load the agent to do it.

    Routing it through `cli.py` would cost ~250ms a tick because that module
    imports the Agent, the LLM facade and the registry at module level. This
    asserts the cheap path stays cheap.
    """
    import subprocess
    import sys

    code = (
        "import sys\n"
        "from kravein import jobs\n"
        "jobs.main(['tick'])\n"
        "heavy = sorted(m for m in sys.modules if m.startswith(("
        "'kravein.agent','kravein.llm','kravein.registry','kravein.cline',"
        "'google','requests','fastapi')))\n"
        "print('HEAVY:' + ','.join(heavy))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
    ).stdout
    assert "HEAVY:" in out
    loaded = out.split("HEAVY:")[1].strip()
    assert loaded == "", f"tick pulled in heavy modules: {loaded}"


def test_a_running_job_is_not_started_twice(store):
    """Overlap prevention: an agent turn easily outlives its own tick."""
    import threading
    import time as _time

    starts = []

    def slow(job):
        starts.append(job["id"])
        _time.sleep(2)
        return True, "done", ""

    store.run_job = slow
    store.add(prompt="slow", when=store.now_utc(), kind="agent")

    first = threading.Thread(target=store.tick)
    first.start()
    _time.sleep(0.6)

    # The state must already say running, before any Agent was built.
    live = store.all_jobs()[0]
    assert live["state"] == "running"
    assert live["running_pid"]

    assert store.tick() == 0, "a second tick started the same job again"
    first.join()
    assert starts == ["j1"]


def test_a_dead_runner_is_reaped_and_retried(store):
    """A crashed run must not leave the job wedged in 'running' for ever."""
    store.run_job = lambda job: (True, "ok", "")
    job = store.add(prompt="x", when=store.now_utc(), kind="notify")
    store._update(
        job["id"], state="running", running_pid=999999,
        running_since=store.iso(store.now_utc()),
    )

    store.tick()
    after = store.all_jobs()[0]
    assert after["state"] == "pending", "dead runner left the job stuck"
    assert after["attempts"] == 1


def test_a_stale_job_is_skipped_rather_than_fired_late(store):
    """A laptop shut for a week must not wake up and deliver a backlog."""
    fired = []
    store.run_job = lambda job: (fired.append(job["id"]), (True, "ok", ""))[1]

    from datetime import timedelta

    store.add(prompt="stale", when=store.now_utc() - timedelta(hours=2), kind="notify")
    recurring = store.add(prompt="daily", cron="0 9 * * *", kind="notify")
    store._update(recurring["id"], next_run=store.iso(store.now_utc() - timedelta(hours=2)))

    store.tick()
    assert fired == [], "fired a job that was hours late"

    states = {j["id"]: j["state"] for j in store.all_jobs()}
    assert states["j1"] == "missed"
    # The recurring one lives on, pointed at its next occurrence.
    assert states["j2"] == "pending"
    assert store.parse_iso(
        [j for j in store.all_jobs() if j["id"] == "j2"][0]["next_run"]
    ) > store.now_utc()


def test_a_recurring_job_reschedules_itself(store):
    store.run_job = lambda job: (True, "ok", "")
    job = store.add(prompt="daily", cron="*/5 * * * *", kind="notify")
    store._update(job["id"], next_run=store.iso(store.now_utc()))

    store.tick()
    after = store.all_jobs()[0]
    assert after["state"] == "pending"
    assert after["runs"] == 1
    assert store.parse_iso(after["next_run"]) > store.now_utc()


def test_transient_failures_back_off_and_persist(store):
    """Retry state lives in the store, so a reboot mid-backoff loses nothing."""
    store.run_job = lambda job: (False, "", "503 service unavailable")
    job = store.add(prompt="x", when=store.now_utc(), kind="agent")

    store.tick()
    after = store.all_jobs()[0]
    assert after["state"] == "pending"
    assert after["attempts"] == 1
    assert store.parse_iso(after["next_run"]) > store.now_utc()

    # A permanent failure does not get retried at all.
    store.run_job = lambda job: (False, "", "no such skill")
    store._update(job["id"], next_run=store.iso(store.now_utc()), attempts=0)
    store.tick()
    assert store.all_jobs()[0]["state"] == "failed"


def test_the_model_cannot_confirm_its_own_destructive_action(monkeypatch, tmp_path):
    """A scheduled job refused on `delete_path` retried as `run_command
    "rm -f …"` with confirm=true, and the file was gone — from a session with
    no confirmer at all. `confirm` inside the model's params is now ignored;
    only a trusted caller's keyword argument counts.
    """
    from kravein import registry, safety

    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")

    registry.load_skills()
    with safety.confirmer_scope(None):
        result = registry.dispatch(
            "run_command",
            {"command": f"rm -f {victim}", "confirm": True},
            source="schedule:test",
        )

    assert victim.exists(), f"model self-approved a destructive action: {result}"


def test_an_unattended_refusal_is_remembered_for_the_turn(monkeypatch):
    """Refusing without recording left the retry-under-another-verb path open."""
    from kravein.safety import (
        ConfirmationRequired, Danger, Verdict, confirmer_scope, describe_targets, enforce,
    )

    verdict = Verdict(danger=Danger.DESTRUCTIVE, reasons=["deletes /tmp/x"])
    targets = describe_targets("path:/tmp/x")

    with confirmer_scope(None):
        with pytest.raises(ConfirmationRequired):
            enforce(verdict, "delete /tmp/x", skill="delete_path", targets=targets)

        # The same target under a different verb must now be refused outright.
        with pytest.raises(Exception) as caught:
            enforce(
                Verdict(danger=Danger.DESTRUCTIVE, reasons=["rm -f /tmp/x"]),
                "run `rm -f /tmp/x`", skill="run_command", targets=targets,
            )
        assert "already declined" in str(caught.value)


def test_parse_when_does_not_try_to_be_an_nl_parser(store):
    """Prose is the model's job; this only validates machine formats.

    An earlier version understood "5pm" and "tomorrow 17:00" — a second NL
    parser competing with the model's, and one that would have failed on "next
    Tuesday" or "in two weeks" anyway. Better no fuzzy branch than a third of
    one.
    """
    assert store.parse_when("2026-09-07T17:00", "UTC").hour == 17
    assert store.parse_when("2026-09-07T12:00:00Z", "UTC").hour == 12
    assert store.parse_when("20m", "UTC") > store.now_utc()

    for prose in ("5pm", "tomorrow 17:00", "next Tuesday", "in two weeks",
                  "day after tomorrow", "today 23:30"):
        assert store.parse_when(prose, "UTC") is None, (
            f"{prose!r} was parsed here; it must reach the model instead"
        )


def test_the_clock_reaches_the_model_without_touching_the_system_prompt():
    """The model cannot resolve "next Tuesday" without knowing today's date.

    The obvious home for a clock is the system prompt, and that is exactly
    wrong: the system prompt is the cached prefix, and a value that changes
    every second would invalidate it on every request. It goes at the end of
    the messages instead, where the tail already differs each turn.
    """
    from kravein.agent import Agent

    agent = Agent(source="test")
    agent.messages = [{"role": "user", "content": "remind me next Tuesday"}]

    outgoing = agent._with_context()

    assert len(outgoing) == len(agent.messages) + 1
    assert outgoing[:-1] == agent.messages, "the cached prefix was modified"

    note = outgoing[-1]["content"]
    assert "[context]" in note
    assert "Current date and time" in note
    assert str(__import__("datetime").datetime.now().year) in note

    # Never stored: replayed history must not carry a stale timestamp.
    assert len(agent.messages) == 1

    # And the static prompt stays static.
    first, second = agent.system_prompt(), agent.system_prompt()
    assert first == second
    assert "Current date and time" not in first


def test_a_recycled_pid_does_not_look_like_a_live_runner(store, monkeypatch):
    """Linux reuses pids. A crashed run whose number now belongs to a browser
    tab would otherwise stay "running" for ever — never reaped, never retried.
    """
    import subprocess

    store.run_job = lambda job: (True, "ok", "")
    job = store.add(prompt="x", when=store.now_utc(), kind="notify")

    # A real, live process that is emphatically not a Kravein runner.
    other = subprocess.Popen(["sleep", "30"])
    try:
        store._update(
            job["id"], state="running", running_pid=other.pid,
            running_since=store.iso(store.now_utc()),
        )
        assert store._pid_alive(other.pid) is False, "identity was not checked"

        store.tick()
        after = store.all_jobs()[0]
        assert after["state"] != "running", "a recycled pid kept the job wedged"
    finally:
        other.kill()
        other.wait()


def test_waking_from_suspend_does_not_fire_a_thundering_herd(store):
    """A laptop shut overnight must not deliver every missed job at once."""
    from datetime import timedelta

    fired = []
    store.run_job = lambda job: (fired.append(job["id"]), (True, "ok", ""))[1]

    # Eight hours asleep: a batch of one-shots and a daily job all overdue.
    for hours in (8, 6, 4, 2, 1):
        store.add(
            prompt=f"reminder {hours}h ago",
            when=store.now_utc() - timedelta(hours=hours), kind="notify",
        )
    daily = store.add(prompt="daily", cron="0 9 * * *", kind="notify")
    store._update(daily["id"], next_run=store.iso(store.now_utc() - timedelta(hours=7)))

    # One inside the grace window, which *should* still fire.
    store.add(
        prompt="just missed", when=store.now_utc() - timedelta(minutes=3), kind="notify",
    )

    store.tick()

    assert len(fired) == 1, f"woke up and fired a herd: {fired}"
    states = {j["id"]: j["state"] for j in store.all_jobs()}
    assert sorted(v for v in states.values() if v == "missed") == ["missed"] * 5
    assert states[daily["id"]] == "pending"
    assert store.parse_iso(
        [j for j in store.all_jobs() if j["id"] == daily["id"]][0]["next_run"]
    ) > store.now_utc()


def test_a_paused_job_keeps_its_schedule_but_never_fires(store):
    """Pause is not cancel. "Not now" and "never again" are different, and
    conflating them means silencing a recurring job for a week requires
    deleting it and rebuilding it from memory.
    """
    fired = []
    store.run_job = lambda job: (fired.append(job["id"]), (True, "ok", ""))[1]

    job = store.add(prompt="daily", cron="*/1 * * * *", kind="notify")
    store._update(job["id"], next_run=store.iso(store.now_utc()))
    due_before = store.all_jobs()[0]["next_run"]

    assert store.set_paused(job["id"], True) == "paused"
    store.tick()
    assert fired == [], "a paused job fired"
    # The schedule is untouched, so resuming is not a reschedule.
    assert store.all_jobs()[0]["next_run"] == due_before

    assert store.set_paused(job["id"], False) == "pending"
    store.tick()
    assert fired == [job["id"]]

    assert store.set_paused("nope", True) == "", "invented a job that does not exist"


def test_editing_changes_only_what_is_passed(store):
    """Editing the message must not require restating the schedule."""
    job = store.add(prompt="old text", cron="0 9 * * *", kind="notify", title="Old")
    before = store.all_jobs()[0]["next_run"]

    updated = store.edit(job["id"], prompt="new text")
    assert updated["prompt"] == "new text"
    assert updated["cron"] == "0 9 * * *", "clearing the schedule was not asked for"
    assert updated["title"] == "Old"
    assert updated["next_run"] == before

    # A new cron recomputes the next run and clears any retry backoff.
    store._update(job["id"], attempts=2)
    updated = store.edit(job["id"], cron="0 17 * * 5")
    assert updated["cron"] == "0 17 * * 5"
    assert updated["attempts"] == 0

    # Giving an absolute time turns a repeating job into a one-shot.
    from datetime import timedelta

    updated = store.edit(job["id"], when=store.now_utc() + timedelta(days=1))
    assert updated["cron"] == ""

    with pytest.raises(ValueError):
        store.edit(job["id"], cron="not a cron")
    assert store.edit("nope", prompt="x") is None


def test_editing_a_finished_job_puts_it_back_in_play(store):
    """Reusing a job you already ran should not mean retyping it."""
    store.run_job = lambda job: (True, "ok", "")
    job = store.add(prompt="one shot", when=store.now_utc(), kind="notify")
    store.tick()
    assert store.all_jobs()[0]["state"] == "done"

    from datetime import timedelta

    updated = store.edit(job["id"], when=store.now_utc() + timedelta(hours=2))
    assert updated["state"] == "pending"


# ---------------------------------------------------------------------------
