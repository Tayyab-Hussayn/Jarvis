"""Tests: update_plan, the round/token budget and the partial report (#217)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from kravein import agent as agent_mod  # noqa: E402
from kravein import config, llm, registry  # noqa: E402
from kravein.agents import delegate as dg, plan as planmod  # noqa: E402
from kravein.agents.state import StateTracker  # noqa: E402
from conftest import _agent_with  # noqa: E402
from test_delegate import (  # noqa: E402
    _zz_skills, call, orchestrate, say, setting,  # noqa: F401  (fixture is autouse there)
)

ITEMS = [
    {"content": "Find the files", "status": "completed"},
    {"content": "Edit config.py", "status": "in_progress"},
    {"content": "Run the tests", "status": "pending"},
]
DELEGATE = call("delegate", agent="system-ops", task="rename the setting", wait=True)
PARTIAL_REPORT = (
    "OUTCOME: partial\nSUMMARY: Renamed it in two of three files.\n"
    "CHANGED:\n- config.py\nEVIDENCE:\n- read_file showed the new name\n"
    "LEFT:\n- Run the tests\n"
)


# -- validation ------------------------------------------------------------------

@pytest.mark.parametrize("items,fragment", [
    ("nope", "list of"),
    ([{"content": "a", "status": "in_progress"}, {"content": "b", "status": "in_progress"}],
     "Only one item may be in_progress"),
    ([{"content": "", "status": "pending"}], "no content"),
    ([{"content": "  ", "status": "pending"}], "no content"),
    ([{"status": "pending"}], "no content"),
    ([{"content": "a", "status": "doing"}], "pending, in_progress, completed"),
    ([{"content": "a"}], "pending, in_progress, completed"),
    ([{"content": f"step {i}", "status": "pending"} for i in range(21)], "at most 20"),
    (["just text"], "not an object"),
    ([{"content": "x" * 400, "status": "pending"}], "under 300"),
])
def test_bad_plans_are_rejected_with_a_fix_in_the_message(items, fragment):
    plan, error = planmod.validate(items)
    assert plan == [] and fragment in error


def test_a_good_plan_is_normalised_and_rendered_compactly():
    plan, error = planmod.validate(
        [{"content": " Find   it ", "status": "Completed"}, {"content": "Edit", "status": "IN_PROGRESS"}])
    assert error == ""
    assert plan == [{"content": "Find it", "status": "completed"},
                    {"content": "Edit", "status": "in_progress"}]
    assert planmod.render(plan) == "Plan (1/2 done):\n[x] Find it\n[>] Edit"
    assert len(planmod.validate([{"content": "a", "status": "pending"}] * 20)[0]) == 20


def test_the_skill_replaces_the_whole_plan_and_rejects_without_touching_it():
    agent, _ = _agent_with([], profile="system-ops")
    out = registry.dispatch("update_plan", {"items": ITEMS}, context=agent)
    assert out.startswith("Plan (1/3 done):") and agent.tool_state["plan"][1]["status"] == "in_progress"
    bad = registry.dispatch(
        "update_plan",
        {"items": [{"content": "a", "status": "in_progress"}, {"content": "b", "status": "in_progress"}]},
        context=agent)
    assert bad.startswith("Plan not saved:") and "Only one item" in bad
    assert len(agent.tool_state["plan"]) == 3                       # unchanged
    assert registry.dispatch("update_plan", {"items": []}, context=agent) == "Plan cleared."
    assert "plan" not in agent.tool_state


def test_no_agent_run_means_no_plan():
    assert "no agent run" in registry.dispatch("update_plan", {"items": ITEMS})


# -- the schema ----------------------------------------------------------------

def test_schema_is_a_real_array_of_objects_with_a_status_enum():
    decl = registry.tool_declarations(names={"update_plan"})[0]
    items = decl["parameters"]["properties"]["items"]
    assert items["type"] == "array"
    obj = items["items"]
    assert obj["type"] == "object" and sorted(obj["required"]) == ["content", "status"]
    assert obj["properties"]["status"]["enum"] == ["pending", "in_progress", "completed"]
    assert obj["properties"]["content"]["type"] == "string"


def test_plain_dict_lists_and_json_strings_still_work():
    assert registry._json_schema_for(list[dict]) == {"type": "array", "items": {"type": "object"}}
    schema = {"type": "array", "items": {"type": "object"}}
    assert registry._coerce('[{"content": "a, b", "status": "pending"}]', schema) == [
        {"content": "a, b", "status": "pending"}]
    assert registry._coerce("a, b", {"type": "array"}) == ["a", "b"]       # unchanged
    agent, _ = _agent_with([], profile="system-ops")
    out = registry.dispatch("update_plan", {"items": json.dumps(ITEMS)}, context=agent)
    assert out.startswith("Plan (1/3 done)")


def test_only_profiles_that_list_it_get_update_plan():
    assert "update_plan" not in {d["name"] for d in registry.tool_declarations()}
    assert "update_plan" in registry.resolve_tools(["update_plan"])
    ops = agent_mod.load_profile("system-ops")
    assert "update_plan" in registry.resolve_tools(ops.tools)
    assert "update_plan" not in registry.resolve_tools(agent_mod.load_profile("research").tools)
    assert "update_plan" not in registry.resolve_tools(agent_mod.load_profile("orchestrator").tools)


# -- the plan in the context tail --------------------------------------------------

def test_the_plan_rides_in_the_tail_and_survives_trimming(monkeypatch):
    agent, _ = _agent_with([say("summary of older turns")], profile="system-ops")
    agent.tool_state["plan"] = [dict(i) for i in ITEMS]
    for n in range(12):                                   # a long history
        agent.messages += [{"role": "user", "content": f"old request {n} " + "x" * 400},
                           {"role": "assistant", "content": "ok " + "y" * 400}]
    agent.messages.append({"role": "user", "content": "current"})
    setting(monkeypatch, llm__history_token_budget=500)
    before = len(agent.messages)
    agent._trim_history()
    assert len(agent.messages) < before                   # history really was folded
    tail = agent._with_context()[-1]["content"]
    assert "Your current plan" in tail and "[>] Edit config.py" in tail and "[ ] Run the tests" in tail
    assert "Your current plan" not in json.dumps(agent.messages)          # never stored in history


def test_no_plan_adds_nothing_and_a_new_request_forgets_the_old_plan():
    agent, _ = _agent_with([say("hi")], profile="system-ops")
    assert all("current plan" not in m["content"] for m in agent._with_context())
    agent.tool_state["plan"] = [dict(i) for i in ITEMS]
    agent.ask("another request")
    assert "plan" not in agent.tool_state


def test_the_model_sees_its_plan_on_the_next_round():
    agent, stub = _agent_with(
        [call("update_plan", items=ITEMS), say("done")], profile="system-ops")
    seen = []
    real = stub.complete

    def spy(messages, **kw):
        seen.append(messages)
        return real(messages, **kw)

    stub.complete = spy
    agent.ask("rename the setting")
    assert "current plan" not in seen[0][-1]["content"]
    assert "current plan" in seen[1][-1]["content"] and "Edit config.py" in seen[1][-1]["content"]


# -- the budget ------------------------------------------------------------------

def test_top_level_budget_still_ends_with_the_old_error(monkeypatch):
    agent, _ = _agent_with([call("update_plan", items=ITEMS)], profile="system-ops", max_rounds=2)
    turn = agent.ask("go")
    assert "Stopped after 2 tool rounds" in turn.error and not turn.budget_exhausted


def test_defaults():
    assert config.DEFAULT_SETTINGS["agents"]["delegate_max_rounds"] == 25
    assert config.DEFAULT_SETTINGS["agents"]["delegate_token_budget"] > 0


def _run(monkeypatch, child_script, **settings):
    setting(monkeypatch, **settings)
    frames = []
    agent, router = orchestrate(
        [DELEGATE, say("ok")], child_script, confirmer=lambda *a: True,
        observer=lambda kind, text, data: frames.append((kind, data)))
    agent.ask("rename it")
    report = [m for m in agent.messages if m["role"] == "tool"][0]["content"]
    return report, router, frames


def test_running_out_of_rounds_gives_a_partial_report_with_the_plan(monkeypatch):
    report, router, _ = _run(
        monkeypatch,
        [call("update_plan", items=ITEMS), call("update_plan", items=ITEMS), say(PARTIAL_REPORT)],
        agents__delegate_max_rounds=2)
    assert "outcome: partial" in report and "Stopped after" not in report
    assert "used all 2 tool rounds" in report
    assert "Plan (1/3 done):" in report and "[>] Edit config.py" in report
    assert "Left to do:\n- Run the tests" in report
    assert "Renamed it in two of three files" in report
    assert router.calls["system-ops"] == 3
    last = router.seen["system-ops"][-1]
    assert not last["tools"]                                # the wrap-up call has no tools
    assert "Do not call any tool" in last["messages"][-1]["content"]


def test_a_failed_summary_call_gives_a_report_built_in_code(monkeypatch):
    # The wrap-up answer is another tool call with no text: nothing usable.
    report, router, _ = _run(
        monkeypatch,
        [call("update_plan", items=ITEMS), call("update_plan", items=ITEMS), call("update_plan", items=ITEMS)],
        agents__delegate_max_rounds=2)
    assert "outcome: partial" in report and "Stopped after" not in report
    assert "Stopped before finishing: used all 2 tool rounds. 1 of 3 plan items are completed." in report
    assert "[>] Edit config.py" in report and "Left to do:" in report
    assert "- Edit config.py" in report and "- Run the tests" in report
    assert "completed: Find the files" in report and "update_plan x2" in report


def test_a_raising_summary_call_gives_the_code_report(monkeypatch):
    setting(monkeypatch, agents__delegate_max_rounds=1)
    agent, router = orchestrate(
        [DELEGATE, say("ok")], [call("update_plan", items=ITEMS)], confirmer=lambda *a: True)
    real = router.complete

    def boom(messages, *, system="", tools=None, **kw):
        if "## Your role: system-ops" in system and router.calls["system-ops"] >= 1:
            raise llm.LLMError("provider down")
        return real(messages, system=system, tools=tools, **kw)

    stub = agent._llm.cline
    stub.complete = boom
    agent.ask("rename it")
    report = [m for m in agent.messages if m["role"] == "tool"][0]["content"]
    assert "outcome: partial" in report and "[>] Edit config.py" in report


def test_without_a_plan_the_code_report_says_so(monkeypatch):
    report, _, _ = _run(
        monkeypatch, [call("zz_quick"), call("zz_quick"), call("zz_quick")],
        agents__delegate_max_rounds=2)
    # zz_quick is not a system-ops tool, so every round is refused; still a partial.
    assert "outcome: partial" in report and "Stopped after" not in report


def test_the_token_ceiling_also_stops_the_loop(monkeypatch):
    big = [{"content": "x" * 290, "status": "pending"}] * 10
    report, router, _ = _run(
        monkeypatch,
        [call("update_plan", items=big)] * 30 + [say(PARTIAL_REPORT)],
        agents__delegate_max_rounds=25, agents__delegate_token_budget=1500)
    assert "outcome: partial" in report and "token budget" in report
    assert router.calls["system-ops"] < 25                    # stopped by tokens, not rounds
    assert "Stopped after" not in report


def test_a_generous_token_budget_does_not_interfere(monkeypatch):
    report, router, _ = _run(
        monkeypatch,
        [call("update_plan", items=ITEMS), say("OUTCOME: done\nSUMMARY: All done.\nEVIDENCE:\n- ran it\n")],
        agents__delegate_token_budget=10_000_000)
    assert "outcome: done" in report and "token budget" not in report
    assert "Plan (1/3 done):" in report                         # the plan is reported even when done
    assert "Left to do" not in report


# -- the agent_state frame -------------------------------------------------------

def test_agent_state_frames_carry_the_plan_of_a_running_task(monkeypatch):
    _, _, frames = _run(
        monkeypatch,
        [call("update_plan", items=ITEMS), say("OUTCOME: done\nSUMMARY: ok\n")])
    plan_frames = [d for k, d in frames if k == "agent_state" and d.get("phase") == "plan"]
    assert len(plan_frames) == 1
    assert plan_frames[0]["agent"] == "system-ops" and plan_frames[0]["delegation"]
    assert plan_frames[0]["plan"][1] == {"content": "Edit config.py", "status": "in_progress"}


def test_the_tracker_keeps_the_plan_while_running_and_drops_it_after():
    out = []
    t = StateTracker(out.append)
    t.handle("agent_state", "", {"phase": "turn_start", "agent": "system-ops", "delegation": "d1", "task": "x"})
    t.handle("agent_state", "", {"phase": "plan", "agent": "system-ops", "delegation": "d1",
                                 "plan": ITEMS + [{"content": "junk", "status": "bogus"}]})
    assert out[-1]["state"] == "thinking" and len(out[-1]["plan"]) == 3
    t.handle("tool", "", {"agent": "system-ops", "delegation": "d1", "name": "read_file"})
    assert out[-1]["state"] == "working" and out[-1]["plan"][0]["status"] == "completed"
    snap = {s["agent"]: s for s in t.snapshot(seen_only=True)}
    assert snap["system-ops"]["plan"][1]["content"] == "Edit config.py"
    t.handle("agent_state", "", {"phase": "plan", "agent": "system-ops", "delegation": "d1", "plan": []})
    assert "plan" not in out[-1]
    t.handle("agent_state", "", {"phase": "plan", "agent": "system-ops", "delegation": "d1", "plan": ITEMS})
    t.handle("agent_state", "", {"phase": "delegate_end", "agent": "system-ops",
                                 "delegation": "d1", "outcome": "partial", "summary": "s"})
    assert all("plan" not in f for f in out[-2:])
    assert "plan" not in {s["agent"]: s for s in t.snapshot(seen_only=True)}["system-ops"]


def test_the_plan_on_the_wire_is_capped():
    out = []
    t = StateTracker(out.append)
    t.handle("agent_state", "", {"phase": "plan", "agent": "a",
                                 "plan": [{"content": "c" * 500, "status": "pending"}] * 30})
    assert len(out[-1]["plan"]) == 20 and len(out[-1]["plan"][0]["content"]) <= 200


# -- reports ---------------------------------------------------------------------

OLD_REPORT = ("OUTCOME: done\nSUMMARY: Restarted it.\nCHANGED:\n- bluetooth\n"
              "EVIDENCE:\n- service_status active\nQUESTIONS:\n- none\n")


def test_an_old_report_without_plan_or_left_still_parses_and_renders():
    r = dg.parse_report(OLD_REPORT)
    assert r.outcome == "done" and r.changed == ["bluetooth"] and r.plan == [] and r.left == []
    text = dg.render(r, agent="system-ops", delegation="x", tools=["a"], limit=1500)
    assert "Plan" not in text and "Left to do" not in text and "Restarted it." in text


def test_a_new_report_parses_left_and_the_plan_comes_from_code():
    r = dg.parse_report(PARTIAL_REPORT + "PLAN:\n- [completed] model's own claim\n")
    assert r.left == ["Run the tests"] and r.plan == []


def test_the_length_cap_cuts_evidence_before_the_plan():
    r = dg.Report(outcome="partial", summary="s", plan=[dict(i) for i in ITEMS],
                  evidence=[f"line {n} " + "e" * 80 for n in range(40)])
    text = dg.render(r, agent="system-ops", delegation="x", tools=[], limit=600)
    assert "[>] Edit config.py" in text and "[report cut]" in text
