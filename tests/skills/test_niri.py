"""Tests: niri backend (#123).

State comes from JSON recorded from niri 26.04 (``fixtures/niri``); a small
fake answers ``niri msg`` the way niri does and applies actions, so the
tests can check that the backend re-reads the effect instead of trusting the
exit code. No live compositor is touched.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein.platform import apps, desktop, detect, screen, shell  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "niri"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


class FakeNiri:
    """Answers ``niri msg`` calls; ``ignore`` makes actions accepted but inert."""

    def __init__(self, extra_windows=True):
        self.windows = _load("windows.json")
        self.workspaces = _load("workspaces.json")
        if extra_windows:
            second = copy.deepcopy(self.windows[0])
            second.update(id=7, title="other", app_id="foot", pid=99,
                          workspace_id=2, is_focused=False)
            self.windows.append(second)
        self.calls: list[list[str]] = []
        self.ignore = False
        self.next_spawn: dict | None = None

    def _focus(self, wid):
        for w in self.windows:
            w["is_focused"] = w["id"] == wid
            if w["is_focused"]:
                for ws in self.workspaces:
                    ws["is_focused"] = ws["id"] == w["workspace_id"]
                    ws["is_active"] = ws["is_focused"] or ws["is_active"] and ws["id"] != w["workspace_id"]

    def run(self, args, **kwargs):
        args = list(args)
        self.calls.append(args)
        ok = lambda out="": shell.Result(args, 0, out, "")  # noqa: E731
        if args[:3] == ["niri", "msg", "--json"]:
            what = args[3]
            return ok(json.dumps(self.windows if what == "windows" else self.workspaces))
        assert args[:3] == ["niri", "msg", "action"], args
        act, rest = args[3], args[4:]
        if self.ignore:
            return ok()
        opt = lambda flag: rest[rest.index(flag) + 1] if flag in rest else None  # noqa: E731
        if act == "focus-window":
            wid = int(opt("--id"))
            if not any(w["id"] == wid for w in self.windows):
                return shell.Result(args, 1, "", "no such window")
            self._focus(wid)
        elif act == "close-window":
            wid = int(opt("--id"))
            self.windows = [w for w in self.windows if w["id"] != wid]
        elif act == "focus-workspace":
            ref = rest[0]
            hit = [ws for ws in self.workspaces if str(ws["idx"]) == ref or ws["name"] == ref]
            if hit:  # an unknown index is clamped by niri: no change
                for ws in self.workspaces:
                    ws["is_focused"] = ws is hit[0]
        elif act == "move-window-to-workspace":
            wid = int(opt("--window-id"))
            ref = rest[-1]
            hit = [ws for ws in self.workspaces if str(ws["idx"]) == ref or ws["name"] == ref]
            if hit:
                for w in self.windows:
                    if w["id"] == wid:
                        w["workspace_id"] = hit[0]["id"]
        elif act == "toggle-window-floating":
            wid = int(opt("--id"))
            for w in self.windows:
                if w["id"] == wid:
                    w["is_floating"] = not w["is_floating"]
        elif act == "spawn" and self.next_spawn:
            new = copy.deepcopy(self.windows[0])
            new.update(self.next_spawn)
            for w in self.windows:
                w["is_focused"] = False  # a new window takes focus
            self.windows.append(new)
        return ok()


@pytest.fixture
def niri(monkeypatch):
    fake = FakeNiri()
    monkeypatch.setattr(shell, "run", fake.run)
    monkeypatch.setattr(shell, "spawn", lambda *a, **k: 0)
    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    monkeypatch.setattr(detect, "find_niri_socket", lambda: "/run/user/1/niri.x.1.sock")
    return desktop.NiriBackend(), fake


# -- listing --------------------------------------------------------------

def test_lists_recorded_windows_with_exact_ids(niri):
    backend, _ = niri
    wins = backend.list_windows()
    assert [(w.id, w.app, w.workspace, w.focused) for w in wins] == [
        ("3", "kitty", "1", True), ("7", "foot", "2", False)]
    assert wins[0].pid == 3423 and wins[0].title == "~/P/desktop"
    assert backend.focused().id == "3"


def test_workspaces_are_labelled_by_idx_or_name(niri):
    backend, fake = niri
    assert backend.list_workspaces() == ["1", "2"]
    fake.workspaces[1]["name"] = "web"
    assert backend.list_workspaces() == ["1", "web"]
    assert backend.list_windows()[1].workspace == "web"


def test_second_output_workspaces_cannot_collide_with_bare_numbers(niri):
    backend, fake = niri
    fake.workspaces.append({**fake.workspaces[0], "id": 9, "output": "HDMI-A-1",
                            "is_focused": False, "is_active": False})
    assert backend.list_workspaces() == ["1", "2", "1@HDMI-A-1"]
    assert backend.workspace("1@HDMI-A-1") is False


def test_window_by_id_refuses_stale_id_without_name_fallback(niri, monkeypatch):
    backend, _ = niri
    monkeypatch.setattr(desktop, "_backend", backend)
    assert desktop.window_by_id("7").app == "foot"
    assert desktop.window_by_id("8") is None
    assert desktop.window_by_id("kitty") is None


# -- actions: verify the effect, not the ack -------------------------------

def test_focus_confirms_focus_landed(niri):
    backend, fake = niri
    assert backend.focus(backend.list_windows()[1]) is True
    assert backend.focused().id == "7"
    fake.ignore = True
    assert backend.focus(backend.list_windows()[0]) is False  # accepted, nothing moved


def test_focus_stale_id_never_issues_an_action(niri):
    backend, fake = niri
    ghost = desktop.Window("42", "", "", None, "1", False)
    assert backend.focus(ghost) is False
    assert backend.close(ghost) is False
    assert not any(c[3:4] in (["focus-window"], ["close-window"]) for c in fake.calls)


def test_close_targets_by_id_not_by_focus(niri):
    backend, fake = niri
    assert backend.close(backend.list_windows()[1]) is True
    assert ["niri", "msg", "action", "close-window", "--id", "7"] in fake.calls
    assert [w.id for w in backend.list_windows()] == ["3"]  # the focused one survived


def test_switch_workspace_is_verified(niri):
    backend, fake = niri
    assert backend.workspace("2") is True
    assert backend.workspace("9") is False  # niri clamps; focus did not land on "9"


def test_move_to_workspace_rereads_window_workspace(niri):
    backend, fake = niri
    win = backend.list_windows()[0]
    assert backend.move_to_workspace("2", window=win) is True
    assert backend.list_windows()[0].workspace == "2"
    assert backend.move_to_workspace("9", window=backend.list_windows()[0]) is False


def test_move_unfocused_window_keeps_focus_where_it_was(niri):
    backend, fake = niri
    other = backend.list_windows()[1]
    assert backend.move_to_workspace("1", window=other) is True
    assert ["--focus", "false"] == next(c for c in fake.calls if "move-window-to-workspace" in c)[6:8]


def test_toggle_float_confirms_flip(niri):
    backend, fake = niri
    assert backend.toggle_float() is True
    assert fake.windows[0]["is_floating"] is True
    fake.ignore = True
    assert backend.toggle_float() is False


def test_no_window_means_no_action(niri):
    backend, fake = niri
    fake.windows.clear()
    assert backend.fullscreen() is False and backend.toggle_float() is False
    assert backend.move_to_workspace("2") is False


# -- open_app on a workspace ----------------------------------------------

def test_spawn_on_workspace_moves_new_window_and_restores_focus(niri):
    backend, fake = niri
    fake.next_spawn = {"id": 11, "title": "NEW", "app_id": "kitty", "pid": 500,
                       "workspace_id": 1, "is_focused": True}
    assert backend.spawn_on_workspace(["kitty", "--title", "NEW"], "2") is True
    new = next(w for w in backend.list_windows() if w.id == "11")
    assert new.workspace == "2"                  # landed where asked
    assert backend.focused().id == "3"           # user is back on their window
    assert backend._focused_workspace() == "1"


def test_spawn_on_workspace_refused_spawn_reports_false(niri, monkeypatch):
    backend, fake = niri
    real = fake.run
    monkeypatch.setattr(shell, "run", lambda a, **k: shell.Result(list(a), 1, "", "no") if "spawn" in a else real(a, **k))
    assert backend.spawn_on_workspace(["kitty"], "2") is False


def test_launch_on_workspace_reports_landing_through_apps(niri, monkeypatch, tmp_path):
    backend, fake = niri
    monkeypatch.setattr(desktop, "_backend", backend)
    fake.next_spawn = {"id": 12, "app_id": "kitty", "title": "k", "pid": 501,
                       "workspace_id": 1, "is_focused": True}
    entry = apps.DesktopEntry(name="Kitty", exec_cmd="kitty", path=tmp_path / "kitty.desktop")
    monkeypatch.setattr(apps, "find", lambda q: [entry])
    assert "on workspace 2" in apps.launch_on_workspace("kitty", "2")


# -- detection ------------------------------------------------------------

def test_socket_discovery_without_env(monkeypatch, tmp_path):
    import os
    monkeypatch.delenv("NIRI_SOCKET", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    (tmp_path / "niri.wayland-1.99999999.sock").touch()    # dead pid: stale
    live = tmp_path / f"niri.wayland-1.{os.getpid()}.sock"
    live.touch()
    assert detect.find_niri_socket() == str(live)


def test_socket_discovery_none_running(monkeypatch, tmp_path):
    monkeypatch.delenv("NIRI_SOCKET", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert detect.find_niri_socket() == ""


def test_adopt_exports_session_for_ssh(monkeypatch, tmp_path):
    import os
    # setenv first so monkeypatch restores what _adopt_niri_session writes
    monkeypatch.setenv("NIRI_SOCKET", "")
    monkeypatch.setenv("WAYLAND_DISPLAY", "")
    monkeypatch.delenv("NIRI_SOCKET")
    monkeypatch.delenv("WAYLAND_DISPLAY")
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    monkeypatch.delenv("SWAYSOCK", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    sock = tmp_path / f"niri.wayland-1.{os.getpid()}.sock"
    sock.touch()
    detect._adopt_niri_session()
    assert os.environ["NIRI_SOCKET"] == str(sock)
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-1"
    assert detect._detect_session() == "wayland"
    assert detect._detect_compositor("none") == "niri"


# -- screenshots -----------------------------------------------------------

def test_screenshot_uses_niri_when_grim_missing(monkeypatch, tmp_path):
    out = tmp_path / "s.png"
    seen = []

    def run(args, **kw):
        seen.append(list(args))
        out.write_bytes(b"png")
        return shell.Result(list(args), 0, "", "")

    monkeypatch.setattr(shell, "run", run)
    monkeypatch.setattr(detect, "find_niri_socket", lambda: "/s")
    monkeypatch.setattr(screen, "backend_name", lambda: "niri")
    assert screen.capture(out) == out
    assert [c for c in seen if c[0] == "niri"][0][:5] == ["niri", "msg", "action", "screenshot-screen", "--path"]


def test_screenshot_region_names_missing_package(monkeypatch, tmp_path):
    monkeypatch.setattr(screen, "backend_name", lambda: "niri")
    with pytest.raises(screen.CaptureError, match="grim and slurp"):
        screen.capture(tmp_path / "x.png", region=True)
