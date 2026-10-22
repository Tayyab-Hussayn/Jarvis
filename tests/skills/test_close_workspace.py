"""Tests: close workspace.

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


# close_workspace
# ---------------------------------------------------------------------------
#
# Added because "close workspace 2" had no skill behind it: the model
# improvised switch_workspace + close_window per window, which produced a queue
# of confirmations and, when one was declined, a half-cleared workspace.


def test_close_workspace_is_registered_and_destructive():
    entry = registry.get_skill("close_workspace")
    assert entry is not None
    assert entry.danger >= Danger.DESTRUCTIVE


def test_close_workspace_asks_once_for_the_whole_workspace(fake_desktop):
    """One prompt, not one per window — the point of the skill."""
    from kravein.skills import windows as win

    backend = fake_desktop(_FakeBackend([
        _FakeWindow("a", "foot", 2), _FakeWindow("b", "chrome", 2),
        _FakeWindow("c", "kitty", 2), _FakeWindow("z", "other", 3),
    ]))

    asked = []
    with safety.confirmer_scope(lambda a, d, g: (asked.append(a), True)[1]):
        out = win.close_workspace(workspace="2")

    assert len(asked) == 1, f"asked {len(asked)} times"
    assert sorted(backend.closed) == ["a", "b", "c"]
    assert "closed 3 of 3" in out
    assert [w.id for w in backend.windows] == ["z"], "touched another workspace"


def test_close_workspace_never_closes_the_window_hosting_kravein(fake_desktop, monkeypatch):
    """Structural, not a matter of what the user answers.

    A window was once targeted by list position and the assistant killed its
    own terminal mid-task. A bulk close is exactly where that repeats.
    """
    from kravein.skills import windows as win

    monkeypatch.setattr(win, "_own_pids", lambda: {4242})
    backend = fake_desktop(_FakeBackend([
        _FakeWindow("mine", "foot", 2, pid=4242),
        _FakeWindow("other", "chrome", 2, pid=1),
    ]))

    # Approve everything — protection must not depend on the answer.
    with safety.confirmer_scope(lambda *_: True):
        out = win.close_workspace(workspace="2")

    assert "mine" not in backend.closed, "closed the window running Kravein"
    assert backend.closed == ["other"]
    assert "skipped" in out.lower()


def test_close_workspace_refuses_when_only_its_own_window_is_there(fake_desktop, monkeypatch):
    from kravein.skills import windows as win

    monkeypatch.setattr(win, "_own_pids", lambda: {4242})
    backend = fake_desktop(_FakeBackend([_FakeWindow("mine", "foot", 2, pid=4242)]))

    asked = []
    with safety.confirmer_scope(lambda a, d, g: (asked.append(a), True)[1]):
        out = win.close_workspace(workspace="2")

    assert backend.closed == []
    assert asked == [], "asked about something it was never going to do"
    assert "nothing safe to close" in out


def test_close_workspace_on_an_empty_workspace_asks_nothing(fake_desktop):
    from kravein.skills import windows as win

    fake_desktop(_FakeBackend([_FakeWindow("z", "other", 3)]))
    asked = []
    with safety.confirmer_scope(lambda a, d, g: (asked.append(a), True)[1]):
        out = win.close_workspace(workspace="2")
    assert asked == []
    assert "already empty" in out


def test_close_workspace_reports_windows_that_would_not_close(fake_desktop):
    """An app showing a save prompt must be named, not silently counted."""
    from kravein.skills import windows as win

    fake_desktop(_FakeBackend(
        [_FakeWindow("a", "foot", 2), _FakeWindow("b", "editor", 2)],
        stubborn={"b"},
    ))
    with safety.confirmer_scope(lambda *_: True):
        out = win.close_workspace(workspace="2")

    assert "closed 1 of 2" in out
    assert "still open" in out and "editor" in out
    assert "force=true" in out


def test_close_workspace_declined_blocks_the_forced_retry(fake_desktop):
    from kravein.skills import windows as win

    backend = fake_desktop(_FakeBackend([_FakeWindow("a", "foot", 2)]))
    asked = []
    with safety.confirmer_scope(lambda a, d, g: (asked.append(a), False)[1]):
        with pytest.raises(safety.ConfirmationRequired):
            win.close_workspace(workspace="2")
        with pytest.raises(safety.SafetyError):
            win.close_workspace(workspace="2", force=True)

    assert len(asked) == 1
    assert backend.closed == []


def test_wait_until_gone_returns_as_soon_as_windows_disappear():
    """It used to sleep a fixed 0.9s per window regardless."""
    import time as _time

    from kravein.skills import windows as win

    backend = _FakeBackend([_FakeWindow("a", "foot", 2)])
    backend.windows = []                      # already gone
    started = _time.monotonic()
    remaining = win._wait_until_gone(backend, {"a"}, timeout=3.0)
    assert remaining == set()
    assert _time.monotonic() - started < 0.6, "waited longer than it needed to"


# ---------------------------------------------------------------------------
