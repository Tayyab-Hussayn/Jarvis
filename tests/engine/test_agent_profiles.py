"""Tests: agent profiles (SKILL.md), tool-set enforcement, alias presets (#132)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import audit, config, llm, registry  # noqa: E402
from kravein.agents import (  # noqa: E402
    AgentProfile, ProfileError, list_profiles, load_profile, parse_profile,
)
from kravein.agents import profile as profile_mod  # noqa: E402
from kravein.safety import Danger  # noqa: E402
from conftest import _agent_with  # noqa: E402


@pytest.fixture
def events(monkeypatch):
    """Collect audit records instead of writing the real log."""
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(audit, "record", lambda event, **f: seen.append((event, f)))
    return seen


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)


GOOD = """---
name: scout
purpose: Looks things up.
tools: [memory, disk_usage]
model: zen:some-model
report_max_chars: 900
---
Be brief.

Second paragraph.
"""


# -- loader -----------------------------------------------------------------

def test_example_profile_loads():
    p = load_profile("_example")
    assert p.name == "_example"
    assert p.tools == ("memory", "disk_usage")
    assert p.report_max_chars == 800
    assert "one sentence" in p.instructions


def test_full_profile_parses_every_field():
    p = parse_profile(GOOD, expected_name="scout")
    assert p == AgentProfile(
        name="scout", purpose="Looks things up.", tools=("memory", "disk_usage"),
        instructions="Be brief.\n\nSecond paragraph.", model="zen:some-model",
        report_max_chars=900,
    )


def test_dash_list_and_comments_and_quotes():
    p = parse_profile(
        "---\nname: a\npurpose: \"x: y # not a comment\"  # real comment\n"
        "tools:\n  - memory\n  - 'disk_usage'\n---\nbody", expected_name="a",
    )
    assert p.purpose == "x: y # not a comment"
    assert p.tools == ("memory", "disk_usage")


@pytest.mark.parametrize("text", [
    "name: a\npurpose: p\ntools: [x]\n",                       # no frontmatter
    "---\nname: a\npurpose: p\ntools: [x]\nbody",               # not closed
    "---\nname: a\ntools: [x]\n---\n",                          # no purpose
    "---\nname: a\npurpose: p\n---\n",                          # no tools
    "---\nname: a\npurpose: p\ntools: []\n---\n",               # empty tools
    "---\nname: a\npurpose: p\ntools: memory\n---\n",           # tools not a list
    "---\nname: a\npurpose: p\ntools: [x]\nextra: 1\n---\n",    # unknown key
    "---\nname: a\nname: b\npurpose: p\ntools: [x]\n---\n",     # duplicate key
    "---\nname: Bad Name\npurpose: p\ntools: [x]\n---\n",       # invalid name
    "---\nname: other\npurpose: p\ntools: [x]\n---\n",          # != folder
    "---\nname: a\npurpose: p\ntools: [x]\nreport_max_chars: no\n---\n",
    "---\nname: a\npurpose: p\ntools: [x]\nreport_max_chars: 0\n---\n",
    "---\nname: a\npurpose: p\ntools: [x]\n  stray: 1\n---\n",  # indentation
    "---\n- orphan\nname: a\npurpose: p\ntools: [x]\n---\n",    # list w/o key
])
def test_malformed_profile_is_refused(text):
    with pytest.raises(ProfileError):
        parse_profile(text, expected_name="a")


def test_missing_profile_and_bad_names(tmp_path):
    with pytest.raises(ProfileError):
        load_profile("nope", root=tmp_path)
    with pytest.raises(ProfileError):
        load_profile("../etc", root=tmp_path)


def test_list_profiles_skips_fixtures_and_empty_folders(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "SKILL.md").write_text(GOOD.replace("scout", "real"))
    (tmp_path / "_hidden").mkdir()
    (tmp_path / "_hidden" / "SKILL.md").write_text(GOOD)
    (tmp_path / "empty").mkdir()
    assert list_profiles(root=tmp_path) == ["real"]
    assert "_example" not in list_profiles()


def test_malformed_profile_file_never_half_loads(tmp_path):
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "SKILL.md").write_text("---\nname: bad\npurpose: p\n---\n")
    with pytest.raises(ProfileError):
        profile_mod.load_profile("bad", root=tmp_path)


# -- tool set ---------------------------------------------------------------

def _names(decls):
    return {d["name"] for d in decls}


def test_profile_agent_sends_only_its_tools():
    agent, stub = _agent_with([llm.Reply(text="ok", provider="stub")], profile="_example")
    agent.ask("hi")
    sent = _names(stub.seen_tools)
    expected = registry.resolve_tools(["memory", "disk_usage"])
    assert sent == set(expected) and "disk_usage" in sent
    assert "get_volume" not in sent and "close_window" not in sent


def test_profile_instructions_join_the_shared_prompt():
    agent, stub = _agent_with([llm.Reply(text="ok", provider="stub")], profile="_example")
    agent.ask("hi")
    assert "tiny example agent" in stub.seen_system
    assert "## Your role: _example" in stub.seen_system
    assert "This machine" in stub.seen_system        # shared base, not copied


def test_unknown_tool_in_profile_is_an_error():
    p = AgentProfile(name="x", purpose="p", tools=("memory", "no_such_tool"))
    with pytest.raises(ProfileError):
        _agent_with([], profile=p)


def test_no_profile_agent_is_unchanged():
    agent, stub = _agent_with([llm.Reply(text="ok", provider="stub")])
    agent.ask("hi")
    assert stub.seen_tools == registry.tool_declarations()
    assert agent.profile is None and agent._allowed is None


def test_dispatch_refuses_and_audits_out_of_profile_call(events):
    out = registry.dispatch("get_volume", {}, allowed=frozenset({"disk_usage"}))
    assert "not available to this agent" in out
    refused = [f for e, f in events if e == "skill.profile_refused"]
    # get_volume is now an alias preset of the `volume` action skill (#133);
    # the audit names the canonical skill and records the name the model used.
    assert refused and refused[0]["skill"] == "volume"
    assert refused[0]["called_as"] == "get_volume"
    # Nothing ran: no skill.start record.
    assert not [1 for e, _ in events if e == "skill.start"]


def test_alias_cannot_dodge_the_profile(events):
    out = registry.dispatch("browser_search", {"query": "x"}, allowed=frozenset({"disk_usage"}))
    assert "not available" in out
    refused = [f for e, f in events if e == "skill.profile_refused"][0]
    assert refused["skill"] == "browser_search_web"
    assert refused["called_as"] == "browser_search"


def test_dispatch_without_allowed_is_unchanged():
    assert "not available to this agent" not in registry.dispatch("disk_usage", {})


def test_model_calling_a_tool_outside_its_profile_gets_nothing(events):
    script = [
        llm.Reply(tool_calls=[llm.ToolCall("get_volume", {}, "c1")], provider="stub"),
        llm.Reply(text="done", provider="stub"),
    ]
    agent, _ = _agent_with(script, profile="_example", confirmer=lambda *_: True)
    turn = agent.ask("turn it up")
    tool_msgs = [m for m in agent.messages if m["role"] == "tool"]
    assert "not available to this agent" in tool_msgs[0]["content"]
    assert [e for e, _ in events if e == "skill.profile_refused"]
    assert turn.reply == "done"


# -- alias presets ----------------------------------------------------------

@pytest.fixture
def preset_skill():
    from kravein.registry import skill

    seen: list[dict] = []

    @skill(
        name="zz_job_ctl", category="zz_test", danger=Danger.SAFE,
        aliases={"zz_pause": {"action": "pause"}, "zz_cancel": {"action": "cancel"}},
    )
    def zz_job_ctl(action: str = "status", target: str = "") -> str:
        """Control a thing.

        Args:
            action: status, pause or cancel.
            target: what to act on.
        """
        seen.append({"action": action, "target": target})
        return f"{action}:{target}"

    @skill(name="zz_plain", category="zz_test", aliases=["zz_plain_old"])
    def zz_plain(x: str = "") -> str:
        """Plain."""
        return "plain"

    yield seen
    for n in ("zz_job_ctl", "zz_plain"):
        registry._registry.pop(n, None)
    for a in ("zz_pause", "zz_cancel", "zz_plain_old"):
        registry._aliases.pop(a, None)
        registry._alias_presets.pop(a, None)


def test_alias_preset_wins_over_a_conflicting_argument(preset_skill, events):
    out = registry.dispatch("zz_pause", {"action": "cancel", "target": "j1"})
    assert out == "pause:j1"
    assert preset_skill == [{"action": "pause", "target": "j1"}]
    rec = [f for e, f in events if e == "skill.alias"][0]
    assert rec["alias"] == "zz_pause" and rec["skill"] == "zz_job_ctl"
    assert rec["overridden"] == ["action"]


def test_alias_preset_fills_a_missing_argument(preset_skill):
    assert registry.dispatch("zz_cancel", {"target": "j2"}) == "cancel:j2"


def test_real_name_is_not_preset(preset_skill):
    assert registry.dispatch("zz_job_ctl", {"action": "cancel", "target": "a"}) == "cancel:a"


def test_aliases_are_hidden_from_the_schema(preset_skill):
    names = _names(registry.tool_declarations())
    assert "zz_job_ctl" in names
    assert not names & {"zz_pause", "zz_cancel", "zz_plain_old"}


def test_tuple_aliases_still_work(preset_skill):
    assert registry.dispatch("zz_plain_old", {}) == "plain"
    assert registry.get_skill("zz_plain_old").alias_presets == {}


def test_preset_of_unknown_parameter_is_rejected_at_decoration():
    from kravein.registry import skill

    with pytest.raises(ValueError):
        @skill(name="zz_bad", category="zz_test", aliases={"zz_bad_a": {"nope": 1}})
        def zz_bad(action: str = "") -> str:
            """Bad."""
            return ""

    assert registry.get_skill("zz_bad") is None


def test_preset_cannot_be_used_to_dodge_the_profile(preset_skill, events):
    out = registry.dispatch("zz_pause", {}, allowed=frozenset({"disk_usage"}))
    assert "not available" in out and preset_skill == []
