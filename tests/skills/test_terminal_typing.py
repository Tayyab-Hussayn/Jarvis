"""Tests: terminal typing.

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


# Terminal typing
# ---------------------------------------------------------------------------

def test_terminal_refuses_to_type_into_kraveins_own_window(fake_desktop, monkeypatch):
    from kravein.skills import terminal as term

    monkeypatch.setattr(term, "own_pids", lambda: {4242})
    monkeypatch.setattr(term.inputs, "backend_name", lambda: "ydotool")
    fake_desktop(_FakeBackend([_FakeWindow("mine", "foot", 3, pid=4242)]))

    with safety.confirmer_scope(lambda *_: True):
        out = term.type_into_terminal(command="ls", query="foot")
    assert "Refusing" in out


def test_terminal_command_is_risk_classified(fake_desktop, monkeypatch):
    """Typing `rm -rf` at a shell is not safer than executing it."""
    from kravein.skills import terminal as term

    monkeypatch.setattr(term, "own_pids", lambda: set())
    monkeypatch.setattr(term.inputs, "backend_name", lambda: "ydotool")
    fake_desktop(_FakeBackend([_FakeWindow("t", "foot", 4, pid=9)]))

    seen = []
    with safety.confirmer_scope(lambda a, d, g: (seen.append((a, d, g)), False)[1]):
        with pytest.raises(safety.ConfirmationRequired):
            term.type_into_terminal(command="rm -rf ~/notes", query="foot")

    action, detail, danger = seen[0]
    assert danger >= Danger.DESTRUCTIVE
    assert "removes files" in detail


def test_terminal_refuses_an_ambiguous_target(fake_desktop, monkeypatch):
    from kravein.skills import terminal as term

    monkeypatch.setattr(term, "own_pids", lambda: set())
    monkeypatch.setattr(term.inputs, "backend_name", lambda: "ydotool")
    fake_desktop(_FakeBackend([
        _FakeWindow("a", "foot", 2, pid=1), _FakeWindow("b", "foot", 4, pid=2),
    ]))
    out = term.type_into_terminal(command="ls", query="foot")
    assert "matches 2 windows" in out


def test_terminal_does_not_type_when_focus_fails(fake_desktop, monkeypatch):
    """Typing into a window that was never focused types into something else."""
    from kravein.skills import terminal as term

    monkeypatch.setattr(term, "own_pids", lambda: set())
    monkeypatch.setattr(term.inputs, "backend_name", lambda: "ydotool")
    monkeypatch.setattr(term, "_focus_and_verify", lambda w: False)
    typed = []
    monkeypatch.setattr(term.inputs, "type_text", lambda t, **k: typed.append(t))
    fake_desktop(_FakeBackend([_FakeWindow("t", "foot", 4, pid=9)]))

    with safety.confirmer_scope(lambda *_: True):
        out = term.type_into_terminal(command="ls", query="foot")
    assert typed == [], "typed without confirming focus"
    assert "Could not focus" in out


# ---------------------------------------------------------------------------
