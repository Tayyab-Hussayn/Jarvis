"""browser_type(submit=True) must ask before it presses Enter (#239)."""

from __future__ import annotations

import types

import pytest

from kravein import safety
from kravein.skills import browser as skill


class FakePage:
    url = "https://example.com/login"

    def __init__(self):
        self.pressed = []

    def title(self):
        return "Sign in"

    def wait_for_load_state(self, *a, **k):
        pass


class FakeField:
    def __init__(self, page):
        self.page = page
        self.value = ""

    def click(self):
        pass

    def fill(self, text):
        self.value = text

    def press(self, key):
        self.page.pressed.append(key)


@pytest.fixture
def page(monkeypatch):
    p = FakePage()
    field = FakeField(p)
    p.field = field
    monkeypatch.setattr(skill.browser, "available", lambda: (True, ""))
    monkeypatch.setattr(skill.browser, "running", lambda: True)
    monkeypatch.setattr(skill.browser, "active_page", lambda ctx: p)
    monkeypatch.setattr(skill.browser, "call", lambda fn: fn(None))
    monkeypatch.setattr(skill, "_find_field", lambda pg, d: field)
    return p


def test_typing_without_submit_needs_no_card(page):
    assert skill.browser_type("Email", "a@b.c") == "Typed into 'Email'."
    assert page.field.value == "a@b.c" and page.pressed == []


def test_unattended_submit_is_refused_but_text_stays_typed(page):
    out = skill.browser_type("Email", "a@b.c", submit=True, _source="job")
    assert page.pressed == []
    assert "did NOT submit" in out and page.field.value == "a@b.c"


def test_declined_card_does_not_submit(page):
    seen = []

    def no(prompt, detail="", danger=None):
        seen.append(prompt + " " + detail)
        return False

    with safety.confirmer_scope(no):
        out = skill.browser_type("Email", "a@b.c", submit=True)
    assert page.pressed == [] and "did NOT submit" in out
    assert seen and "example.com" in seen[0] and "Sign in" in seen[0] and "Email" in seen[0]


def test_approved_card_submits(page):
    with safety.confirmer_scope(lambda *_: True):
        out = skill.browser_type("Email", "a@b.c", submit=True)
    assert page.pressed == ["Enter"] and "submitted" in out


def test_page_that_moved_while_the_card_was_open_is_not_submitted(page):
    def yes_then_navigate(prompt, detail="", danger=None):
        page.url = "https://evil.example/"
        return True

    with safety.confirmer_scope(yes_then_navigate):
        out = skill.browser_type("Email", "a@b.c", submit=True)
    assert page.pressed == [] and "not submitted" in out
