"""Tests: open_app into a chosen workspace (#93)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein.platform import apps, desktop, shell  # noqa: E402
from kravein.skills import apps as skill_apps  # noqa: E402


def _entry(name, exe, tmp_path, **kw):
    return apps.DesktopEntry(
        name=name, exec_cmd=f"{exe} %U", path=tmp_path / f"{exe}.desktop", **kw
    )


class _Backend:
    name = "fake"
    can_spawn_on_workspace = True

    def __init__(self, new_window_ws="3", workspaces=("1", "3")):
        self.spawned: list[tuple[list[str], str]] = []
        self.focused: list[str] = []
        self.windows: list = []
        self.new_ws = new_window_ws
        self.workspaces = list(workspaces)

    def list_windows(self):
        return list(self.windows)

    def list_workspaces(self):
        return self.workspaces

    def focus(self, window):
        self.focused.append(window.id)
        return True

    def spawn_on_workspace(self, command, workspace):
        self.spawned.append((command, workspace))
        if self.new_ws is not None:
            self.windows.append(desktop.Window(
                id="0xnew", title="t", app=command[0], pid=1,
                workspace=self.new_ws, focused=False))
        return True


def _setup(monkeypatch, tmp_path, backend, entries):
    monkeypatch.setattr(desktop, "backend", lambda: backend)
    monkeypatch.setattr(apps, "index", lambda: entries)
    monkeypatch.setattr(apps.time, "sleep", lambda s: None)


def test_workspace_launch_dispatches_to_workspace_and_never_focuses(monkeypatch, tmp_path):
    backend = _Backend()
    _setup(monkeypatch, tmp_path, backend, [_entry("Foot", "foot", tmp_path)])
    out = skill_apps.open_app("foot", workspace="3")
    assert backend.spawned == [(["foot"], "3")]
    assert backend.focused == []
    assert "workspace 3" in out and "Started" in out


def test_window_landing_elsewhere_is_reported_not_claimed(monkeypatch, tmp_path):
    backend = _Backend(new_window_ws="1")
    _setup(monkeypatch, tmp_path, backend, [_entry("Foot", "foot", tmp_path)])
    out = skill_apps.open_app("foot", workspace="3")
    assert "workspace 1, not 3" in out


def test_no_window_appearing_is_not_confirmed(monkeypatch, tmp_path):
    backend = _Backend(new_window_ws=None)
    _setup(monkeypatch, tmp_path, backend, [_entry("Foot", "foot", tmp_path)])
    out = skill_apps.open_app("foot", workspace="3")
    assert "cannot confirm" in out


def test_invalid_workspace_refused_without_spawning(monkeypatch, tmp_path):
    backend = _Backend()
    _setup(monkeypatch, tmp_path, backend, [_entry("Foot", "foot", tmp_path)])
    for bad in ("0", "-2", 'x"); evil()', "nope"):
        out = skill_apps.open_app("foot", workspace=bad)
        assert "not a workspace" in out
    assert backend.spawned == []


def test_existing_named_workspace_accepted(monkeypatch, tmp_path):
    backend = _Backend(new_window_ws="web", workspaces=("1", "web"))
    _setup(monkeypatch, tmp_path, backend, [_entry("Foot", "foot", tmp_path)])
    skill_apps.open_app("foot", workspace="web")
    assert backend.spawned == [(["foot"], "web")]


def test_unknown_app_refused(monkeypatch, tmp_path):
    backend = _Backend()
    _setup(monkeypatch, tmp_path, backend, [_entry("Foot", "foot", tmp_path)])
    out = skill_apps.open_app("zzzz", workspace="3")
    assert "Could not find" in out and backend.spawned == []


def test_ambiguous_app_lists_candidates(monkeypatch, tmp_path):
    backend = _Backend()
    _setup(monkeypatch, tmp_path, backend, [
        _entry("Firefox", "firefox", tmp_path),
        _entry("Firefox Developer Edition", "firefox-dev", tmp_path),
    ])
    out = skill_apps.open_app("fire", workspace="3")
    assert "several" in out and "Firefox Developer Edition" in out
    assert backend.spawned == []


def test_exact_name_wins_over_longer_matches(monkeypatch, tmp_path):
    backend = _Backend()
    _setup(monkeypatch, tmp_path, backend, [
        _entry("Firefox", "firefox", tmp_path),
        _entry("Firefox Developer Edition", "firefox-dev", tmp_path),
    ])
    skill_apps.open_app("firefox", workspace="3")
    assert backend.spawned == [(["firefox"], "3")]


def test_unsupported_desktop_refuses_clearly(monkeypatch, tmp_path):
    backend = _Backend()
    backend.can_spawn_on_workspace = False
    _setup(monkeypatch, tmp_path, backend, [_entry("Foot", "foot", tmp_path)])
    out = skill_apps.open_app("foot", workspace="3")
    assert "not supported on this desktop" in out and backend.spawned == []


def test_without_workspace_behaviour_unchanged(monkeypatch, tmp_path):
    called = {}
    monkeypatch.setattr(apps, "launch_by_name", lambda q: called.setdefault("q", q) and "ok")
    assert skill_apps.open_app("foot") == "ok"
    assert called["q"] == "foot"


def test_hyprland_dispatch_lua_and_legacy(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, **kw):
        calls.append(list(args))
        return shell.Result(list(args), 0, "ok", "")

    monkeypatch.setattr(shell, "run", fake_run)
    lua = desktop.HyprlandBackend()
    lua._lua = True
    assert lua.spawn_on_workspace(["foot", "-e", "my cmd"], "3")
    assert calls[-1][:2] == ["hyprctl", "eval"]
    assert '{workspace="3 silent"}' in calls[-1][2]
    assert "'my cmd'" in calls[-1][2]

    legacy = desktop.HyprlandBackend()
    legacy._lua = False
    assert legacy.spawn_on_workspace(["foot"], "3")
    assert calls[-1] == ["hyprctl", "dispatch", "exec", "[workspace 3 silent] foot"]


def test_list_entries_is_structured(monkeypatch, tmp_path):
    hidden = _entry("Hidden", "h", tmp_path, no_display=True)
    zed = _entry("Zed", "zed", tmp_path, icon="zed-icon")
    monkeypatch.setattr(apps, "index", lambda: [zed, hidden, _entry("Alpha", "alpha", tmp_path)])
    rows = apps.list_entries()
    assert [r["name"] for r in rows] == ["Alpha", "Zed"]
    assert rows[1] == {"name": "Zed", "id": "zed.desktop", "icon": "zed-icon", "exec": "zed %U"}
    assert len(apps.list_entries(limit=1)) == 1
