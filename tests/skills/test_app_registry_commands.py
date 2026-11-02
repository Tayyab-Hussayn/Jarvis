"""Tests for #200: preferred apps, resolve_command, verified launches, learned registry."""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import audit, config, safety  # noqa: E402
from kravein.platform import appreg, apps, commands  # noqa: E402
from conftest import _FakeBackend, _FakeWindow, fake_desktop  # noqa: E402,F401


def _desktop(d: Path, fname: str, name: str, exe: str, cats: str = "") -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / fname).write_text(
        f"[Desktop Entry]\nType=Application\nName={name}\nExec={exe} %U\n"
        + (f"Categories={cats}\n" if cats else "")
    )


def _exe(d: Path, name: str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("#!/bin/sh\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A fake home with ~/.local/bin, a PATH without it, and desktop entries."""
    home = tmp_path / "home"
    apps_dir = tmp_path / "usr" / "applications"
    _desktop(apps_dir, "foot.desktop", "Foot", "foot", "TerminalEmulator;")
    _desktop(apps_dir, "kitty.desktop", "kitty", "kitty", "TerminalEmulator;")
    _desktop(apps_dir, "alacritty.desktop", "Alacritty", "alacritty", "TerminalEmulator;")
    _desktop(apps_dir, "kitty-open.desktop", "kitty URL Launcher", "kitty", "TerminalEmulator;\nNoDisplay=true")
    _desktop(apps_dir, "firefox.desktop", "Firefox", "firefox")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "usr"))
    monkeypatch.setattr(Path, "home", lambda: home)
    sysbin = tmp_path / "sysbin"
    _exe(sysbin, "ls")
    monkeypatch.setenv("PATH", str(sysbin))
    from kravein.platform import shell
    shell.clear_which_cache()
    apps.index.cache_clear()
    monkeypatch.setattr(apps, "_default_handler", lambda *a: "")
    yield home
    apps.index.cache_clear()
    shell.clear_which_cache()


# --- preferred apps ---------------------------------------------------------

def test_terminal_means_kitty_when_installed(machine, monkeypatch):
    monkeypatch.setattr(config, "get", lambda k, d=None: d)
    monkeypatch.setattr(apps.shell, "have", lambda b: True)
    entry, problem = apps.resolve("terminal")
    assert problem == "" and entry.name == "kitty"


def test_preferred_terminal_setting_wins(machine, monkeypatch):
    monkeypatch.setattr(config, "get",
                        lambda k, d=None: "alacritty" if k == "apps.preferred.terminal" else d)
    entry, _ = apps.resolve("terminal")
    assert entry.name == "Alacritty"
    assert apps.preferred_terminal_binary() == "alacritty"


def test_unresolvable_preference_falls_back_to_detection(machine, monkeypatch):
    monkeypatch.setattr(config, "get",
                        lambda k, d=None: "no-such-term" if k == "apps.preferred.terminal" else d)
    entry, _ = apps.resolve("terminal")
    assert entry.name == "kitty"


def test_browser_preference_uses_xdg_default(machine, monkeypatch):
    monkeypatch.setattr(config, "get", lambda k, d=None: d)
    monkeypatch.setattr(apps, "_default_handler", lambda *a: "firefox.desktop")
    entry, _ = apps.resolve("browser")
    assert entry.name == "Firefox"


def test_open_terminal_skill_uses_preferred(machine, monkeypatch):
    from kravein.skills import terminal as term

    monkeypatch.setattr(config, "get", lambda k, d=None: d)
    monkeypatch.setattr(term.shell if hasattr(term, "shell") else apps.shell,
                        "have", lambda b: True, raising=False)
    monkeypatch.setattr("kravein.platform.shell.have", lambda b: True)
    assert term._pick_terminal() == "kitty"


# --- resolve_command --------------------------------------------------------

def test_resolves_exact_executable_in_local_bin(machine):
    exe = _exe(machine / ".local" / "bin", "agy")
    res = commands.resolve_command("agy")
    assert res.ok and res.path == str(exe)


def test_resolution_ignores_case_space_and_hyphen(machine):
    exe = _exe(machine / ".local" / "bin", "antigravity")
    assert commands.resolve_command("Anti Gravity").path == str(exe)
    assert commands.resolve_command("anti-gravity").path == str(exe)


