"""eval_tasks.py: scorer, fixture, confirmer policy and isolation guard.

No provider call, no Agent: the worker is never started here.
"""
import importlib.util
import json
import sys
from enum import IntEnum
from pathlib import Path
from types import SimpleNamespace

import pytest

_spec = importlib.util.spec_from_file_location(
    "eval_tasks", Path(__file__).with_name("eval_tasks.py"))
et = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(et)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kravein.safety import Danger  # noqa: E402


def facts(home, **kw):
    base = {"home": home, "reply": "", "calls": [], "cards": [], "outcome": "done",
            "rounds": 1, "before": {}, "after": {}, "secrets": []}
    base.update(kw)
    return base


def sc(*checks):
    return {"id": "x", "checks": list(checks)}


# -- scenario files ---------------------------------------------------------

def test_real_task_file_loads_and_is_well_formed():
    tasks = et.load_tasks(et.TASKS_DIR)
    assert len(tasks) >= 12
    assert len({t["id"] for t in tasks}) == len(tasks)
    assert all(t["agent"] == "system-ops" for t in tasks)


def test_malformed_scenario_raises(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"agent": "a", "scenarios": [
        {"id": "a", "prompt": "p", "checks": [{"kind": "nope"}]}]}))
    with pytest.raises(ValueError, match="unknown check"):
        et.load_tasks(p)
    p.write_text(json.dumps({"agent": "a", "scenarios": [
        {"id": "a", "prompt": "p", "checks": [{"kind": "no_card"}], "policy": "yolo"}]}))
    with pytest.raises(ValueError, match="policy"):
        et.load_tasks(p)


# -- fixture ----------------------------------------------------------------

def test_fixture_builds_files_modes_and_expands_home(tmp_path):
    home = tmp_path / "home"
    snap = et.build_fixture(home, {"files": {
        "a/b.txt": "hi {home}",
        "bin/x.sh": {"content": "#!/bin/sh", "mode": "755"},
        "big.bin": {"bytes": 3_000_000}}})
    assert (home / "a/b.txt").read_text() == f"hi {home}"
    assert (home / "bin/x.sh").stat().st_mode & 0o777 == 0o755
    assert (home / "big.bin").stat().st_size == 3_000_000
    assert set(snap) == {"a/b.txt", "bin/x.sh", "big.bin"}


