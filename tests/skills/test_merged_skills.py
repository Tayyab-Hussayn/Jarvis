"""Merged skills (#133): one skill with an ``action``, old names kept as aliases.

Every old tool name must still dispatch to the same plain function with the
same arguments (the alias preset supplies the action), aliases must stay out
of the model's tool list, and the dangerous actions must still reach
``safety.enforce`` where they always did.
"""

from __future__ import annotations

import functools
import importlib
import inspect
import json

import pytest

from kravein import audit, registry, safety

S = "kravein.skills."

# old tool name, params the old caller sent, (module, plain function it must reach),
# the arguments that function must receive (None = only check it was reached).
CASES = [
    ("get_volume", {}, "settings", "get_volume", {}),
    ("set_volume", {"percent": 40}, "settings", "set_volume", {"percent": 40}),
    ("change_volume", {}, "settings", "change_volume", {}),
    ("change_volume", {"delta": -5}, "settings", "change_volume", {"delta": -5}),
    ("mute_audio", {"state": True}, "settings", "mute_audio", {"state": True}),
    ("mute_audio", {}, "settings", "mute_audio", {"state": None}),
    ("get_brightness", {}, "settings", "get_brightness", {}),
    ("set_brightness", {"percent": 30}, "settings", "set_brightness", {"percent": 30}),
    ("change_brightness", {"delta": 20}, "settings", "change_brightness", {"delta": 20}),
    ("list_audio_devices", {}, "settings", "list_audio_devices", {}),
    ("list_monitors", {}, "settings", "list_monitors", {}),
    ("now_playing", {}, "settings", "now_playing", {}),
    ("list_wifi_networks", {"rescan": True}, "settings", "list_wifi_networks", {"rescan": True}),
    ("stop_gentle_scroll", {}, "gentle", "stop_gentle_scroll", {}),
    ("gentle_scroll_speed", {"speed": "3"}, "gentle", "gentle_scroll_speed", {"speed": "3"}),
    ("gentle_scroll_status", {}, "gentle", "gentle_scroll_status", {}),
    ("click_mouse", {"button": "right"}, "interact", "click_mouse", {"button": "right"}),
    ("move_mouse", {"x": 1, "y": 2}, "interact", "move_mouse", {"x": 1, "y": 2}),
    ("read_clipboard", {}, "interact", "read_clipboard", {}),
    ("write_clipboard", {"text": "hi"}, "interact", "write_clipboard", {"text": "hi"}),
    ("input_status", {}, "interact", "input_status", {}),
    ("set_reminder", {"message": "tea"}, "notifications", "set_reminder", {"message": "tea"}),
    ("list_reminders", {}, "notifications", "list_reminders", {}),
    ("cancel_reminders", {}, "notifications", "cancel_reminders", None),
    ("notification_status", {}, "notifications", "notification_status", {}),
    ("recent_notifications", {"limit": 3}, "notifications", "recent_notifications", {"limit": 3}),
    ("watch_notifications", {"enabled": False}, "notifications", "watch_notifications", {"enabled": False}),
    ("spotify_play", {}, "media", "spotify_play", {}),
    ("spotify_play", {"what": "jazz"}, "media", "spotify_play", {"what": "jazz"}),
    ("spotify_status", {}, "media", "spotify_status", {}),
    ("set_wallpaper", {"path": "/x.png"}, "media", "set_wallpaper", None),
    ("wallpaper_status", {}, "media", "wallpaper_status", {}),
    ("remember", {"key": "k", "value": "v"}, "memory", "remember", {"key": "k", "value": "v"}),
    ("add_routine_step", {"routine_id": "r1", "label": "L", "at": "07:00"},
     "routines", "add_routine_step", {"routine_id": "r1", "label": "L", "at": "07:00"}),
    ("edit_routine_step", {"routine_id": "r1", "step_id": "s1", "points": 5},
     "routines", "edit_routine_step", {"routine_id": "r1", "step_id": "s1", "points": 5}),
    ("delete_routine_step", {"routine_id": "r1", "step_id": "s1"},
     "routines", "delete_routine_step", {"routine_id": "r1", "step_id": "s1"}),
    ("complete_step", {"routine_id": "r1", "step_id": "s1"},
     "routines", "complete_step", {"routine_id": "r1", "step_id": "s1"}),
    ("skip_step", {"routine_id": "r1", "step_id": "s1"},
     "routines", "skip_step", {"routine_id": "r1", "step_id": "s1"}),
    ("add_routine", {"name": "Morning"}, "routines", "add_routine", {"name": "Morning"}),
    ("pause_routine", {"routine_id": "r1", "resume": True},
     "routines", "pause_routine", {"routine_id": "r1", "resume": True}),
    ("routine_history", {"limit": 3}, "routines", "routine_history", {"limit": 3}),
    ("job_history", {"limit": 5}, "schedule", "job_history", {"limit": 5}),
    ("schedule_status", {}, "schedule", "schedule_status", {}),
    ("cancel_job", {"job_id": "j1"}, "schedule", "cancel_job", {"job_id": "j1"}),
    ("pause_job", {"job_id": "j1", "resume": True}, "schedule", "pause_job",
     {"job_id": "j1", "resume": True}),
    ("edit_job", {"job_id": "j1", "when": "5pm"}, "schedule", "edit_job",
     {"job_id": "j1", "when": "5pm"}),
    ("browser_status", {}, "browser", "browser_status", {}),
    ("browser_switch_tab", {"index": 2}, "browser", "browser_switch_tab", {"index": 2}),
    ("browser_close_tab", {}, "browser", "browser_close_tab", {}),
    ("browser_close_tab", {"index": 3}, "browser", "browser_close_tab", {"index": 3}),
    ("open_file_manager", {"path": "/tmp"}, "filemanager", "open_file_manager", {"path": "/tmp"}),
    ("show_in_file_manager", {"path": "/tmp/a"}, "filemanager", "show_in_file_manager", {"path": "/tmp/a"}),
    ("file_manager_status", {}, "filemanager", "file_manager_status", {}),
    ("open_path", {"path": "/tmp/a"}, "files", "open_path", {"path": "/tmp/a"}),
    ("focused_window", {}, "windows", "focused_window", {}),
    ("list_workspaces", {}, "windows", "list_workspaces", {}),
    ("toggle_fullscreen", {}, "windows", "toggle_fullscreen", {}),
    ("toggle_floating", {}, "windows", "toggle_floating", {}),
    ("llm_status", {}, "llm_admin", "llm_status", {}),
    ("reset_llm_failover", {}, "llm_admin", "reset_llm_failover", {}),
    ("list_llm_models", {"filter_text": "x"}, "llm_admin", "list_llm_models", {"filter_text": "x"}),
    ("set_llm_model", {"model": "m"}, "llm_admin", "set_llm_model", {"model": "m"}),
    ("set_llm_provider", {"provider": "p"}, "llm_admin", "set_llm_provider", {"provider": "p"}),
]