def test_app_filler_is_dropped_but_cli_filler_only_yields_candidates(machine):
    exe = _exe(machine / ".local" / "bin", "antigravity")
    assert commands.resolve_command("antigravity app").path == str(exe)
    # "antigravity cli" may be a different program than the GUI "antigravity"
    _exe(machine / ".local" / "bin", "agy")
    res = commands.resolve_command("antigravity cli")
    assert not res.ok
    assert any("agy" in c for c in res.candidates)
    assert any("same name without 'cli'" in c for c in res.candidates)


def test_desktop_entry_name_resolves_to_its_exec(machine, tmp_path):
    exe = _exe(machine / ".local" / "bin", "agy")
    _desktop(tmp_path / "usr" / "applications", "ag.desktop", "Antigravity", str(exe))
    apps.index.cache_clear()
    assert commands.resolve_command("antigravity").path == str(exe)


def test_two_equal_names_are_candidates_never_a_pick(machine):
    a = _exe(machine / ".local" / "bin", "anti-gravity")
    b = _exe(machine / ".local" / "bin", "antigravity")
    res = commands.resolve_command("anti gravity")
    assert not res.ok and sorted(res.candidates) == sorted([str(a), str(b)])


def test_near_miss_lists_candidates_and_resolves_nothing(machine):
    _exe(machine / ".local" / "bin", "antigravity-cli-helper")
    res = commands.resolve_command("antigravity")
    assert not res.ok
    assert any("antigravity-cli-helper" in c for c in res.candidates)
    assert "I will not guess" in res.explain("antigravity")


def test_opt_bin_is_searched(machine, tmp_path, monkeypatch):
    opt = tmp_path / "opt"
    exe = _exe(opt / "tool" / "bin", "toolx")
    real = Path

    class P(type(Path())):
        pass

    monkeypatch.setattr(commands, "search_dirs", lambda: [str(opt / "tool" / "bin")])
    assert commands.resolve_command("toolx").path == str(exe)


# --- command lines ----------------------------------------------------------

def test_unknown_program_is_refused_with_candidates(machine):
    _exe(machine / ".local" / "bin", "agy")
    cmd, problem = commands.check_command_line("agyy --help")
    assert "Nothing was run" in problem and "agy" in problem


def test_guessed_name_with_args_is_refused(machine):
    _exe(machine / ".local" / "bin", "agy")
    _, problem = commands.check_command_line("antigravity cli")
    assert "antigravity" in problem and "Nothing was run" in problem


def test_builtins_pipelines_and_known_programs_pass(machine):
    for cmd in ("cd /tmp && ls", "ls | ls", "FOO=1 ls -l", "sudo ls", "export A=1; ls",
                "ls 'a && b'"):
        assert commands.check_command_line(cmd) == (cmd, ""), cmd


def test_every_segment_is_checked(machine):
    _, problem = commands.check_command_line("ls && nosuchprog")
    assert "nosuchprog" in problem


def test_off_path_exact_executable_is_rewritten_to_absolute_path(machine):
    exe = _exe(machine / ".local" / "bin", "agy")
    cmd, problem = commands.check_command_line("agy --version")
    assert problem == "" and cmd.startswith(str(exe)) and cmd.endswith("--version")


def test_last_program(machine):
    assert commands.last_program("cd ~/x && agy --flag") == "agy"
    assert commands.last_program("cd /tmp") == ""
    assert commands.last_program("'/home/u/.local/bin/agy' a") == "agy"


def test_run_command_fails_fast_on_unknown_program(machine, monkeypatch):
    from kravein.skills import shell as sh

    ran = []
    monkeypatch.setattr(sh.shell_platform, "run", lambda *a, **k: ran.append(a))
    out = sh.run_command("antigravity cli")
    assert ran == [] and "Nothing was run" in out


# --- process evidence -------------------------------------------------------

def test_wait_for_process_sees_only_new_matching_process(monkeypatch):
    monkeypatch.setattr(commands, "descendants", lambda pid: {10, 11, 12})
    monkeypatch.setattr(commands, "_proc_names",
                        lambda pid: {"agy"} if pid == 12 else {"bash"})
    assert "pid 12" in commands.wait_for_process(1, "/home/u/.local/bin/agy", {10, 11}, 0.2)
    # the same process was already there before typing: not evidence
    assert commands.wait_for_process(1, "agy", {10, 11, 12}, 0.2) == ""


