"""Tests: exact window ids and terminal cwd in list/focus/close (#82).

Owner bug: Focus on one of three `foot` terminals answered "matches 3 windows".
Every window now has an exact id the caller can pass back.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import safety  # noqa: E402
from kravein.platform import desktop  # noqa: E402
from conftest import _FakeBackend, _FakeWindow, fake_desktop  # noqa: E402,F401


def _three_foots():
    return _FakeBackend([
        _FakeWindow("0xa", "foot", 2, pid=100, title="server"),
        _FakeWindow("0xb", "foot", 3, pid=200, title="claude"),
        _FakeWindow("0xc", "foot", 4, pid=300, title="spare"),
    ])


def _with_focus(backend):
    backend.focused_ids = []
    backend.focus = lambda w: (backend.focused_ids.append(w.id), True)[1]
    return backend


def test_focus_by_id_hits_exactly_that_window(fake_desktop):
    from kravein.skills import windows as win

    backend = _with_focus(fake_desktop(_three_foots()))
    out = win.focus_window(id="0xb")
    assert backend.focused_ids == ["0xb"]
    assert "workspace 3" in out


def test_focus_without_id_still_refuses_ambiguity(fake_desktop):
    from kravein.skills import windows as win

    backend = _with_focus(fake_desktop(_three_foots()))
    out = win.focus_window(query="foot")
    assert "matches 3 windows" in out
    assert backend.focused_ids == []


def test_stale_id_is_refused_never_name_matched(fake_desktop):
    from kravein.skills import windows as win

    backend = _with_focus(fake_desktop(_three_foots()))
    # query would match; the id must still win and fail.
    out = win.focus_window(query="foot", id="0xdead")
    assert "No window with id 0xdead" in out
    assert backend.focused_ids == []


def test_close_by_id_closes_exactly_that_window(fake_desktop):
    from kravein.skills import windows as win

    backend = fake_desktop(_three_foots())
    seen = []
    with safety.confirmer_scope(lambda a, d, g: (seen.append((a, d)), True)[1]):
        out = win.close_window(id="0xc")
    assert backend.closed == ["0xc"]
    assert out.startswith("Closed")
    assert "workspace 4" in seen[0][0] + seen[0][1]


def test_close_stale_id_refused(fake_desktop):
    from kravein.skills import windows as win

    backend = fake_desktop(_three_foots())
    with safety.confirmer_scope(lambda *_: True):
        out = win.close_window(query="foot", id="0xdead")
    assert "No window with id" in out
    assert backend.closed == []


def test_close_confirmation_names_the_cwd(fake_desktop, monkeypatch):
    from kravein.skills import windows as win

    fake_desktop(_three_foots())
    monkeypatch.setattr(win.desktop, "terminal_cwd",
                        lambda w: "/home/k/proj" if w.id == "0xb" else None)
    seen = []
    with safety.confirmer_scope(lambda a, d, g: (seen.append((a, d)), False)[1]):
        try:
            win.close_window(id="0xb")
        except safety.ConfirmationRequired:
            pass
    assert "cwd /home/k/proj" in seen[0][1]


def test_list_windows_json_has_id_and_cwd(fake_desktop, monkeypatch):
    from kravein.skills import windows as win

    fake_desktop(_FakeBackend([
        desktop.Window(id=i, title="t", app="foot", pid=n, workspace=str(n),
                       focused=False)
        for i, n in (("0xa", 2), ("0xb", 3), ("0xc", 4))
    ]))
    monkeypatch.setattr(win.desktop, "terminal_cwd",
                        lambda w: "/tmp/x" if w.id == "0xa" else None)
    rows = json.loads(win.list_windows(as_json=True))
    assert [r["id"] for r in rows] == ["0xa", "0xb", "0xc"]
    assert rows[0]["cwd"] == "/tmp/x" and rows[1]["cwd"] is None
    text = win.list_windows()
    assert "id 0xa" in text and "cwd /tmp/x" in text


def _term(pid=100, app="foot"):
    return desktop.Window(id="0x1", title="t", app=app, pid=pid,
                          workspace="1", focused=False)


def test_cwd_follows_single_child_chain(monkeypatch):
    tree = {100: [101], 101: [102], 102: []}
    monkeypatch.setattr(desktop, "_children", lambda p: tree.get(p, []))
    monkeypatch.setattr(desktop.os, "readlink",
                        lambda path: {"/proc/102/cwd": "/work/repo"}[path])
    assert desktop.terminal_cwd(_term()) == "/work/repo"


def test_cwd_stops_at_a_shell_with_background_jobs(monkeypatch):
    tree = {100: [101], 101: [102, 103]}
    monkeypatch.setattr(desktop, "_children", lambda p: tree.get(p, []))
    monkeypatch.setattr(desktop.os, "readlink",
                        lambda path: {"/proc/101/cwd": "/shell/dir"}[path])
    assert desktop.terminal_cwd(_term()) == "/shell/dir"


def test_cwd_unknown_for_shared_terminal_server_and_non_terminals(monkeypatch):
    # One foot server pid hosting several shells: no way to tell which is ours.
    monkeypatch.setattr(desktop, "_children", lambda p: [1, 2, 3])
    assert desktop.terminal_cwd(_term()) is None
    monkeypatch.setattr(desktop, "_children", lambda p: [5])
    assert desktop.terminal_cwd(_term(app="firefox")) is None