ALIASES = sorted({c[0] for c in CASES} | {
    "get_volume", "suspend_system", "reboot_system", "shutdown_system", "log_out",
})


@pytest.fixture(autouse=True)
def _available(monkeypatch):
    # media_control/wifi/xdg-open need binaries this test machine may lack.
    monkeypatch.setattr(registry.Skill, "available", lambda self: (True, ""))


@pytest.mark.parametrize("old,params,module,func,expected", CASES,
                         ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)])
def test_old_name_reaches_the_same_function_with_the_same_arguments(
        monkeypatch, old, params, module, func, expected):
    mod = importlib.import_module(S + module)
    real = getattr(mod, func)
    seen = []

    @functools.wraps(real)  # keeps the signature visible to _merge.run
    def fake(*a, **k):
        seen.append(inspect.signature(real).bind(*a, **k).arguments)
        return "REACHED"

    monkeypatch.setattr(mod, func, fake)
    out = registry.dispatch(old, dict(params))
    assert out == "REACHED", out
    if expected is not None:
        got = {k: v for k, v in seen[0].items() if not k.startswith("_")}
        assert got == expected


def test_aliases_resolve_but_are_not_in_the_tool_schema():
    shown = {d["name"] for d in registry.tool_declarations()}
    for old in ALIASES:
        assert old not in shown or registry.get_skill(old).name == old, old
        assert registry.get_skill(old) is not None, old
    # The names that stay visible are the merged ones.
    for merged in ("volume", "brightness", "power_session", "manage_job", "routine_step",
                   "routine", "reminder", "notifications", "spotify", "wallpaper",
                   "memory", "llm_control", "mouse", "clipboard", "browser_tab",
                   "file_manager", "window_state", "list_devices"):
        assert merged in shown
    for gone in ("set_volume", "pause_job", "cancel_job", "skip_step", "toggle_floating",
                 "shutdown_system", "set_llm_model", "spotify_status"):
        assert gone not in shown


def test_the_preset_decides_the_action_not_the_caller(monkeypatch):
    # pause_job must never become a cancel because the caller sent action=cancel.
    from kravein.skills import schedule
    seen = []
    monkeypatch.setattr(schedule, "cancel_job", lambda *a, **k: seen.append("cancel") or "c")
    monkeypatch.setattr(schedule, "pause_job", lambda *a, **k: seen.append("pause") or "p")
    registry.dispatch("pause_job", {"job_id": "j1", "action": "cancel"})
    assert seen == ["pause"]


