"""Tests: toolselect routing.

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


# Tool routing
# ---------------------------------------------------------------------------

def _names(decls):
    return {d["name"] for d in decls}


def test_routing_off_reproduces_the_full_tool_list():
    """The default must be byte-identical to what was sent before routing."""
    full = registry.tool_declarations()
    assert "more_tools" not in _names(full)
    assert _names(full) == {
        n for n, s in registry.all_skills().items()
        if n != "more_tools" and s.available()[0]
    }


def test_routing_narrows_but_keeps_core():
    from kravein import toolselect

    chosen = toolselect.select("turn the volume down")
    assert "settings" in chosen and toolselect.CORE <= chosen
    narrowed = registry.tool_declarations(categories=chosen, routing=True)
    assert len(narrowed) < len(registry.tool_declarations())
    assert "more_tools" in _names(narrowed)
    assert "run_command" in _names(narrowed)  # the escape hatch survives


def test_routing_fails_open_when_nothing_matches():
    from kravein import toolselect

    assert toolselect.select("hmm") is None


def test_routing_understands_roman_urdu_and_sticky_categories():
    from kravein import toolselect

    assert "schedule" in toolselect.select("kal 5 baje yaad dilana")
    assert "browser" in toolselect.select("hmm", sticky={"browser"})


def test_more_tools_lists_every_category():
    """The docstring is the schema; a category it omits is unreachable."""
    doc = registry.all_skills()["more_tools"].description
    for category in registry.categories():
        if category != "tools":
            assert category in doc


def test_every_category_has_a_routing_decision():
    """A new category must be routable or core, or routing silently hides it."""
    from kravein import toolselect

    for category in registry.categories():
        assert (
            category in toolselect.CATEGORY_HINTS
            or category in toolselect.CORE
            or category == "tools"
        ), category


def test_agent_widens_toolset_via_more_tools(monkeypatch):
    from kravein import agent as agent_mod, config

    monkeypatch.setattr(
        config, "get",
        lambda key, default=None: "auto" if key == "llm.tool_routing" else default,
    )
    a = agent_mod.Agent.__new__(agent_mod.Agent)
    a._tool_categories, a._turn_categories, a.source = set(), None, "test"

    tools = a._tools_for_turn("turn the volume down")
    assert "browser_open" not in _names(tools)

    registry.dispatch("more_tools", {"categories_wanted": ["browser"]}, context=a)
    widened = a._current_tools("turn the volume down", tools)
    assert "browser_open" in _names(widened)
    assert "browser" in a._tool_categories  # and it sticks for later turns
