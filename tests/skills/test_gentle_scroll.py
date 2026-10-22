"""Tests: gentle scroll.

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


# Gentle scroll
# ---------------------------------------------------------------------------

def test_parse_rate_accepts_numbers_and_words():
    from kravein import gentle

    assert gentle.parse_rate("2") == (2.0, "")
    assert gentle.parse_rate(3) == (3.0, "")
    assert gentle.parse_rate("slow")[0] < 1.0
    assert gentle.parse_rate("fast")[0] > 1.0
    assert gentle.parse_rate("", 1.5) == (1.5, "")

    _, problem = gentle.parse_rate("banana")
    assert "banana" in problem
    _, problem = gentle.parse_rate("999")
    assert "between" in problem


def test_gentle_scroll_refuses_a_page_with_nothing_to_scroll(monkeypatch):
    """A page that cannot scroll must say so, not go quiet.

    The first version reported success and then sat there doing nothing,
    because the scrollable element was only chosen inside the animation frame
    loop — after the return value the caller checks had been built.
    """
    from kravein import gentle

    monkeypatch.setattr(gentle, "_apply_to_page", lambda rate, direction: {"found": False})
    monkeypatch.setattr(gentle, "_cancel_page", lambda: None)
    started = []
    monkeypatch.setattr(gentle, "_start", lambda session: started.append(session))

    message = gentle.start_browser(1.0, "down", 60, "the page")
    assert "can scroll" in message
    assert not started, "started a session on a page with nothing to scroll"


def test_gentle_scroll_stop_is_safe_when_nothing_is_running():
    from kravein import gentle

    gentle.stop()
    assert gentle.stop() == ""
    assert gentle.current() is None


def test_gentle_scroll_refuses_an_ambiguous_window(monkeypatch):
    """Same rule as close_window: three matches means refuse and list them."""
    from kravein.skills import gentle as skill
    from kravein.skills import windows as win

    def _ambiguous(query, workspace):
        raise win.AmbiguousTarget("3 windows match 'foot'; say which one.")

    monkeypatch.setattr(skill, "_resolve_one", _ambiguous)
    monkeypatch.setattr(skill.desktop, "backend", lambda: _StubBackend())

    message = skill.gentle_scroll(target="foot")
    assert "3 windows match" in message


def test_gentle_scroll_refuses_kraveins_own_window(monkeypatch):
    """Parking the pointer on the assistant's own terminal helps nobody."""
    import os

    from kravein.skills import gentle as skill

    window = _StubWindow(pid=os.getpid())
    monkeypatch.setattr(skill, "_resolve_one", lambda q, w: window)
    monkeypatch.setattr(skill.desktop, "backend", lambda: _StubBackend())

    message = skill.gentle_scroll(target="foot")
    assert "Refusing" in message


def test_gentle_scroll_sends_nothing_when_focus_does_not_land(monkeypatch):
    """Wheel events follow the pointer, so an unfocused target is a hazard."""
    from kravein.skills import gentle as skill

    window = _StubWindow(pid=999999)
    monkeypatch.setattr(skill, "_resolve_one", lambda q, w: window)
    monkeypatch.setattr(skill.desktop, "backend", lambda: _StubBackend())
    monkeypatch.setattr(skill.desktop, "focus_and_verify", lambda w: False)
    monkeypatch.setattr(skill.inputs, "pointer_position", lambda: (10, 10))
    monkeypatch.setattr(
        skill.gentle, "start_window",
        lambda *a, **k: pytest.fail("scrolled a window that was never focused"),
    )

    message = skill.gentle_scroll(target="foot")
    assert "Could not focus" in message


def test_place_pointer_raises_when_the_pointer_did_not_land(monkeypatch):
    """ydotool's absolute mode exits 0 while parking the pointer in a corner.

    Believing it is how wheel events end up scrolling a window nobody asked
    about, so the position is read back and a miss is an error.
    """
    from kravein.platform import inputs

    monkeypatch.setattr(inputs, "move_mouse", lambda x, y, absolute=True: "ok")
    monkeypatch.setattr(inputs, "pointer_position", lambda: (1364, 766))

    from kravein.platform import desktop

    monkeypatch.setattr(desktop, "backend", lambda: _StubBackend())

    with pytest.raises(inputs.InputError):
        inputs.place_pointer(683, 384)


def test_param_docs_keep_wrapped_lines():
    """A wrapped Args: description used to be cut off mid-sentence."""
    from kravein.registry import _parse_param_docs

    doc = (
        "Do a thing.\n\n"
        "Args:\n"
        "    query: Which terminal — app name or part of its title.\n"
        "        Omit for the focused window.\n"
        "    other: Something else.\n"
    )
    parsed = _parse_param_docs(doc)
    assert parsed["query"].endswith("Omit for the focused window.")
    assert parsed["other"] == "Something else."


class _StubWindow:
    def __init__(self, pid):
        self.id = "0xabc"
        self.title = "a terminal"
        self.app = "foot"
        self.pid = pid
        self.workspace = "2"
        self.focused = False
        self.geometry = (0, 0, 800, 600)


class _StubBackend:
    def available(self):
        return True

    def focused(self):
        return None

    def move_cursor(self, x, y):
        return False

    def list_windows(self):
        return []


# ---------------------------------------------------------------------------