def test_tool_count_dropped_at_least_a_quarter():
    # 169 tools (167 + edit_file + search_text) before the merge.
    # 126 until #218 added command_job (status/stop of background commands, one tool
    # with two alias names). Raise only with a reason, and merge before adding.
    assert len(registry.tool_declarations()) <= 127


def test_merged_skills_do_not_use_the_unenforced_tripwire_wrongly():
    # Declared tier >= DESTRUCTIVE is only allowed where every action enforces.
    for name in ("volume", "brightness", "manage_job", "reminder", "routine_step",
                 "routine", "file_manager", "llm_control", "memory"):
        assert registry.get_skill(name).danger < safety.Danger.DESTRUCTIVE
    assert registry.get_skill("power_session").danger == safety.Danger.CRITICAL
    # Reads that must stay apart from destructive actions.
    assert registry.get_skill("list_processes").danger == safety.Danger.SAFE
    assert registry.get_skill("kill_process").danger >= safety.Danger.DESTRUCTIVE
    assert registry.get_skill("cancel_shutdown") is not None


@pytest.mark.parametrize("old,args,plain", [
    ("suspend_system", {}, "suspend"),
    ("reboot_system", {"delay_seconds": 5}, "reboot"),
    ("shutdown_system", {}, "power_off"),
    ("log_out", {}, "logout"),
])
def test_power_actions_still_ask_and_do_nothing_when_nobody_can_confirm(
        monkeypatch, old, args, plain):
    from kravein.platform import power
    ran = []
    monkeypatch.setattr(power, plain, lambda *a, **k: ran.append(plain) or "DONE")
    records = []
    monkeypatch.setattr(audit, "record", lambda event, **f: records.append((event, f)))
    out = registry.dispatch(old, dict(args))
    assert ran == [], out                       # refused: no confirmer in this thread
    assert "skill.unenforced" not in [e for e, _ in records]


def test_power_action_runs_when_confirmed(monkeypatch):
    from kravein.platform import power
    monkeypatch.setattr(power, "reboot", lambda delay_seconds=0: f"REBOOT {delay_seconds}")
    out = registry.dispatch("reboot_system", {"delay_seconds": 5}, confirm=True)
    assert out == "REBOOT 5"
    assert registry.dispatch("power_session", {"action": "reboot"}, confirm=True) == "REBOOT 0"


def test_cancel_job_and_cancel_reminders_still_reach_enforce(monkeypatch):
    from kravein.skills import schedule
    calls = []
    monkeypatch.setattr(schedule, "enforce", lambda verdict, desc, **kw: calls.append((desc, kw)))
    monkeypatch.setattr(schedule.jobs, "all_jobs",
                        lambda: [{"id": "j1", "state": "pending", "prompt": "p", "title": "t",
                                  "when": "x", "kind": "notify", "next_run": None}])
    monkeypatch.setattr(schedule.jobs, "cancel", lambda *a, **k: True, raising=False)
    registry.dispatch("manage_job", {"action": "cancel", "job_id": ""}, confirm=True)
    assert calls, "cancelling every job must go through enforce"


def test_bad_or_missing_arguments_are_refused_not_guessed():
    assert "needs" in registry.dispatch("volume", {"action": "set"})
    assert "needs" in registry.dispatch("mouse", {"action": "move", "x": 1})
    assert "needs" in registry.dispatch("manage_job", {"action": "pause"})
    assert "needs" in registry.dispatch("routine_step", {"action": "complete", "routine_id": "r1"})
    assert "needs" in registry.dispatch("file_manager", {"action": "show"})
    assert "needs" in registry.dispatch("memory", {"action": "remember", "key": "k"})
    assert "needs" in registry.dispatch("browser_tab", {"action": "switch"})
    assert "needs" in registry.dispatch("llm_control", {"action": "set_model"})


def test_every_merged_action_is_documented_in_the_schema():
    by_name = {d["name"]: d for d in registry.tool_declarations()}
    for name in ("volume", "brightness", "reminder", "notifications", "routine_step",
                 "routine", "manage_job", "file_manager", "llm_control", "memory", "mouse"):
        props = by_name[name]["parameters"]["properties"]
        action = props["action"]
        assert action.get("enum") or "(" in action["description"], name
        # Wrapped Args: lines must reach the model whole.
        assert all(p["description"].strip() for p in props.values()), name
    text = json.dumps(by_name["manage_job"])
    assert "EVERY pending job" in text
