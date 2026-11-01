"""Tests: delegate, the Orchestrator profile, escalation, the switch (#138)."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import agent as agent_mod  # noqa: E402
from kravein import audit, config, llm, registry, safety  # noqa: E402
from kravein.agents import ProfileError, delegate as dg, load_profile  # noqa: E402
from kravein.registry import skill  # noqa: E402
from kravein.safety import Danger, Verdict  # noqa: E402
from conftest import _agent_with  # noqa: E402

REPORT_DONE = (
    "OUTCOME: done\nSUMMARY: Wiped x.\nCHANGED:\n- removed x\nEVIDENCE:\n- listing no "
    "longer shows x\n"
)


def say(text):
    return llm.Reply(text=text, provider="stub")


def call(name, **args):
    return llm.Reply(tool_calls=[llm.ToolCall(name, args, f"c-{name}")], provider="stub")


class Router:
    """One scripted model per role, picked by the system prompt.

    `client()` is a shared singleton, so the Orchestrator and its sub-agent talk
    to the same provider stub; the system prompt tells them apart.
    """

    def __init__(self, orchestrator, subagent):
        self.scripts = {"orchestrator": list(orchestrator), "system-ops": list(subagent)}
        self.calls = {"orchestrator": 0, "system-ops": 0}
        self.seen = {"orchestrator": [], "system-ops": []}

    def install(self, stub):
        stub.complete = self.complete

    def complete(self, messages, *, system="", tools=None, temperature=0.4, model=""):
        role = "system-ops" if "## Your role: system-ops" in system else "orchestrator"
        self.seen[role].append({"messages": messages, "tools": tools, "system": system})
        script = self.scripts[role]
        reply = script[min(self.calls[role], len(script) - 1)]
        self.calls[role] += 1
        return reply


@pytest.fixture(autouse=True)
def _zz_skills():
    effects: dict = {"wiped": [], "sources": [], "memories": [], "quick": 0}
    hook = {"stop": None}

    @skill(name="zz_wipe", category="files", danger=Danger.DESTRUCTIVE)
    def zz_wipe(target: str, _source: str = "local") -> str:
        """Wipe a thing.

        Args:
            target: What to wipe.
        """
        safety.enforce(
            Verdict(danger=Danger.DESTRUCTIVE, reasons=[f"wipes {target}"]),
            f"wipe '{target}'", skill="zz_wipe", source=_source,
            targets=safety.describe_targets(f"zz:{target}"),
        )
        effects["wiped"].append(target)
        effects["sources"].append(_source)
        effects["memories"].append(safety.current_memory())
        if hook["stop"] is not None:
            hook["stop"].set()
        return "WIPED-RAW-OUTPUT " + target

    @skill(name="zz_quick", category="settings", danger=Danger.SAFE)
    def zz_quick() -> str:
        """Quick thing."""
        effects["quick"] += 1
        effects["memories"].append(safety.current_memory())
        return "quick ok"

    effects["hook"] = hook
    yield effects
    for n in ("zz_wipe", "zz_quick"):
        registry._registry.pop(n, None)


@pytest.fixture
def audit_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AUDIT_LOG", tmp_path / "audit.jsonl")
    monkeypatch.setattr(audit, "_fd", None)
    yield tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit, "_fd", None)


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def setting(monkeypatch, **values):
    real = config.get
    monkeypatch.setattr(
        config, "get", lambda dotted, default=None: values.get(dotted.replace(".", "__"), real(dotted, default))
    )


def orchestrate(parent_script, child_script, **kw):
    agent, stub = _agent_with([say("x")], profile="orchestrator", **kw)
    router = Router(parent_script, child_script)
    router.install(stub)
    return agent, router


DELEGATE_WIPE = call("delegate", agent="system-ops", task="wipe x")


# -- profile -----------------------------------------------------------------

def test_orchestrator_profile_resolves_against_the_live_registry():
    p = load_profile("orchestrator")
    tools = registry.resolve_tools(p.tools)       # raises on a renamed skill
    assert "delegate" in tools and "open_app" in tools and "get_volume" in tools
    assert "run_command" not in tools and "delete_path" not in tools


def test_system_ops_profile_has_no_delegate():
    tools = registry.resolve_tools(load_profile("system-ops").tools)
    assert "delegate" not in tools and "run_command" in tools


def test_delegate_is_not_in_the_all_tools_list_or_categories():
    assert "delegate" not in {d["name"] for d in registry.tool_declarations()}
    assert "agents" not in registry.categories()
    agent, _ = _agent_with([say("x")], profile="orchestrator")
    assert "delegate" in {d["name"] for d in agent._tools_for_turn("hi")}


# -- pass-through ---------------------------------------------------------------

def test_end_to_end_orchestrator_delegates_and_gets_only_the_report(_zz_skills):
    prompts = []

    def owner(prompt, detail, danger):
        prompts.append(prompt)
        return True

    agent, router = orchestrate(
        [DELEGATE_WIPE, say("All wiped.")],
        [call("zz_wipe", target="x"), say(REPORT_DONE)],
        confirmer=owner,
    )
    turn = agent.ask("wipe x for me")
    assert turn.reply == "All wiped." and turn.tool_calls == ["delegate"]
    assert _zz_skills["wiped"] == ["x"]
    assert prompts == ["system-ops: wipe 'x'"]            # the card names the sub-agent
    report = [m for m in agent.messages if m["role"] == "tool"][0]["content"]
    assert report.startswith("[system-ops report #") and "outcome: done" in report
    assert "removed x" in report
    # The raw tool output never reached the Orchestrator's context.
    assert "WIPED-RAW-OUTPUT" not in json.dumps(agent.messages)
    assert "WIPED-RAW-OUTPUT" not in json.dumps(router.seen["orchestrator"][-1]["messages"])


def test_owner_decline_ends_the_delegated_task_and_is_reported(_zz_skills):
    agent, router = orchestrate(
        [DELEGATE_WIPE, say("Okay, not done.")],
        [call("zz_wipe", target="x"), call("zz_wipe", target="y"), say(REPORT_DONE)],
        confirmer=lambda *a: False,
    )
    agent.ask("wipe x")
    report = [m for m in agent.messages if m["role"] == "tool"][0]["content"]
    assert "outcome: declined" in report and "declined" in report.lower()
    assert _zz_skills["wiped"] == []
    assert router.calls["system-ops"] == 1                 # no further rounds after the no


def test_unattended_delegate_denies_and_says_needs_approval(_zz_skills):
    agent, _ = orchestrate(
        [DELEGATE_WIPE, say("Could not.")],
        [call("zz_wipe", target="x"), say(REPORT_DONE)],
        confirmer=None,
    )
    agent.ask("wipe x")
    report = [m for m in agent.messages if m["role"] == "tool"][0]["content"]
    assert "outcome: needs-approval" in report
    assert _zz_skills["wiped"] == []


@pytest.mark.parametrize("failure", ["raise", "false"])
def test_confirmer_failures_deny(_zz_skills, failure):
    def broken(*a):
        if failure == "raise":
            raise TimeoutError("socket dropped")
        return False

    agent, _ = orchestrate(
        [DELEGATE_WIPE, say("no")], [call("zz_wipe", target="x"), say(REPORT_DONE)],
        confirmer=broken,
    )
    agent.ask("wipe x")
    assert _zz_skills["wiped"] == []
    assert "outcome: declined" in [m for m in agent.messages if m["role"] == "tool"][0]["content"]


def test_subagent_cannot_approve_its_own_action(_zz_skills):
    # `confirm=true` in the sub-agent's own arguments is ignored, as everywhere.
    agent, _ = orchestrate(
        [DELEGATE_WIPE, say("no")],
        [call("zz_wipe", target="x", confirm=True), say(REPORT_DONE)],
        confirmer=lambda *a: False,
    )
    agent.ask("wipe x")
    assert _zz_skills["wiped"] == []


def test_child_keeps_the_parents_source_and_shares_its_denial_memory(_zz_skills):
    agent, _ = orchestrate(
        [call("zz_quick"), DELEGATE_WIPE, say("ok")],
        [call("zz_wipe", target="x"), say(REPORT_DONE)],
        confirmer=lambda *a: True,
    )
    agent.source = "voice"
    agent.ask("go")
    assert _zz_skills["sources"] == ["voice"]              # never a looser source
    parent_memory, child_memory = _zz_skills["memories"][0], _zz_skills["memories"][1]
    assert parent_memory is child_memory


def test_a_decline_in_the_child_is_remembered_by_the_parent(_zz_skills):
    agent, _ = orchestrate(
        [DELEGATE_WIPE, call("zz_quick"), say("ok")],
        [call("zz_wipe", target="x"), say(REPORT_DONE)],
        confirmer=lambda *a: False,
    )
    agent.ask("wipe x")
    assert "zz:x" in _zz_skills["memories"][0].targets    # the quick call saw the child's no


# -- no nesting, no widening ---------------------------------------------------

def test_no_profile_other_than_the_orchestrator_may_hold_delegate():
    bad = agent_mod.AgentProfile(name="sneaky", purpose="p", tools=("delegate", "memory"))
    with pytest.raises(ProfileError):
        _agent_with([], profile=bad)
    with pytest.raises(ProfileError):
        agent_mod.Agent(source="t", profile="orchestrator", depth=1)


def test_delegate_refuses_any_caller_but_the_orchestrator(audit_file):
    plain, _ = _agent_with([say("x")])
    assert "only available to the Orchestrator" in registry.dispatch(
        "delegate", {"agent": "system-ops", "task": "t"}, context=plain)
    ops, _ = _agent_with([say("x")], profile="system-ops")
    assert "only available to the Orchestrator" in dg.delegate("system-ops", "t", _context=ops)
    assert [r for r in records(audit_file) if r["event"] == "delegate.refused"]


def test_subagent_calling_delegate_or_an_out_of_profile_tool_is_refused(audit_file, _zz_skills):
    agent, router = orchestrate(
        [DELEGATE_WIPE, say("ok")],
        [call("delegate", agent="system-ops", task="again"), call("get_volume"), say(REPORT_DONE)],
        confirmer=lambda *a: True,
    )
    agent.ask("go")
    refused = [r for r in records(audit_file) if r["event"] == "skill.profile_refused"]
    assert {r["skill"] for r in refused} == {"delegate", "get_volume"}
    assert all(r.get("agent") == "system-ops" for r in refused)
    assert router.calls["orchestrator"] == 2 and router.calls["system-ops"] == 3


def test_unknown_subagent_and_empty_task_are_answered_not_run():
    agent, _ = _agent_with([say("x")], profile="orchestrator")
    assert "Unknown sub-agent" in dg.delegate("nope", "t", _context=agent)
    assert "Unknown sub-agent" in dg.delegate("orchestrator", "t", _context=agent)
    assert "needs a task" in dg.delegate("system-ops", "  ", _context=agent)


# -- report contract --------------------------------------------------------------

def test_parse_report_reads_the_last_block():
    text = "chatter\nOUTCOME: partial\nSUMMARY: first\nOUTCOME: Done.\nSUMMARY: second\nCHANGED:\n- a\n* b\nQUESTIONS:\n- q?"
    r = dg.parse_report(text)
    assert (r.outcome, r.summary, r.changed, r.questions) == ("done", "second", ["a", "b"], ["q?"])
    assert dg.parse_report("no structure here") is None
    assert dg.parse_report("OUTCOME: nonsense\nSUMMARY: s").outcome == ""


@pytest.mark.parametrize("child_reply", [say("I just did it."), say(""), say("OUTCOME: done")])
def test_report_fallback_is_never_empty(_zz_skills, child_reply):
    agent, _ = orchestrate(
        [DELEGATE_WIPE, say("ok")], [call("zz_wipe", target="x"), child_reply],
        confirmer=lambda *a: True,
    )
    agent.ask("go")
    report = [m for m in agent.messages if m["role"] == "tool"][0]["content"]
    assert "Summary:" in report and "zz_wipe" in report
    assert "outcome: partial" in report                    # unproven: never "done"
    assert "tool calls made: zz_wipe" in report


def test_report_is_capped_to_the_profile_limit(_zz_skills):
    huge = "OUTCOME: done\nSUMMARY: " + "long " * 2000 + "\nCHANGED:\n" + "- item\n" * 500
    agent, _ = orchestrate(
        [DELEGATE_WIPE, say("ok")], [call("zz_wipe", target="x"), say(huge)],
        confirmer=lambda *a: True,
    )
    agent.ask("go")
    report = [m for m in agent.messages if m["role"] == "tool"][0]["content"]
    limit = load_profile("system-ops").report_max_chars
    assert len(report) <= limit and report.startswith("[system-ops report #")
    assert report.endswith("[report cut]")


def test_the_models_own_claim_cannot_override_a_forced_outcome(_zz_skills):
    agent, _ = orchestrate(
        [DELEGATE_WIPE, say("ok")],
        [call("zz_wipe", target="x"), say(REPORT_DONE)],
        confirmer=lambda *a: False,
    )
    agent.ask("go")
    report = [m for m in agent.messages if m["role"] == "tool"][0]["content"]
    assert "outcome: done" not in report and "outcome: declined" in report


# -- stop, time, rounds -------------------------------------------------------------

def test_stop_reaches_the_subagent(_zz_skills):
    stop = threading.Event()
    _zz_skills["hook"]["stop"] = stop                    # the skill sets it mid-task
    agent, router = orchestrate(
        [DELEGATE_WIPE, say("stopped")],
        [call("zz_wipe", target="x"), call("zz_wipe", target="y"), say(REPORT_DONE)],
        confirmer=lambda *a: True,
    )
    agent.ask("go", stop)
    assert _zz_skills["wiped"] == ["x"]                    # nothing after the stop
    assert router.calls["system-ops"] == 1


def test_wall_clock_cap_ends_the_task(monkeypatch, _zz_skills):
    setting(monkeypatch, agents__delegate_timeout_seconds=0)
    agent, router = orchestrate(
        [DELEGATE_WIPE, say("late")], [say(REPORT_DONE)], confirmer=lambda *a: True,
    )
    agent.ask("go")
    report = [m for m in agent.messages if m["role"] == "tool"][0]["content"]
    assert "outcome: partial" in report and "ran out of time" in report
    assert router.calls["system-ops"] == 0


def test_round_cap_is_configurable(monkeypatch, _zz_skills):
    setting(monkeypatch, agents__delegate_max_rounds=2)
    agent, router = orchestrate(
        [DELEGATE_WIPE, say("gave up")], [call("zz_wipe", target="x")],
        confirmer=lambda *a: True,
    )
    agent.ask("go")
    report = [m for m in agent.messages if m["role"] == "tool"][0]["content"]
    assert "outcome: failed" in report and router.calls["system-ops"] == 2


# -- escalation ------------------------------------------------------------------------

def test_escalation_offers_only_delegate_after_n_rounds(audit_file, monkeypatch, _zz_skills):
    setting(monkeypatch, agents__escalate_after_rounds=3)
    agent, router = orchestrate(
        [call("zz_quick"), call("zz_quick"), call("zz_quick"), call("zz_quick"), say("done")],
        [], confirmer=lambda *a: True,
    )
    turn = agent.ask("do many things")
    seen = router.seen["orchestrator"]
    assert "zz_quick" in {t["name"] for t in seen[2]["tools"]}            # round 3: normal
    assert [t["name"] for t in seen[3]["tools"]] == ["delegate"]          # round 4: delegate only
    assert "only tool is delegate" in seen[3]["messages"][-1]["content"]
    # The model named another tool anyway: refused in dispatch, not just hidden.
    tool_msgs = [m["content"] for m in agent.messages if m["role"] == "tool"]
    assert "not available to this agent" in tool_msgs[3]
    assert _zz_skills["quick"] == 3
    assert [r for r in records(audit_file) if r["event"] == "agent.escalated"]
    assert turn.reply == "done"                                            # a reply still ends it


def test_escalation_can_be_answered_with_text(monkeypatch, _zz_skills):
    setting(monkeypatch, agents__escalate_after_rounds=1)
    agent, router = orchestrate([call("zz_quick"), say("here is what I have")], [])
    assert agent.ask("x").reply == "here is what I have"


def test_escalation_does_nothing_without_a_subagent(monkeypatch, _zz_skills):
    import kravein.agents as agents_pkg

    setting(monkeypatch, agents__escalate_after_rounds=1)
    monkeypatch.setattr(agents_pkg, "list_profiles", lambda **k: ["orchestrator"])
    agent, router = orchestrate([call("zz_quick"), call("zz_quick"), say("ok")], [])
    agent.ask("x")
    assert "zz_quick" in {t["name"] for t in router.seen["orchestrator"][2]["tools"]}


def test_no_escalation_for_agents_without_delegate(monkeypatch, _zz_skills):
    setting(monkeypatch, agents__escalate_after_rounds=1)
    agent, stub = _agent_with([call("zz_quick"), call("zz_quick"), say("ok")])
    agent.ask("x")
    assert stub.seen_tools is not None and len(stub.seen_tools) > 1


# -- the switch ---------------------------------------------------------------------------

def test_switch_off_is_the_old_agent(monkeypatch):
    setting(monkeypatch, agents__orchestrator=False)
    a = agent_mod.make_agent(source="cli-ask")
    assert a.profile is None and a._allowed is None
    assert a._tools_for_turn("hi") == registry.tool_declarations()
    assert "delegate" not in {d["name"] for d in a._tools_for_turn("hi")}


def test_switch_on_uses_the_orchestrator(monkeypatch):
    setting(monkeypatch, agents__orchestrator=True)
    a = agent_mod.make_agent(source="web", thread_id="t")
    assert a.profile.name == "orchestrator" and "delegate" in a._allowed and a.thread_id == "t"


def test_default_setting_is_off():
    assert config.DEFAULT_SETTINGS["agents"]["orchestrator"] is False


def test_broken_orchestrator_profile_falls_back_and_is_audited(monkeypatch, audit_file):
    setting(monkeypatch, agents__orchestrator=True)
    monkeypatch.setattr(agent_mod, "ORCHESTRATOR", "does-not-exist")
    a = agent_mod.make_agent(source="cli")
    assert a.profile is None
    assert [r for r in records(audit_file) if r["event"] == "agent.orchestrator_unavailable"]


# -- audit linkage and UI frames ----------------------------------------------------------

def test_parent_and_child_turns_are_linked_in_the_audit(audit_file, _zz_skills):
    agent, _ = orchestrate(
        [DELEGATE_WIPE, say("ok")], [call("zz_wipe", target="x"), say(REPORT_DONE)],
        confirmer=lambda *a: True,
    )
    agent.ask("wipe x")
    recs = records(audit_file)
    start = [r for r in recs if r["event"] == "agent.delegate_start"][0]
    did = start["delegation"]
    assert start["agent"] == "system-ops"
    child = [r for r in recs if r.get("delegation") == did]
    events = {r["event"] for r in child}
    assert {"agent.request", "skill.start", "safety.confirm_requested",
            "safety.confirm_result", "agent.delegate_end"} <= events
    assert [r for r in child if r["event"] == "skill.start"][0]["skill"] == "zz_wipe"
    parent_request = [r for r in recs if r["event"] == "agent.request" and "delegation" not in r]
    assert parent_request and parent_request[0]["text"] == "wipe x"
    end = [r for r in recs if r["event"] == "agent.delegate_end"][0]
    assert end["outcome"] == "done"
    # The context is gone once the delegation is over.
    audit.record("after.delegate")
    assert "delegation" not in records(audit_file)[-1]


def test_subagent_activity_reaches_the_observer_labelled(_zz_skills):
    frames = []
    agent, _ = orchestrate(
        [DELEGATE_WIPE, say("ok")], [call("zz_wipe", target="x"), say(REPORT_DONE)],
        confirmer=lambda *a: True,
    )
    agent.observer = lambda kind, text, data: frames.append((kind, text, data))
    agent.ask("wipe x")
    child = [f for f in frames if f[2].get("agent") == "system-ops"]
    assert {f[0] for f in child} == {"tool", "tool_result"}
    assert all(f[1].startswith("[system-ops]") for f in child)
    assert not [f for f in frames if f[0] == "answer" and f[2].get("agent")]
    parent_ids = {f[2]["call_id"] for f in frames if f[0] == "tool" and not f[2].get("agent")}
    assert not parent_ids & {f[2]["call_id"] for f in child}   # no id collision in the UI


# -- profile model ----------------------------------------------------------------------------

class _Named:
    def __init__(self, name, fail=False):
        self.name, self.fail, self.models = name, fail, []

    def available(self):
        return True

    def complete(self, messages, *, system="", tools=None, temperature=0.4, model=""):
        self.models.append(model)
        if self.fail:
            raise RuntimeError("down")
        return llm.Reply(text=self.name, provider=self.name)


def test_preferred_provider_goes_first_with_its_model_and_failover_survives(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    facade = llm.LLM()
    zen, cline = _Named("zen"), _Named("cline", fail=True)
    monkeypatch.setattr(facade, "_ordered", lambda: [zen, cline])
    assert facade.complete([{"role": "user", "content": "x"}], prefer="cline:big-model").text == "zen"
    assert cline.models == ["big-model"] and zen.models == [""]          # failed over; own model
    cline.fail = False
    assert facade.complete([{"role": "user", "content": "x"}], prefer="cline:big-model").text == "cline"
    assert facade.complete([{"role": "user", "content": "x"}], prefer="ghost:m").text == "zen"


def test_agent_passes_its_profile_model_to_the_facade_and_rejects_unknown_providers(monkeypatch):
    ok = agent_mod.AgentProfile(name="m", purpose="p", tools=("memory",), model="zen:some-model")
    agent, stub = _agent_with([say("x")], profile=ok)
    seen = {}
    # `client()` is a shared singleton: patch it only for this test.
    monkeypatch.setattr(agent._llm, "complete", lambda messages, **kw: seen.update(kw) or say("x"))
    agent.ask("hi")
    assert seen["prefer"] == "zen:some-model"
    bad = agent_mod.AgentProfile(name="m", purpose="p", tools=("memory",), model="nope:some-model")
    with pytest.raises(ProfileError):
        _agent_with([], profile=bad)