def test_interpreted_script_matches_by_argv(monkeypatch, tmp_path):
    monkeypatch.setattr(commands, "_proc_names", lambda pid: {"node", "agy"})
    assert commands._matches(5, {"agy"})


def test_type_into_terminal_reports_unverified_when_no_process(fake_desktop, monkeypatch, machine):
    from kravein.skills import terminal as term

    _exe(machine / ".local" / "bin", "agy")
    monkeypatch.setattr(term, "own_pids", lambda: set())
    monkeypatch.setattr(term.inputs, "backend_name", lambda: "ydotool")
    monkeypatch.setattr(term, "_focus_and_verify", lambda w: True)
    typed = []
    monkeypatch.setattr(term, "_type_command", lambda c, e: typed.append(c))
    monkeypatch.setattr(commands, "process_snapshot", lambda pid: set())
    monkeypatch.setattr(commands, "wait_for_process", lambda *a, **k: "")
    fake_desktop(_FakeBackend([_FakeWindow("t", "kitty", 2, pid=9)]))
    with safety.confirmer_scope(lambda *_: True):
        out = term.type_into_terminal(command="agy", query="kitty")
    assert "NOT confirmed" in out
    assert typed and typed[0].endswith("agy")


def test_type_into_terminal_reports_evidence(fake_desktop, monkeypatch, machine):
    from kravein.skills import terminal as term

    _exe(machine / ".local" / "bin", "agy")
    monkeypatch.setattr(term, "own_pids", lambda: set())
    monkeypatch.setattr(term.inputs, "backend_name", lambda: "ydotool")
    monkeypatch.setattr(term, "_focus_and_verify", lambda w: True)
    monkeypatch.setattr(term, "_type_command", lambda c, e: None)
    monkeypatch.setattr(commands, "process_snapshot", lambda pid: set())
    monkeypatch.setattr(commands, "wait_for_process", lambda *a, **k: "pid 77 (agy)")
    fake_desktop(_FakeBackend([_FakeWindow("t", "kitty", 2, pid=9)]))
    with safety.confirmer_scope(lambda *_: True):
        out = term.type_into_terminal(command="agy", query="kitty")
    assert "Verified: pid 77 (agy)" in out


def test_type_into_terminal_never_types_an_unknown_program(fake_desktop, monkeypatch, machine):
    from kravein.skills import terminal as term

    monkeypatch.setattr(term, "own_pids", lambda: set())
    monkeypatch.setattr(term.inputs, "backend_name", lambda: "ydotool")
    typed = []
    monkeypatch.setattr(term, "_type_command", lambda c, e: typed.append(c))
    fake_desktop(_FakeBackend([_FakeWindow("t", "kitty", 2, pid=9)]))
    out = term.type_into_terminal(command="antigravity cli", query="kitty")
    assert typed == [] and "Nothing was run" in out


# --- learned registry -------------------------------------------------------

def test_registry_roundtrip_and_normalised_hit(machine):
    appreg.learn(["Anti Gravity", "agy"], kind="cli", ident="/bin/sh", display="agy")
    assert appreg.lookup("antigravity")["id"] == "/bin/sh"
    assert appreg.lookup("AGY")["name"] == "agy"
    assert appreg.lookup("unknown") is None
    assert json.loads(appreg.path().read_text())["apps"]["antigravity"]["uses"] == 1


def test_stale_entry_is_dropped(machine, tmp_path):
    gone = _exe(tmp_path / "x", "old")
    appreg.learn(["old", "older"], kind="cli", ident=str(gone), display="old")
    gone.unlink()
    assert appreg.lookup("old") is None
    assert appreg.lookup("older") is None      # every name of that app went


def test_forget_removes_all_names(machine):
    appreg.learn(["a b", "ab2"], kind="cli", ident="/bin/sh", display="sh")
    assert "Forgot" in appreg.forget("ab2")
    assert appreg.lookup("a b") is None
    assert "not in" in appreg.forget("a b")


