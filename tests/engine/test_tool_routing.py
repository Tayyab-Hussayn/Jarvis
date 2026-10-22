"""Pins tool-routing behaviour: default off, narrowing never gates, fail-open.

Complements the routing tests in tests/test_linux_port.py; nothing here needs
a provider because only the tool-list plumbing is exercised.
"""

from __future__ import annotations

import threading

import pytest

from kravein import agent as agent_mod, audit, config, registry, toolselect


def _names(decls):
    return {d["name"] for d in decls}


def _agent(monkeypatch, mode):
    monkeypatch.setattr(
        config, "get",
        lambda key, default=None: mode if key == "llm.tool_routing" else default,
    )
    a = agent_mod.Agent.__new__(agent_mod.Agent)
    a._tool_categories, a._turn_categories, a.source = set(), None, "test"
    return a


@pytest.fixture
def events(monkeypatch):
    seen = []
    monkeypatch.setattr(audit, "record", lambda kind, **kw: seen.append((kind, kw)))
    return seen


def test_default_config_is_off():
    assert config.DEFAULT_SETTINGS["llm"]["tool_routing"] == "off"


@pytest.mark.parametrize("mode", ["off", "", "OFF", "on", "yes", None])
def test_only_auto_enables_routing(monkeypatch, mode):
    a = _agent(monkeypatch, mode)
    assert a._routing_on() is False
    tools = a._tools_for_turn("turn the volume down")
    assert _names(tools) == _names(registry.tool_declarations())
    assert "more_tools" not in _names(tools)
    assert a._turn_categories is None


def test_auto_is_case_insensitive(monkeypatch):
    assert _agent(monkeypatch, "AUTO")._routing_on() is True


def test_off_ignores_sticky_categories_and_does_not_audit(monkeypatch, events):
    a = _agent(monkeypatch, "off")
    a._tool_categories.add("browser")
    a._tools_for_turn("hmm")
    assert not [e for e in events if e[0] == "agent.tools_routed"]


def test_unmatched_request_fails_open_to_every_skill(monkeypatch, events):
    a = _agent(monkeypatch, "auto")
    tools = a._tools_for_turn("hmm")
    assert _names(tools) == _names(registry.tool_declarations())
    # more_tools is meaningless when nothing was narrowed.
    assert "more_tools" not in _names(tools)
    assert a._turn_categories is None
    [(_, data)] = [e for e in events if e[0] == "agent.tools_routed"]
    assert data["categories"] == "all"
    assert data["tools"] == len(tools)


def test_narrowed_turn_audits_categories_and_count(monkeypatch, events):
    a = _agent(monkeypatch, "auto")
    tools = a._tools_for_turn("turn the volume down")
    [(_, data)] = [e for e in events if e[0] == "agent.tools_routed"]
    assert "settings" in data["categories"]
    assert data["categories"] == sorted(data["categories"])
    assert data["tools"] == len(tools)
    assert a._turn_categories == set(data["categories"])


def test_sticky_category_narrows_a_vague_followup(monkeypatch):
    a = _agent(monkeypatch, "auto")
    a._tool_categories.add("browser")
    tools = a._tools_for_turn("hmm")
    assert "browser_open" in _names(tools)
    assert "more_tools" in _names(tools)
    assert len(tools) < len(registry.tool_declarations())


def test_core_categories_are_always_present(monkeypatch):
    a = _agent(monkeypatch, "auto")
    names = _names(a._tools_for_turn("turn the volume down"))
    for category, skills in registry.categories().items():
        if category in toolselect.CORE:
            assert {s.name for s in skills if s.available()[0]} <= names


def test_tool_list_is_reused_until_widened(monkeypatch):
    a = _agent(monkeypatch, "auto")
    tools = a._tools_for_turn("turn the volume down")
    assert a._current_tools("x", tools) is tools  # byte-stable for prompt cache
    a.enable_categories(["browser"])
    widened = a._current_tools("x", tools)
    assert widened is not tools and "browser_open" in _names(widened)


def test_enable_categories_without_narrowing_is_harmless(monkeypatch):
    a = _agent(monkeypatch, "auto")
    a._tools_for_turn("hmm")  # fail-open: _turn_categories is None
    a.enable_categories(["browser"])
    assert a._turn_categories is None
    assert a._tool_categories == {"browser"}
    cached = registry.tool_declarations()
    assert a._current_tools("hmm", cached) is cached


def test_more_tools_unknown_only_lists_choices_and_changes_nothing(monkeypatch):
    a = _agent(monkeypatch, "auto")
    a._tools_for_turn("turn the volume down")
    before = set(a._tool_categories), set(a._turn_categories)
    out = registry.dispatch("more_tools", {"categories_wanted": ["nope"]}, context=a)
    assert "No such topic" in str(out)
    assert (set(a._tool_categories), set(a._turn_categories)) == before


def test_more_tools_ignores_unknown_but_adds_known(monkeypatch):
    a = _agent(monkeypatch, "auto")
    a._tools_for_turn("turn the volume down")
    out = registry.dispatch(
        "more_tools", {"categories_wanted": [" Browser ", "nope"]}, context=a
    )
    assert "unknown, ignored: nope" in str(out)
    assert "browser" in a._turn_categories


def test_more_tools_cannot_enable_its_own_category():
    class Ctx:
        def enable_categories(self, wanted):  # must not run
            raise AssertionError(wanted)

    out = registry.dispatch("more_tools", {"categories_wanted": ["tools"]}, context=Ctx())
    assert "No such topic" in str(out)


def test_more_tools_without_routing_context_is_a_noop():
    out = registry.dispatch(
        "more_tools", {"categories_wanted": ["browser"]}, context=object()
    )
    assert "not active" in str(out)


def test_reset_clears_sticky_categories(monkeypatch, events):
    a = _agent(monkeypatch, "auto")
    a._lock, a.messages = threading.RLock(), []
    a._tool_categories.add("browser")
    a.reset()
    assert a._tool_categories == set()


def test_routing_only_skills_hidden_unless_routing_flag():
    assert "more_tools" not in _names(registry.tool_declarations(categories={"settings"}))
    assert "more_tools" in _names(
        registry.tool_declarations(categories={"settings"}, routing=True)
    )


@pytest.mark.parametrize("text", ["", None, "   "])
def test_blank_request_matches_nothing(text):
    assert toolselect.classify(text) == set()
    assert toolselect.select(text) is None


def test_select_is_a_superset_of_core_and_sticky():
    chosen = toolselect.select("turn the volume down", sticky=frozenset({"games"}))
    assert toolselect.CORE | {"games", "settings"} <= chosen


def test_classify_is_case_insensitive():
    assert "settings" in toolselect.classify("TURN THE VOLUME DOWN")


def test_hint_categories_are_real_and_routing_only_set_is_pinned():
    assert set(toolselect.CATEGORY_HINTS) <= set(registry.categories())
    assert toolselect.ROUTING_ONLY_SKILLS == {"more_tools"}
