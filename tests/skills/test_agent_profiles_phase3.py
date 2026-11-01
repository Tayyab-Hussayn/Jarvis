"""Phase 3 sub-agent profiles: system-ops, research, reviewer (+ Orchestrator guidance).

The tool sets are the safety boundary (dispatch refuses anything outside a
profile), so these tests pin them against the live registry instead of
trusting the prose in each SKILL.md.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kravein import registry
from kravein.agents import load_profile
from kravein.safety import Danger

REPO = Path(__file__).resolve().parents[2]
SCENARIOS = REPO / "tools" / "eval" / "agents"

# The only skills allowed above SAFE in a read-oriented profile, and why.
# research: creating a document is the whole point of "documents"; `memory`
# Memory is deliberately NOT an exception: research reads untrusted web
# pages, so it may `recall` but must never write what a page says into memory.
RESEARCH_EXCEPTIONS = {
    "convert_document", "create_document", "create_pdf",
    "create_presentation", "create_spreadsheet",
}
# reviewer: a screenshot writes one image file and changes nothing else.
REVIEWER_EXCEPTIONS = {"take_screenshot"}

# The Orchestrator's own tool set is pinned: phase 3 changes its guidance only.
ORCHESTRATOR_TOOLS = (
    "windows", "phone", "settings", "notify", "memory", "routines", "schedule",
    "open_app", "close_app", "list_apps", "search_apps", "system_status",
    "disk_usage", "network_status", "battery_status", "weather", "web_search",
    "open_url", "search_and_open", "lock_screen", "suspend_system",
    "shutdown_system", "reboot_system", "cancel_shutdown", "read_clipboard",
    "write_clipboard", "take_screenshot", "delegate",
)


@pytest.fixture(scope="module", autouse=True)
def _skills():
    registry.load_skills()


def _tools(name: str) -> frozenset[str]:
    return registry.resolve_tools(load_profile(name).tools)  # raises on a bad name


def _danger(name: str) -> Danger:
    return registry.all_skills()[name].danger


@pytest.mark.parametrize("agent", ["system-ops", "research", "reviewer"])
def test_profile_resolves_and_cannot_delegate(agent):
    tools = _tools(agent)
    assert tools and "delegate" not in tools
    assert load_profile(agent).instructions
    # Bodies are the model's instructions: concrete, not an essay.
    assert len(load_profile(agent).instructions.splitlines()) <= 120


def test_system_ops_covers_every_required_area():
    p = load_profile("system-ops")
    tools = _tools("system-ops")
    for category in ("shell", "system", "apps", "files", "terminal", "interact",
                     "browser", "games", "schedule", "routines"):
        assert category in p.tools
        assert registry.resolve_tools([category]) <= tools
    for name in ("edit_file", "search_text", "install_package", "run_command",
                 "read_audit_log", "schedule_job", "list_windows"):
        assert name in tools
    # No voice, phone or power controls: those are the Orchestrator's.
    assert not tools & {"shutdown_system", "reboot_system", "suspend_system",
                        "phone_status", "speak"}


def test_system_ops_body_teaches_the_hard_won_rules():
    body = load_profile("system-ops").instructions.lower()
    for phrase in ("inspect before", "edit_file", "ydotool", "first of several",
                   "decline", "evidence", "schedule_job", "cron"):
        assert phrase in body


def test_research_has_no_state_changing_tool_but_documents_and_memory():
    tools = _tools("research")
    above_safe = {n for n in tools if _danger(n) >= Danger.MODERATE}
    assert above_safe <= RESEARCH_EXCEPTIONS, above_safe - RESEARCH_EXCEPTIONS
    assert {"web_search", "fetch_page", "browser_read", "read_file", "recall",
            "create_document"} <= tools
    assert not tools & {"run_command", "write_file", "edit_file", "delete_path",
                        "open_app", "type_text", "install_package", "forget",
                        "memory"}


@pytest.mark.parametrize("call", [
    ("memory", {"action": "remember", "key": "k", "value": "from a web page"}),
    ("remember", {"key": "k", "value": "from a web page"}),
    ("forget", {"key": "k"}),
])
def test_research_cannot_write_memory_through_dispatch(call, monkeypatch, tmp_path):
    from kravein import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    name, args = call
    out = registry.dispatch(name, args, allowed=_tools("research"))
    assert "not available to this agent" in out
    # Reading is the one memory capability it has.
    assert "not available to this agent" not in registry.dispatch(
        "recall", {}, allowed=_tools("research"))


def test_a_profile_entry_never_widens_through_an_alias():
    """An alias in `tools` grants the whole canonical skill (see resolve_tools).

    Sub-agents must list canonical names. The Orchestrator's aliases are pinned
    below and each maps to a skill whose other actions it already holds, except
    `log_out` (power_session), which comes with suspend/reboot/shutdown and is
    still confirmed by the skill itself.
    """
    categories = registry.categories()
    widened = {}
    for agent in ("system-ops", "research", "reviewer", "orchestrator"):
        for entry in load_profile(agent).tools:
            if entry not in categories and registry.get_skill(entry).name != entry:
                widened.setdefault(agent, []).append(entry)
    assert widened == {"orchestrator": [
        "suspend_system", "shutdown_system", "reboot_system",
        "read_clipboard", "write_clipboard",
    ]}


def test_reviewer_is_strictly_read_only():
    tools = _tools("reviewer")
    above_safe = {n for n in tools if _danger(n) >= Danger.MODERATE}
    assert above_safe <= REVIEWER_EXCEPTIONS, above_safe - REVIEWER_EXCEPTIONS
    assert not tools & {"delegate", "run_command", "write_file", "edit_file",
                        "delete_path", "memory", "control_service", "kill_process",
                        "install_package", "open_app", "type_text"}
    assert {"read_audit_log", "take_screenshot", "read_file", "search_text",
            "service_status", "list_windows"} <= tools


def test_orchestrator_tool_set_is_unchanged_and_guides_delegation():
    p = load_profile("orchestrator")
    assert p.tools == ORCHESTRATOR_TOOLS
    body = p.instructions
    for agent in ("system-ops", "research", "reviewer"):
        assert f"`{agent}`" in body
    assert "reviewer" in body and "not confirmed" in body


def _scenario_files():
    return sorted(SCENARIOS.glob("*.json"))


def test_there_is_an_eval_set_for_each_agent():
    assert {p.stem for p in _scenario_files()} == {"orchestrator", "system-ops",
                                                    "research", "reviewer"}


@pytest.mark.parametrize("path", _scenario_files(), ids=lambda p: p.stem)
def test_eval_scenarios_are_well_formed_and_reachable(path):
    data = json.loads(path.read_text())
    agent = data["agent"]
    assert agent == path.stem
    allowed = _tools(agent)
    scenarios = data["scenarios"]
    assert len(scenarios) >= 5
    ids = [s["id"] for s in scenarios]
    assert len(ids) == len(set(ids))
    for s in scenarios:
        assert s["prompt"].strip()
        expected = s["expect_first_tool"]
        # The expected first call must be one this agent is actually allowed to make.
        assert expected in allowed, f"{s['id']}: {expected} is outside {agent}'s tool set"
        if expected == "delegate":
            assert s["expect_delegate_to"] in {"system-ops", "research", "reviewer"}