def test_registry_is_size_capped_keeping_recent(machine, monkeypatch):
    monkeypatch.setattr(appreg, "MAX_KEYS", 5)
    for i in range(8):
        appreg.learn([f"app{i}"], kind="cli", ident="/bin/sh", display=f"app{i}")
    keys = set(json.loads(appreg.path().read_text())["apps"])
    assert len(keys) == 5 and "app7" in keys


def test_corrupt_registry_is_empty_not_fatal(machine):
    appreg.path().parent.mkdir(parents=True, exist_ok=True)
    appreg.path().write_text("{not json")
    assert appreg.lookup("x") is None
    appreg.learn(["x"], kind="cli", ident="/bin/sh", display="x")
    assert appreg.lookup("x") is not None


def test_concurrent_learns_lose_nothing(machine):
    def work(i):
        appreg.learn([f"name{i}"], kind="cli", ident="/bin/sh", display=f"n{i}")

    threads = [threading.Thread(target=work, args=(i,)) for i in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    keys = json.loads(appreg.path().read_text())["apps"]
    assert len(keys) == 12


def test_forget_app_skill(machine):
    from kravein.skills import apps as sk

    appreg.learn(["zed"], kind="cli", ident="/bin/sh", display="zed")
    assert "Forgot" in sk.forget_app("Zed")


# --- launch: verify, learn, then hit -----------------------------------------

class _Desk(_FakeBackend):
    """Opens a window when `spawn` is called (the app 'works')."""

    def __init__(self, opens=True):
        super().__init__([])
        self.opens = opens

    def focused(self):
        return None

    def focus(self, w):
        return True


def _install_launch(monkeypatch, backend):
    monkeypatch.setattr(apps.desktop, "backend", lambda: backend)

    def fake_launch(entry, *, focus_existing=True):
        if backend.opens:
            backend.windows.append(_FakeWindow(f"w{len(backend.windows)}", "firefox", 1,
                                               title=entry.name))
        return f"Launched {entry.name}."

    monkeypatch.setattr(apps, "launch", fake_launch)
    monkeypatch.setattr(apps, "is_running", lambda e: False)
    monkeypatch.setattr(commands, "process_running", lambda n: "")


def test_verified_launch_is_learned_then_second_open_is_a_registry_hit(machine, monkeypatch):
    backend = _Desk()
    _install_launch(monkeypatch, backend)
    events = []
    monkeypatch.setattr(audit, "record", lambda ev, **kw: events.append(ev))

    first = apps.launch_by_name("Fire Fox")
    assert "verified: new window" in first
    assert "apps.registry_learn" in events and "apps.registry_hit" not in events

    events.clear()
    second = apps.launch_by_name("fire fox")
    assert "apps.registry_hit" in events
    assert second.endswith("(learned name)")


def test_unverified_launch_is_not_learned(machine, monkeypatch):
    backend = _Desk(opens=False)
    _install_launch(monkeypatch, backend)
    out = apps.launch_by_name("firefox")
    assert "cannot say it opened" in out
    assert appreg.lookup("firefox") is None


def test_stale_registry_entry_searches_again(machine, monkeypatch):
    backend = _Desk()
    _install_launch(monkeypatch, backend)
    appreg.learn(["ffx"], kind="desktop", ident="gone.desktop", display="Gone")
    out = apps.launch_by_name("ffx")
    assert "Could not find" in out
    assert appreg.lookup("ffx") is None


def test_kind_words_are_never_learned(machine, monkeypatch):
    backend = _Desk()
    _install_launch(monkeypatch, backend)
    monkeypatch.setattr(config, "get", lambda k, d=None: d)
    apps.launch_by_name("terminal")
    assert appreg.lookup("terminal") is None


def test_unknown_app_that_is_an_off_path_executable_launches_by_exact_name(machine, monkeypatch):
    exe = _exe(machine / ".local" / "bin", "agy")
    monkeypatch.setattr(apps.shell, "spawn", lambda args, **k: 1)
    monkeypatch.setattr(commands, "process_running", lambda n: "pid 5")
    out = apps.launch_by_name("agy")
    assert "verified: pid 5" in out
    assert appreg.lookup("agy")["id"] == str(exe)
