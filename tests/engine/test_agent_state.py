"""Tests: /api/agents, /api/agents/state and live agent_state frames (#146)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import config, events, llm, registry, safety  # noqa: E402
from kravein.agents.state import StateTracker  # noqa: E402
from kravein.registry import skill  # noqa: E402
from kravein.safety import Danger, Verdict  # noqa: E402
from conftest import _agent_with as _mk  # noqa: E402

TOKEN = "test-token-value"
WS_HEADERS = {"Host": "127.0.0.1:8787"}
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def say(text):
    return llm.Reply(text=text, provider="stub")


def call(name, **args):
    return llm.Reply(tool_calls=[llm.ToolCall(name, args, f"c-{name}")], provider="stub")


@pytest.fixture
def tools():
    ran = []

    @skill(name="zz_ok", category="files", danger=Danger.SAFE)
    def zz_ok(_source: str = "local") -> str:
        """Do a harmless thing."""
        ran.append("ok")
        return "fine"

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
        ran.append(target)
        return "wiped"

    yield ran
    registry._registry.pop("zz_ok", None)
    registry._registry.pop("zz_wipe", None)


def tracked(script, **kw):
    """An agent whose observer feeds a tracker; returns (agent, stub, frames, tracker)."""
    frames: list[dict] = []
    tracker = StateTracker(frames.append)
    agent, stub = _mk(script, observer=lambda k, t, d: tracker.handle(k, t, d), **kw)
    return agent, stub, frames, tracker


def seq(frames, agent):
    return [f["state"] for f in frames if f["agent"] == agent]


# -- state sequences ---------------------------------------------------------

def test_a_plain_turn_with_the_switch_off_reports_the_default_agent_as_orchestrator(tools):
    assert config.get("agents.orchestrator", False) is False
    agent, _, frames, _ = tracked([call("zz_ok"), say("All done.")])
    agent.ask("do the harmless thing")
    assert {f["agent"] for f in frames} == {"orchestrator"}
    assert seq(frames, "orchestrator") == ["thinking", "working", "thinking", "done", "idle"]
    assert frames[0]["task"] == "do the harmless thing"
    working = frames[1]
    assert working["tool"] == "zz_ok" and "args" not in working
    done = frames[3]
    assert done["report"] == {"outcome": "done", "summary": "All done."}
    assert frames[4]["report"] == done["report"] and "task" not in frames[4]
    assert all(isinstance(f["since"], float) for f in frames)


def test_task_and_summary_are_cut_to_200_characters(tools):
    agent, _, frames, _ = tracked([say("y" * 500)])
    agent.ask("x" * 500)
    assert len(frames[0]["task"]) <= 200
    assert len(next(f for f in frames if f["state"] == "done")["report"]["summary"]) <= 200


def test_a_failed_turn_ends_failed_then_idle(tools):
    agent, stub, frames, _ = tracked([say("x")])

    def boom(*a, **k):
        raise llm.LLMError("provider down")

    stub.complete = boom
    agent.ask("hello")
    assert seq(frames, "orchestrator") == ["thinking", "failed", "idle"]
    assert "provider down" in next(f for f in frames if f["state"] == "failed")["report"]["summary"]


def test_a_declined_confirmation_waits_then_the_turn_ends_declined(tools):
    frames: list[dict] = []
    tracker = StateTracker(frames.append)

    def confirmer(action, detail, danger):
        tracker.waiting(True)                 # what Session.confirm does around its wait
        try:
            return False
        finally:
            tracker.waiting(False)

    agent, _ = _mk([call("zz_wipe", target="x"), say("ok")], confirmer=confirmer,
                   observer=lambda k, t, d: tracker.handle(k, t, d))
    agent.ask("wipe x")
    assert tools == []
    states = seq(frames, "orchestrator")
    assert states == ["thinking", "working", "waiting", "working", "thinking", "done", "idle"]
    waiting = next(f for f in frames if f["state"] == "waiting")
    assert waiting["tool"] == "zz_wipe"

    # Asking again for the declined target ends the turn as declined.
    frames.clear()
    agent2, _ = _mk([call("zz_wipe", target="y"), call("zz_wipe", target="y"), say("ok")],
                    confirmer=confirmer, observer=lambda k, t, d: tracker.handle(k, t, d))
    agent2.ask("wipe y")
    assert seq(frames, "orchestrator")[-2:] == ["declined", "idle"]
    assert tools == []


def test_session_confirm_reports_waiting_while_the_card_is_open():
    loop = asyncio.new_event_loop()
    try:
        session = events.Session(loop, confirm_timeout=0.2)
        seen = []
        session.on_wait = seen.append
        assert session.confirm("wipe x", "", Danger.DESTRUCTIVE) is False     # times out: deny
        assert seen == [True, False]
    finally:
        loop.close()


def test_a_delegated_turn_gives_the_sub_agent_its_own_states(tools):
    config._settings_cache["agents"]["orchestrator"] = True
    frames: list[dict] = []
    tracker = StateTracker(frames.append)
    from kravein.agent import Agent

    scripts = {
        "orch": [call("delegate", agent="system-ops", task="check the disk"), say("It is fine.")],
        "child": [call("zz_ok"), say("OUTCOME: done\nSUMMARY: disk is fine")],
    }
    seen = {"orch": 0, "child": 0}

    class Stub:
        name = "cline"

        def available(self):
            return True

        def complete(self, messages, *, system="", tools=None, **kw):
            role = "child" if "## Your role: system-ops" in system else "orch"
            reply = scripts[role][min(seen[role], len(scripts[role]) - 1)]
            seen[role] += 1
            return reply

    agent = Agent(source="web", profile="orchestrator",
                  observer=lambda k, t, d: tracker.handle(k, t, d), confirmer=lambda *a: False)
    for name in ("cline", "gemini", "openrouter"):
        s = Stub()
        s.name = name
        setattr(agent._llm, name, s)
    # The child needs a tool its profile allows; zz_ok is in the "files" category.
    agent.ask("how is the disk?")

    assert seq(frames, "orchestrator")[:3] == ["thinking", "working", "thinking"]
    assert seq(frames, "orchestrator")[-2:] == ["done", "idle"]
    child = [f for f in frames if f["agent"] == "system-ops"]
    assert [f["state"] for f in child][0] == "thinking" and child[0]["delegation"]
    assert [f["state"] for f in child][-2:] == ["done", "idle"]
    end = next(f for f in child if f["state"] == "done")
    assert end["report"]["outcome"] == "done" and "disk is fine" in end["report"]["summary"]
    # The orchestrator is "working: delegate" while the child runs.
    working = [f for f in frames if f["agent"] == "orchestrator" and f["state"] == "working"]
    assert working[0]["tool"] == "delegate"


def test_a_declined_delegation_ends_declined(tools):
    tracker_frames: list[dict] = []
    tracker = StateTracker(tracker_frames.append)
    tracker.handle("agent_state", "", {"phase": "turn_start", "agent": "system-ops",
                                       "delegation": "d1", "task": "wipe x"})
    tracker.handle("agent_state", "", {"phase": "delegate_end", "agent": "system-ops",
                                       "delegation": "d1", "outcome": "declined",
                                       "summary": "The owner declined an action."})
    assert [f["state"] for f in tracker_frames] == ["thinking", "declined", "idle"]
    assert tracker_frames[1]["report"]["outcome"] == "declined"


def test_a_subagent_turn_end_alone_is_not_the_end_of_the_delegation():
    frames: list[dict] = []
    t = StateTracker(frames.append)
    t.handle("agent_state", "", {"phase": "turn_start", "agent": "research", "delegation": "d1"})
    t.handle("agent_state", "", {"phase": "turn_end", "agent": "research", "delegation": "d1",
                                 "outcome": "done", "summary": "x"})
    assert [f["state"] for f in frames] == ["thinking"]


# -- wire frames: sub-agent fields on tool frames -----------------------------

def test_tool_frames_carry_agent_and_delegation_but_no_new_secrets():
    loop = asyncio.new_event_loop()
    try:
        session = events.Session(loop)
        sent = []
        session.emit = lambda t, **f: sent.append((t, f))
        observe = events.observer_for(session)
        data = {"call_id": "d1:c1", "name": "zz_ok", "args": {"a": 1}, "agent": "research",
                "delegation": "d1", "result": "r"}
        observe("tool", "zz_ok()", data)
        observe("tool_result", "r", data)
        observe("thinking", "hm", {"agent": "research", "delegation": "d1"})
        observe("tool", "zz_ok()", {"call_id": "c2", "name": "zz_ok", "args": {}})
        assert all(f["agent"] == "research" and f["delegation"] == "d1" for _, f in sent[:3])
        assert "agent" not in sent[3][1] and "delegation" not in sent[3][1]
    finally:
        loop.close()


# -- principal scoping --------------------------------------------------------

class Held:
    def __init__(self, principal):
        self.principal = principal
        self.got = []
        outer = self

        class S:
            def emit(self, t, **f):
                outer.got.append((t, f))

        self.session = S()


def test_frames_go_only_to_the_same_principals_sessions():
    store = events.SessionStore()
    tab1, tab2, phone = Held("browser"), Held("browser"), Held("device:ph1")
    for h in (tab1, tab2, phone):
        store.add(type("I", (), {"id": id(h), "principal": h.principal, "session": h.session})())
    store.tracker("browser").handle("tool", "", {"name": "zz_ok"})
    assert [t for t, _ in tab1.got] == ["agent_state"] and [t for t, _ in tab2.got] == ["agent_state"]
    assert phone.got == []
    assert store.tracker("device:ph1").snapshot()[0]["state"] == "idle"


# -- REST + websocket ---------------------------------------------------------

@pytest.fixture
def web():
    from fastapi.testclient import TestClient

    from kravein.server import create_app

    with TestClient(create_app(TOKEN), base_url="http://127.0.0.1:8787") as c:
        yield c


def test_agents_endpoint_requires_a_token(web):
    assert web.get("/api/agents").status_code == 401
    assert web.get("/api/agents/state").status_code == 401


def test_agents_switch_off_lists_one_default_agent(web):
    body = web.get("/api/agents", headers=AUTH).json()
    assert body["orchestrator_enabled"] is False
    (only,) = body["agents"]
    assert only["name"] == "orchestrator" and only["role"] == "orchestrator"
    assert isinstance(only["tools"], int) and only["tools"] > 0


def test_agents_switch_on_lists_profiles_without_bodies_or_tool_names(web):
    config._settings_cache["agents"]["orchestrator"] = True
    body = web.get("/api/agents", headers=AUTH).json()
    assert body["orchestrator_enabled"] is True
    names = {a["name"]: a for a in body["agents"]}
    assert names["orchestrator"]["role"] == "orchestrator"
    assert names["system-ops"]["role"] == "sub" and names["system-ops"]["purpose"]
    for a in body["agents"]:
        assert set(a) == {"name", "purpose", "model", "tools", "role"}
        assert isinstance(a["tools"], int)
    assert "## " not in json.dumps(body)         # no SKILL.md body


def test_state_snapshot_and_resume_replay(web):
    config._settings_cache["agents"]["orchestrator"] = True
    snap = web.get("/api/agents/state", headers=AUTH).json()["states"]
    assert {s["agent"] for s in snap} >= {"orchestrator", "system-ops"}
    assert all(s["state"] == "idle" for s in snap)

    with web.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        hello = json.loads(ws.receive_text())
        assert hello["t"] == "hello"
        sid = hello["session_id"]
        tracker = web.app.state.sessions.tracker("browser")
        tracker.handle("agent_state", "", {"phase": "turn_start", "agent": "orchestrator", "task": "hi"})
        tracker.handle("agent_state", "", {"phase": "turn_end", "agent": "orchestrator",
                                           "outcome": "done", "summary": "ok"})
        live = [json.loads(ws.receive_text()) for _ in range(3)]
        assert [f["state"] for f in live] == ["thinking", "done", "idle"]

    with web.websocket_connect(f"/ws?token={TOKEN}&resume={sid}", headers=WS_HEADERS) as ws:
        hello2 = json.loads(ws.receive_text())
        assert hello2["resumed"] is True
        frames = []
        while not any(f["t"] == "agent_state" for f in frames):
            frames.append(json.loads(ws.receive_text()))
        orch = next(f for f in frames if f["t"] == "agent_state")
        assert orch["agent"] == "orchestrator"
        assert orch["state"] == "idle" and orch["report"]["summary"] == "ok"