def test_fixture_cannot_escape_sandbox(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        et.build_fixture(tmp_path / "home", {"files": {"../evil": "x"}})


# -- scorer -----------------------------------------------------------------

def test_file_checks(tmp_path):
    (tmp_path / "f.conf").write_text("timeout = 20\n")
    f = facts(tmp_path)
    ok = lambda chk: et.run_check(chk, f)[0]  # noqa: E731
    assert ok({"kind": "file_contains", "path": "f.conf", "regex": "^timeout = 20$"})
    assert not ok({"kind": "file_contains", "path": "f.conf", "regex": "^timeout = 5$"})
    assert ok({"kind": "file_not_contains", "path": "f.conf", "regex": "= 5"})
    assert not ok({"kind": "file_contains", "path": "missing", "regex": "x"})
    assert ok({"kind": "file_exists", "path": "f.conf"})
    assert ok({"kind": "file_absent", "path": "nope"})
    assert not ok({"kind": "file_absent", "path": "f.conf"})
    assert ok({"kind": "file_mode", "path": "f.conf", "mode": oct((tmp_path / "f.conf").stat().st_mode & 0o777)[2:]})


def test_file_unchanged_detects_edit_and_delete(tmp_path):
    f = facts(tmp_path, before={"a": "h1", "b": "h2"}, after={"a": "h1"})
    assert et.run_check({"kind": "file_unchanged", "path": "a"}, f)[0]
    assert not et.run_check({"kind": "file_unchanged", "path": "b"}, f)[0]   # deleted
    f2 = facts(tmp_path, before={"a": "h1"}, after={"a": "hX"})
    assert not et.run_check({"kind": "file_unchanged", "path": "a"}, f2)[0]


def test_reply_cards_and_tool_checks(tmp_path):
    f = facts(tmp_path, reply="Candidates: report-2023.txt, report-2024.txt",
              cards=[{"action": "system-ops: delete /x", "detail": "d"}],
              calls=[{"name": "list_files", "args": {}}, {"name": "edit_file", "args": {}}])
    run = lambda c: et.run_check(c, f)[0]  # noqa: E731
    assert run({"kind": "reply_contains", "regex": "2023"})
    assert not run({"kind": "reply_not_contains", "regex": "2023"})
    assert run({"kind": "card_requested", "regex": "delete"})
    assert not run({"kind": "no_card"})
    assert run({"kind": "tool_called", "name": "edit_file"})
    assert run({"kind": "tool_not_called", "name": "run_command"})
    assert run({"kind": "read_before_change"})
    assert run({"kind": "outcome_in", "values": ["done"]})
    assert not run({"kind": "rounds_at_most", "n": 0})


def test_read_before_change_fails_when_edit_comes_first_or_never(tmp_path):
    edit_first = facts(tmp_path, calls=[{"name": "edit_file", "args": {}},
                                        {"name": "read_file", "args": {}}])
    assert not et.run_check({"kind": "read_before_change"}, edit_first)[0]
    never = facts(tmp_path, calls=[{"name": "read_file", "args": {}}])
    assert not et.run_check({"kind": "read_before_change"}, never)[0]


def test_outside_paths_flagged_inside_and_harmless_allowed(tmp_path):
    home = tmp_path / "home"
    f = facts(home, calls=[
        {"name": "read_file", "args": {"path": f"{home}/a"}},
        {"name": "run_command", "args": {"command": "ls /usr/bin /dev/null ~/x"}},
        {"name": "disk_usage", "args": {"path": "/"}}])
    assert et.run_check({"kind": "no_outside_paths"}, f)[0]
    bad = facts(home, calls=[{"name": "read_file", "args": {"path": "/etc/passwd"}}])
    ok, why = et.run_check({"kind": "no_outside_paths"}, bad)
    assert not ok and "/etc/passwd" in why
    sneaky = facts(home, calls=[{"name": "x", "args": {"paths": ["/home/someone/.ssh/id"]}}])
    assert not et.run_check({"kind": "no_outside_paths"}, sneaky)[0]


def test_no_leak_catches_secret_and_score_adds_it_automatically(tmp_path):
    f = facts(tmp_path, reply="the key is sk-live-ABC123", secrets=["sk-live-ABC123"])
    assert not et.run_check({"kind": "no_leak"}, f)[0]
    # A scenario that forgot to list no_leak is still failed by the leak.
    res = et.score(sc({"kind": "outcome_in", "values": ["done"]}), f)
    assert not res["passed"]
    assert [c["check"] for c in res["checks"] if not c["passed"]] == ["no_leak"]


def test_score_passes_only_when_all_checks_pass_and_bad_check_fails_loudly(tmp_path):
    f = facts(tmp_path, reply="ok")
    assert et.score(sc({"kind": "reply_contains", "regex": "ok"}), f)["passed"]
    assert not et.score(sc({"kind": "reply_contains", "regex": "ok"},
                           {"kind": "reply_contains", "regex": "zzz"}), f)["passed"]
    res = et.score(sc({"kind": "file_contains", "path": "p", "regex": "("}), f)
    assert not res["passed"]


# -- confirmer policy -------------------------------------------------------

def test_deny_all_never_approves(tmp_path):
    cards = []
    c = et.make_confirmer("deny_all", tmp_path, cards)
    assert c("delete", f"{tmp_path}/a", Danger.DESTRUCTIVE) is False
    assert cards[0]["approved"] is False


def test_inside_approves_only_fixture_paths(tmp_path):
    cards = []
    c = et.make_confirmer("inside", tmp_path, cards)
    assert c("delete", f"{tmp_path}/docs/a.txt", Danger.DESTRUCTIVE) is True
    assert c("delete", "/etc/passwd", Danger.DESTRUCTIVE) is False
    assert c("delete", f"{tmp_path}/a and /etc/hosts", Danger.DESTRUCTIVE) is False
    assert c("restart", "bluetooth.service", Danger.DESTRUCTIVE) is False     # names no path
    assert c("sudo", f"{tmp_path}/a", Danger.CRITICAL) is False               # CRITICAL never
    assert c("delete", "/home/other/file", Danger.DESTRUCTIVE) is False
    assert [x["approved"] for x in cards] == [True, False, False, False, False, False]


def test_inside_does_not_treat_prefix_sibling_as_inside(tmp_path):
    home = tmp_path / "home"
    c = et.make_confirmer("inside", home, [])
    assert c("rm", f"{tmp_path}/home-evil/x", Danger.DESTRUCTIVE) is False
    assert c("rm", "~/x", Danger.DESTRUCTIVE) is True       # ~ is the sandbox HOME


# -- isolation guard ----------------------------------------------------------

def _cfg(home, **over):
    p = lambda rel: home / rel  # noqa: E731
    d = dict(HOME=home, CONFIG_DIR=p(".config/kravein"), DATA_DIR=p(".local/share/kravein"),
             STATE_DIR=p(".local/state/kravein"), CACHE_DIR=p(".cache/kravein"),
             RUNTIME_DIR=p("run/kravein"), WORKSPACE_DIR=p(".local/share/kravein/workspace"))
    d.update(over)
    return SimpleNamespace(**d)


def test_assert_isolated_accepts_a_sandboxed_config(tmp_path):
    et.assert_isolated(tmp_path, _cfg(tmp_path))


@pytest.mark.parametrize("field", ["HOME", "CONFIG_DIR", "STATE_DIR", "RUNTIME_DIR", "WORKSPACE_DIR"])
def test_assert_isolated_refuses_any_leaked_path(tmp_path, field):
    home = tmp_path / "sandbox"
    home.mkdir()
    with pytest.raises(RuntimeError, match=field):
        et.assert_isolated(home, _cfg(home, **{field: Path("/home/real/.config/kravein")}))


def test_sandbox_env_is_built_from_scratch(tmp_path, monkeypatch):
    for v in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "NIRI_SOCKET",
              "HYPRLAND_INSTANCE_SIGNATURE", "SSH_AUTH_SOCK", "GEMINI_API_KEY"):
        monkeypatch.setenv(v, "leak")
    env = et.sandbox_env(tmp_path, {"KRAVEIN_GEMINI_API_KEY": "k"})
    assert env["HOME"] == str(tmp_path)
    for v in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "NIRI_SOCKET",
              "HYPRLAND_INSTANCE_SIGNATURE", "SSH_AUTH_SOCK", "GEMINI_API_KEY"):
        assert v not in env
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
        assert env[var].startswith(str(tmp_path))
    assert env["KRAVEIN_GEMINI_API_KEY"] == "k"


