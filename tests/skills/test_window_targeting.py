"""Tests: window targeting.

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
from conftest import _FakeBackend, _FakeWindow, fake_desktop  # noqa: E402,F401


# Window targeting — the wrong-workspace incident
# ---------------------------------------------------------------------------
#
# Reported: asked to close the `foot` terminal on workspace 4, having walked
# back to workspace 1 by hand. Approving closed the terminal on workspace 2 —
# the one running the Kravein server.
#
# `close_window(query='foot')` resolved through find_windows, which returns
# EVERY match, and then took matches[0]. Three foot terminals were open. The
# confirmation said only "close window 'foot'", so there was nothing in it to
# catch the mistake before approving.


def test_resolve_refuses_an_ambiguous_query(fake_desktop):
    from kravein.skills import windows as win

    fake_desktop(_FakeBackend([
        _FakeWindow("a", "foot", 2, title="server"),
        _FakeWindow("b", "foot", 3, title="claude"),
        _FakeWindow("c", "foot", 4, title="spare"),
    ]))
    with pytest.raises(win.AmbiguousTarget) as caught:
        win._resolve_one("foot")

    message = caught.value.message
    assert "matches 3 windows" in message
    # Every candidate must be named well enough to tell them apart.
    for ws in ("workspace 2", "workspace 3", "workspace 4"):
        assert ws in message


def test_resolve_scopes_by_workspace(fake_desktop):
    from kravein.skills import windows as win

    fake_desktop(_FakeBackend([
        _FakeWindow("a", "foot", 2), _FakeWindow("b", "foot", 4),
    ]))
    assert win._resolve_one("foot", workspace="4").id == "b"


def test_close_window_will_not_guess_between_candidates(fake_desktop):
    """The exact call that destroyed the wrong terminal."""
    from kravein.skills import windows as win

    backend = fake_desktop(_FakeBackend([
        _FakeWindow("server", "foot", 2), _FakeWindow("target", "foot", 4),
    ]))
    asked = []
    with safety.confirmer_scope(lambda a, d, g: (asked.append(a), True)[1]):
        out = win.close_window(query="foot")

    assert backend.closed == [], "closed a window without knowing which was meant"
    assert asked == [], "asked about a window it could not identify"
    assert "matches 2 windows" in out


def test_close_window_confirmation_names_the_workspace(fake_desktop):
    """A confirmation that cannot be reviewed is theatre."""
    from kravein.skills import windows as win

    fake_desktop(_FakeBackend([
        _FakeWindow("a", "foot", 2), _FakeWindow("b", "foot", 4, pid=777, title="spare"),
    ]))
    seen = []
    with safety.confirmer_scope(lambda a, d, g: (seen.append((a, d)), False)[1]):
        with pytest.raises(safety.ConfirmationRequired):
            win.close_window(query="foot", workspace="4")

    action, detail = seen[0]
    assert "workspace 4" in action
    assert "workspace 4" in detail and "777" in detail


def test_close_window_refuses_kraveins_own_window(fake_desktop, monkeypatch):
    from kravein.skills import windows as win

    monkeypatch.setattr(win, "_own_pids", lambda: {4242})
    backend = fake_desktop(_FakeBackend([_FakeWindow("mine", "foot", 2, pid=4242)]))
    with safety.confirmer_scope(lambda *_: True):
        out = win.close_window(query="foot")
    assert backend.closed == []
    assert "Refusing" in out


def test_close_window_rechecks_the_target_before_acting(fake_desktop):
    """Resolution, confirmation and action are three separate moments."""
    from kravein.skills import windows as win

    backend = fake_desktop(_FakeBackend([_FakeWindow("a", "foot", 4)]))

    def vanish(action, detail, danger):
        # The window closes by itself while the card is on screen.
        backend.windows = []
        return True

    with safety.confirmer_scope(vanish):
        out = win.close_window(query="foot")
    assert backend.closed == [], "acted on a handle that was already stale"
    assert "no longer there" in out


def test_close_window_puts_the_user_back_where_they_were(fake_desktop):
    """Acting has to focus the target; the user should not be left there."""
    from kravein.skills import windows as win

    here = _FakeWindow("here", "chrome", 1)
    target = _FakeWindow("target", "foot", 4)
    backend = _FakeBackend([here, target])
    switched: list[str] = []
    # Closing a window necessarily focuses it first (see _act_on), so the fake
    # models that: after the close, focus is left on the target's workspace.
    state = {"focused": here}

    def close(window):
        state["focused"] = window
        backend.closed.append(window.id)
        backend.windows = [w for w in backend.windows if w.id != window.id]
        return True

    backend.close = close
    backend.kill = close
    backend.focused = lambda: state["focused"]
    backend.workspace = lambda t: (switched.append(t), state.update(focused=here), True)[2]
    fake_desktop(backend)

    with safety.confirmer_scope(lambda *_: True):
        win.close_window(query="foot", workspace="4")

    assert switched == ["1"], f"did not restore the origin workspace: {switched}"


def test_killing_the_terminal_running_kravein_is_blocked():
    """A4-1: check_process only ever guarded os.getpid().

    The parent terminal running `kravein serve` was fair game, and killing it
    kills the assistant — the window bug one layer down.
    """
    import psutil

    parent = psutil.Process().parent()
    if parent is None:
        pytest.skip("no parent process to test against")

    verdict = safety.check_process("foot", pid=parent.pid)
    assert verdict.blocked, "killing the terminal running Kravein was allowed"
    assert "running it" in verdict.block_reason or "assistant" in verdict.block_reason


def test_own_pids_covers_the_ancestor_chain():
    import os

    pids = safety.own_pids()
    assert os.getpid() in pids
    assert len(pids) > 1, "only the process itself — ancestors are unprotected"


def test_close_app_excludes_kraveins_own_window(monkeypatch):
    """A1-2: close_app closed every match everywhere, with no self-protection."""
    from kravein.skills import apps as apps_mod

    windows = [
        _FakeWindow("mine", "foot", 3, pid=4242, title="claude"),
        _FakeWindow("other", "foot", 4, pid=99, title="spare"),
    ]
    monkeypatch.setattr(apps_mod.desktop, "find_windows", lambda q: list(windows))
    monkeypatch.setattr(safety, "own_pids", lambda: {4242})

    seen = []
    with safety.confirmer_scope(lambda a, d, g: (seen.append((a, d)), False)[1]):
        with pytest.raises(safety.ConfirmationRequired):
            apps_mod.close_app(app_name="foot")

    action, detail = seen[0]
    assert "1 window" in action, f"did not exclude its own window: {action}"
    assert "workspace 4" in detail and "99" in detail


def test_close_app_scopes_by_workspace(monkeypatch):
    from kravein.skills import apps as apps_mod

    windows = [
        _FakeWindow("a", "foot", 2, pid=1), _FakeWindow("b", "foot", 4, pid=2),
    ]
    monkeypatch.setattr(apps_mod.desktop, "find_windows", lambda q: list(windows))
    monkeypatch.setattr(safety, "own_pids", lambda: set())

    seen = []
    with safety.confirmer_scope(lambda a, d, g: (seen.append(d), False)[1]):
        with pytest.raises(safety.ConfirmationRequired):
            apps_mod.close_app(app_name="foot", workspace="4")
    assert "workspace 4" in seen[0] and "workspace 2" not in seen[0]


# ---------------------------------------------------------------------------
