"""Tests: site search.

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


# Searching inside a site
# ---------------------------------------------------------------------------

def test_web_search_does_not_default_to_google(monkeypatch):
    """Google serves its bot interstitial to an automated profile.

    A user asked to search inside YouTube and ended up on Google's "unusual
    traffic" captcha instead. Half of that was tool choice; the other half was
    an engine that will not serve this browser at all.
    """
    from kravein.skills import browser as skill

    opened: list[str] = []
    monkeypatch.setattr(skill, "browser_open", lambda url, **k: opened.append(url) or "ok")

    skill.browser_search_web("hyprland")
    assert "duckduckgo" in opened[0]

    skill.browser_search_web("hyprland", engine="google")
    assert "google" in opened[1], "an explicit engine must still be honoured"


def test_site_search_reports_when_there_is_no_search_box(monkeypatch):
    """And points at the web-search skill rather than silently doing nothing."""
    from kravein.skills import browser as skill

    monkeypatch.setattr(skill.browser, "available", lambda: (True, ""))
    monkeypatch.setattr(skill.browser, "running", lambda: True)
    monkeypatch.setattr(
        skill.browser, "call",
        lambda fn, **k: {"found": False, "url": "https://example.com/", "title": "Example"},
    )

    message = skill.browser_search_site("anything")
    assert "No search box" in message
    assert "browser_search_web" in message


def test_site_search_does_not_claim_success_when_nothing_moved(monkeypatch):
    """Submitting is not the same as having searched.

    A site search is usually a client-side route change, so a load event can
    fire instantly having gone nowhere. Saying "searched" there would assert a
    result the assistant has not seen.
    """
    from kravein.skills import browser as skill

    monkeypatch.setattr(skill.browser, "available", lambda: (True, ""))
    monkeypatch.setattr(skill.browser, "running", lambda: True)
    monkeypatch.setattr(
        skill.browser, "call",
        lambda fn, **k: {
            "found": True, "moved": False, "url": "https://site.test/",
            "before": "https://site.test/", "title": "Site",
        },
    )

    message = skill.browser_search_site("kittens")
    assert "did not change" in message
    assert not message.startswith("Searched")


def test_site_search_needs_a_site_to_search(monkeypatch):
    from kravein.skills import browser as skill

    monkeypatch.setattr(skill.browser, "available", lambda: (True, ""))
    monkeypatch.setattr(skill.browser, "running", lambda: False)

    message = skill.browser_search_site("kittens")
    assert "browser_open" in message


def test_search_field_prefers_the_visible_input(monkeypatch):
    """Responsive layouts ship two search boxes and hide one."""
    from kravein.platform import browser as platform_browser

    class _Item:
        def __init__(self, visible):
            self._visible = visible

        def is_visible(self):
            return self._visible

        def is_enabled(self):
            return True

    class _Locator:
        def __init__(self, items):
            self._items = items

        def count(self):
            return len(self._items)

        def nth(self, i):
            return self._items[i]

    hidden, shown = _Item(False), _Item(True)

    class _Page:
        def locator(self, selector):
            if selector == 'input[type="search"]':
                return _Locator([])
            return _Locator([hidden, shown])

    found = platform_browser.search_field(_Page(), reveal=False)
    assert found is shown


# ---------------------------------------------------------------------------