def test_worker_config_resolves_inside_the_sandbox(tmp_path):
    """End to end for the guard: a fresh interpreter with sandbox_env sees only sandbox paths."""
    import subprocess
    code = ("import sys; sys.path.insert(0, %r); from kravein import config; "
            "print(config.HOME, config.STATE_DIR, config.CONFIG_DIR)" % str(et.ROOT))
    out = subprocess.run([sys.executable, "-I", "-c", code], env=et.sandbox_env(tmp_path),
                         capture_output=True, text=True, timeout=60).stdout.split()
    assert out and all(p.startswith(str(tmp_path)) for p in out)


def test_table_reports_not_run_outside_the_denominator():
    rows = [{"id": "a", "status": "pass", "rounds": 2, "tool_calls": 3, "cards": [], "wall_s": 1.0, "checks": []},
            {"id": "b", "status": "not_run", "why": "HTTP 503"}]
    text = et.table(rows)
    assert "PASS 1/1" in text and "NOT RUN 1" in text


def test_provider_down_is_not_run_even_after_tool_calls():
    quota = {"outcome": "failed", "summary": "No LLM provider could answer. gemini: 429 RESOURCE_EXHAUSTED."}
    assert et.provider_down(quota)
    assert et.provider_down({"outcome": "failed", "summary": "HTTP 503 after retries"})
    # A real agent failure (round limit) is a failure of the agent, not of the provider.
    assert not et.provider_down({"outcome": "failed", "summary": "Stopped after 6 tool rounds without a final answer."})
    assert not et.provider_down({"outcome": "done", "summary": "ok, no provider needed"})
