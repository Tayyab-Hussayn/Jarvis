"""Tests: proposals queue, approvals, daily review (#143)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import audit, config, jobs, llm, registry, safety  # noqa: E402
from kravein.agents import proposals as pr  # noqa: E402
from kravein.registry import skill  # noqa: E402
from kravein.safety import Danger, Verdict  # noqa: E402
from conftest import _agent_with as _mk  # noqa: E402


def _agent_with(script, source="test", **kw):
    agent, stub = _mk(script, **kw)
    agent.source = source          # the shared helper pins source="test"
    return agent, stub

TOKEN = "test-token-value"
WS_HEADERS = {"Host": "127.0.0.1:8787"}
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def say(text):
    return llm.Reply(text=text, provider="stub")


def call(name, **args):
    return llm.Reply(tool_calls=[llm.ToolCall(name, args, f"c-{name}")], provider="stub")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "AUDIT_LOG", tmp_path / "audit.jsonl")
    monkeypatch.setattr(audit, "_fd", None)
    monkeypatch.setattr(jobs, "STORE", tmp_path / "jobs.json")
    monkeypatch.setattr(jobs, "LOCK", tmp_path / "jobs.lock")
    monkeypatch.setattr(jobs, "HISTORY", tmp_path / "job-runs.jsonl")
    monkeypatch.setattr(pr, "in_server", False)
    # Nothing here may reach a real server.
    from kravein import delivery
    monkeypatch.setattr(delivery, "push", lambda *a, **k: False)
    yield
    monkeypatch.setattr(audit, "_fd", None)


@pytest.fixture
def wipes():
    done: list[str] = []

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
        done.append(target)
        return "wiped " + target

    yield done
    registry._registry.pop("zz_wipe", None)


def make(title="Clean the cache", **kw):
    args = dict(title=title, reasons=["it is big"], kind="delegate",
                agent="system-ops", task="clean ~/.cache", risk="major", created_by="orchestrator")
    args.update(kw)
    return pr.create(**args)


def audit_events():
    path = config.AUDIT_LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# -- store and expiry --------------------------------------------------------

def test_create_get_list_roundtrip():
    p = make()
    assert p["status"] == "pending" and p["reasons"] == ["it is big"]
    assert pr.get(p["id"])["title"] == "Clean the cache"
    assert [x["id"] for x in pr.listing("pending")] == [p["id"]]
    assert pr.listing("approved") == []
    assert "decided_at" not in p


@pytest.mark.parametrize("bad", [
    dict(title=""), dict(reasons=[]), dict(kind="run"), dict(risk="huge"),
    dict(agent="nobody"), dict(task=""), dict(agent="orchestrator"),
])
def test_create_validates(bad):
    with pytest.raises(ValueError):
        make(**bad)


def test_note_needs_no_agent_and_duplicates_and_cap_are_refused(monkeypatch):
    note = make("Reboot soon", kind="note", agent="", task="", risk="low")
    assert "agent" not in note and note["kind"] == "note"
    with pytest.raises(ValueError):
        make("reboot SOON", kind="note", agent="", task="")
    monkeypatch.setitem(config.DEFAULT_SETTINGS["agents"], "proposal_max_pending", 1)
    config._settings_cache["agents"]["proposal_max_pending"] = 1
    with pytest.raises(ValueError):
        make("Another one")


def test_pending_and_unstarted_approved_expire_but_running_does_not():
    a, b, c = make("a"), make("b"), make("c")
    pr.decide(b["id"], "approve", source="web")
    pr.decide(c["id"], "approve", source="web")
    assert pr.claim(c["id"]) is not None
    later = time.time() + 8 * 86400
    assert pr.sweep_expired(now=later) == 2
    assert pr.get(a["id"])["status"] == "expired"
    assert pr.get(b["id"])["status"] == "expired"
    assert pr.get(c["id"])["status"] == "running"
    assert pr.sweep_expired(now=later) == 0


def test_ttl_is_a_setting(monkeypatch):
    config._settings_cache["agents"]["proposal_ttl_days"] = 1
    p = make()
    assert pr.sweep_expired(now=time.time() + 3600) == 0
    assert pr.sweep_expired(now=time.time() + 2 * 86400) == 1
    assert pr.get(p["id"])["status"] == "expired"


def test_state_machine_refuses_other_transitions():
    p = make()
    assert not pr._transition(p["id"], ("approved",), "running")
    assert pr.claim(p["id"]) is None            # not approved yet
    pr.finish(p["id"], True, "x")               # not running
    assert pr.get(p["id"])["status"] == "pending"
    pr.decide(p["id"], "decline", source="web")
    assert pr.decide(p["id"], "approve", source="web")[1] is False
    assert pr.get(p["id"])["status"] == "declined"


def test_orphaned_running_rows_fail_at_start():
    p = make()
    pr.decide(p["id"], "approve", source="web")
    pr.claim(p["id"])
    assert pr.fail_orphans() == 1
    assert pr.get(p["id"])["status"] == "failed"


# -- who may decide ----------------------------------------------------------

@pytest.mark.parametrize("source", [
    "schedule:j1", "whatsapp", "daemon", "local", "", "phone-command", "web-evil",
])
def test_unattended_sources_cannot_decide(source):
    p = make()
    with pytest.raises(pr.NotAllowed):
        pr.decide(p["id"], "approve", source=source)
    assert pr.get(p["id"])["status"] == "pending"
    assert any(r["event"] == "proposal.decide_refused" for r in audit_events())


@pytest.mark.parametrize("source", sorted(pr.OWNER_SOURCES))
def test_live_owner_sources_can_decide(source):
    p = make(f"t-{source}")
    got, changed = pr.decide(p["id"], "decline", source=source)
    assert changed and got["status"] == "declined" and got["decided_by"] == source


def test_decide_is_idempotent_and_note_approval_just_closes():
    p = make()
    first, changed1 = pr.decide(p["id"], "approve", source="web", by="web")
    again, changed2 = pr.decide(p["id"], "approve", source="web", by="web")
    assert changed1 and not changed2 and again["decided_at"] == first["decided_at"]
    note = make("advice", kind="note", agent="", task="")
    got, _ = pr.decide(note["id"], "approve", source="web")
    assert got["status"] == "done"
    with pytest.raises(KeyError):
        pr.decide("pnope", "approve", source="web")
    with pytest.raises(ValueError):
        pr.decide(p["id"], "maybe", source="web")


# -- skills ------------------------------------------------------------------

def test_propose_is_refused_for_everyone_but_the_orchestrator():
    assert "only available to the Orchestrator" in pr.propose("x", ["y"], _context=None)
    agent, _ = _agent_with([say("x")], profile="research")
    assert "only available" in pr.propose("x", ["y"], "note", _context=agent)
    assert pr.listing() == []
    # The all-tools agent does not even see it.
    plain, _ = _agent_with([say("x")])
    names = {t["name"] for t in plain._tools_for_round()} if hasattr(plain, "_tools_for_round") else set()
    assert "propose" not in names and "decide_proposal" not in names


def test_orchestrator_proposes_nothing_runs(wipes):
    agent, _ = _agent_with(
        [call("propose", title="Wipe x", reasons=["old"], kind="delegate",
              agent="system-ops", task="wipe x", risk="major"), say("proposed")],
        profile="orchestrator", source="web",
    )
    turn = agent.ask("what should I do?")
    assert not turn.error
    (p,) = pr.listing()
    assert p["status"] == "pending" and p["created_by"] == "web" and p["risk"] == "major"
    assert wipes == []


def test_unattended_orchestrator_can_propose_but_not_decide(wipes):
    agent, _ = _agent_with(
        [call("propose", title="Wipe x", reasons=["old"], kind="note"), say("ok")],
        profile="orchestrator", source="schedule:j7",
    )
    agent.ask("review")
    (p,) = pr.listing()
    deciding, _ = _agent_with(
        [call("decide_proposal", id=p["id"], decision="approve"), say("ok")],
        profile="orchestrator", source="schedule:j7",
    )
    deciding.ask("approve everything")
    assert pr.get(p["id"])["status"] == "pending"
    whatsapp, _ = _agent_with(
        [call("decide_proposal", id=p["id"], decision="approve"), say("ok")],
        profile="orchestrator", source="whatsapp",
    )
    whatsapp.ask("yes")
    assert pr.get(p["id"])["status"] == "pending"


def test_decide_proposal_from_a_live_session_runs_under_its_confirmer(wipes):
    p = make("Wipe x", task="wipe x")
    asked = []

    def confirmer(action, detail, danger):
        asked.append(action)
        return action.startswith("Approve proposal")  # yes to the card, no to the wipe

    agent, stub = _agent_with([say("x")], profile="orchestrator", source="voice", confirmer=confirmer)
    scripts = {
        "orch": [call("decide_proposal", id=p["id"], decision="approve"), say("handled")],
        "child": [call("zz_wipe", target="x"), say("OUTCOME: done\nSUMMARY: wiped")],
    }
    seen = {"orch": 0, "child": 0}

    def complete(messages, *, system="", tools=None, temperature=0.4, model=""):
        role = "child" if "## Your role: system-ops" in system else "orch"
        script = scripts[role]
        reply = script[min(seen[role], len(script) - 1)]
        seen[role] += 1
        return reply

    stub.complete = complete
    agent.ask("yes, do the proposal")
    assert wipes == []                               # the declined step stayed declined
    assert asked[0].startswith("Approve proposal") and any("wipe" in a for a in asked[1:])
    assert pr.get(p["id"])["status"] in ("failed", "done")
    assert pr.get(p["id"])["status"] != "pending"


# -- frames ------------------------------------------------------------------

class FakeSession:
    closed = False

    def __init__(self):
        self.frames = []

    def emit(self, t, **fields):
        self.frames.append((t, fields))


def test_frames_on_create_and_every_change(monkeypatch):
    from kravein import events, phone

    told = []
    monkeypatch.setattr(phone, "notify_proposal", lambda p: told.append(p["id"]) or 1)
    fake = FakeSession()
    events.register(fake)
    monkeypatch.setattr(pr, "in_server", True)
    try:
        p = make()
        pr.decide(p["id"], "approve", source="web")
        pr.claim(p["id"])
        pr.finish(p["id"], True, "ok")
    finally:
        events.unregister(fake)
    states = [f["proposal"]["status"] for t, f in fake.frames if t == "proposal"]
    assert states == ["pending", "approved", "running", "done"]
    assert fake.frames[0][1]["proposal"]["id"] == p["id"]
    assert told == [p["id"]]                         # phones hear about pending only


def test_outside_the_server_the_server_is_asked(monkeypatch):
    from kravein import delivery

    posted = []
    monkeypatch.setattr(delivery, "push", lambda path, payload, **k: posted.append((path, payload)))
    p = make()
    assert posted == [("/api/proposal-event", {"id": p["id"]})]


# -- REST and the live session ----------------------------------------------

@pytest.fixture
def web(monkeypatch):
    from fastapi.testclient import TestClient

    from kravein.server import create_app

    config._settings_cache["agents"]["orchestrator"] = True
    with TestClient(create_app(TOKEN), base_url="http://127.0.0.1:8787") as c:
        yield c


def test_rest_requires_a_token(web):
    assert web.get("/api/proposals").status_code == 401
    p = make()
    assert web.post(f"/api/proposals/{p['id']}/decide", json={"decision": "approve"}).status_code == 401
    assert pr.get(p["id"])["status"] == "pending"


def test_rest_list_decide_and_idempotency(web):
    note = make("advice", kind="note", agent="", task="")
    decl = make("decline me")
    got = web.get("/api/proposals?status=pending", headers=AUTH).json()["proposals"]
    assert {x["id"] for x in got} == {note["id"], decl["id"]}
    r = web.post(f"/api/proposals/{decl['id']}/decide", headers=AUTH, json={"decision": "decline"})
    assert r.status_code == 200 and r.json()["proposal"]["status"] == "declined"
    assert r.json()["proposal"]["decided_by"] == "web"
    again = web.post(f"/api/proposals/{decl['id']}/decide", headers=AUTH, json={"decision": "approve"})
    assert again.json()["proposal"]["status"] == "declined"          # never flipped
    r = web.post(f"/api/proposals/{note['id']}/decide", headers=AUTH, json={"decision": "approve"})
    assert r.json()["proposal"]["status"] == "done"
    assert web.post("/api/proposals/pnope/decide", headers=AUTH, json={"decision": "approve"}).status_code == 404
    assert web.post(f"/api/proposals/{decl['id']}/decide", headers=AUTH, json={"decision": "x"}).status_code == 400


def test_proposal_event_endpoint_broadcasts(web):
    p = make()
    with web.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        assert json.loads(ws.receive_text())["t"] == "hello"
        assert web.post("/api/proposal-event", headers=AUTH, json={"id": p["id"]}).json() == {"delivered": True}
        frame = json.loads(ws.receive_text())
    assert frame["t"] == "proposal" and frame["proposal"]["id"] == p["id"]


def _router(monkeypatch, scripts):
    seen = {"orch": 0, "child": 0}

    class Stub:
        name = "cline"

        def available(self):
            return True

        def complete(self, messages, *, system="", tools=None, **kw):
            role = "child" if "## Your role: system-ops" in system else "orch"
            script = scripts[role]
            reply = script[min(seen[role], len(script) - 1)]
            seen[role] += 1
            return reply

    facade = llm.LLM()
    facade.cline = Stub()
    facade.gemini = Stub()
    facade.gemini.name = "gemini"
    facade.openrouter = Stub()
    facade.openrouter.name = "openrouter"
    monkeypatch.setattr(llm, "_client", facade)
    monkeypatch.setattr(llm, "client", lambda: facade)
    return seen


def test_approve_runs_in_the_live_session_and_a_declined_step_stays_declined(web, wipes, monkeypatch):
    p = make("Wipe x", task="wipe x")
    seen = _router(monkeypatch, {
        "orch": [call("delegate", agent="system-ops", task="wipe x"), say("done")],
        "child": [call("zz_wipe", target="x"), say("OUTCOME: done\nSUMMARY: wiped")],
    })
    with web.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        assert json.loads(ws.receive_text())["t"] == "hello"
        r = web.post(f"/api/proposals/{p['id']}/decide", headers=AUTH, json={"decision": "approve"})
        assert r.json()["proposal"]["status"] in ("approved", "running")
        confirm = None
        frames = []
        for _ in range(30):
            frame = json.loads(ws.receive_text())
            frames.append(frame["t"])
            if frame["t"] == "confirm_request":
                confirm = frame
                break
        assert confirm is not None, frames
        assert confirm["action"].startswith("system-ops: ")     # asked in THIS session
        ws.send_text(json.dumps({"t": "confirm_reply", "confirm_id": confirm["confirm_id"], "approve": False}))
        for _ in range(30):
            if json.loads(ws.receive_text())["t"] == "turn_end":
                break
    assert wipes == []
    deadline = time.time() + 5
    while pr.get(p["id"])["status"] == "running" and time.time() < deadline:
        time.sleep(0.05)
    assert pr.get(p["id"])["status"] in ("done", "failed")
    assert seen["orch"] >= 1


def test_no_live_session_stays_approved_and_runs_when_one_opens(web, wipes, monkeypatch):
    p = make("Wipe x", task="wipe x")
    r = web.post(f"/api/proposals/{p['id']}/decide", headers=AUTH, json={"decision": "approve"})
    assert r.json()["proposal"]["status"] == "approved"             # nobody to ask
    assert wipes == []
    _router(monkeypatch, {
        "orch": [say("nothing to do")], "child": [say("OUTCOME: done\nSUMMARY: ok")],
    })
    with web.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        assert json.loads(ws.receive_text())["t"] == "hello"
        for _ in range(30):
            if json.loads(ws.receive_text())["t"] == "turn_end":
                break
    deadline = time.time() + 5
    while pr.get(p["id"])["status"] in ("approved", "running") and time.time() < deadline:
        time.sleep(0.05)
    assert pr.get(p["id"])["status"] == "done"


def test_a_busy_or_unrelated_session_leaves_it_approved(web, monkeypatch):
    p = make()
    with web.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        assert json.loads(ws.receive_text())["t"] == "hello"
        held = list(web.app.state.sessions.items())[0]
        held.busy.set()
        r = web.post(f"/api/proposals/{p['id']}/decide", headers=AUTH,
                     json={"decision": "approve", "session_id": held.id})
        assert r.json()["proposal"]["status"] == "approved"
        held.busy.clear()
        r = web.post(f"/api/proposals/{p['id']}/decide", headers=AUTH,
                     json={"decision": "approve", "session_id": "someone-elses"})
        assert r.json()["proposal"]["status"] == "approved"


# -- daily review ------------------------------------------------------------

def test_review_job_is_created_retimed_and_removed_by_the_setting():
    assert pr.ensure_daily_review() == "off"
    config._settings_cache["agents"]["daily_review"] = True
    assert pr.ensure_daily_review() == "created"
    (job,) = [j for j in jobs.all_jobs() if j["kind"] == "review"]
    assert job["cron"] == "0 9 * * *"
    assert pr.ensure_daily_review() == "unchanged"
    config._settings_cache["agents"]["daily_review_time"] = "07:30"
    assert pr.ensure_daily_review() == "retimed"
    assert [j["cron"] for j in jobs.all_jobs() if j["kind"] == "review"] == ["30 7 * * *"]
    config._settings_cache["agents"]["daily_review"] = False
    assert pr.ensure_daily_review() == "removed"
    assert not [j for j in jobs.all_jobs() if j["kind"] == "review"]


def test_review_job_cannot_be_made_or_changed_through_the_public_api():
    with pytest.raises(ValueError):
        jobs.add(prompt="rm -rf", cron="0 9 * * *", kind="review")
    config._settings_cache["agents"]["daily_review"] = True
    pr.ensure_daily_review()
    (job,) = [j for j in jobs.all_jobs() if j["kind"] == "review"]
    with pytest.raises(ValueError):
        jobs.edit(job["id"], prompt="do anything")
    with pytest.raises(ValueError):
        jobs.edit(job["id"], kind="agent")
    with pytest.raises(ValueError):
        a = jobs.add(prompt="p", cron="0 9 * * *", kind="agent")
        jobs.edit(a["id"], kind="review")
    assert jobs.cancel() >= 0
    assert [j for j in jobs.all_jobs() if j["kind"] == "review"]     # cancel-all skips it
    assert jobs.cancel(job["id"]) == 0


def test_daily_review_produces_proposals_only_and_never_a_state_changing_tool(wipes):
    stub_agent, stub = _agent_with([say("x")], profile="research")
    seen_tools = []

    def complete(messages, *, system="", tools=None, **kw):
        seen_tools.append(sorted(t["name"] for t in (tools or [])))
        n = len(seen_tools)
        if n == 1:
            return llm.Reply(tool_calls=[
                llm.ToolCall("zz_wipe", {"target": "home"}, "a"),
                llm.ToolCall("lock_screen", {}, "b"),
                llm.ToolCall("propose", {"title": "Low disk", "reasons": ["92% full"],
                                         "kind": "delegate", "agent": "system-ops",
                                         "task": "clean caches", "risk": "major"}, "c"),
            ], provider="stub")
        return say("one proposal")

    stub.complete = complete
    ok, text, err = pr.run_daily_review({"id": "j9"})
    assert ok and not err
    assert wipes == []
    (p,) = pr.listing()
    assert p["status"] == "pending" and p["created_by"] == "schedule:j9"
    assert "1 proposal" in text
    offered = set(seen_tools[0])
    assert "propose" in offered and "delegate" not in offered and "decide_proposal" not in offered
    assert not offered & {"zz_wipe", "lock_screen", "run_command", "delete_path", "shutdown_system"}
    refused = [r for r in audit_events() if r["event"] == "skill.profile_refused"]
    assert {r.get("skill") or r.get("name") for r in refused} >= {"zz_wipe", "lock_screen"}


def test_run_job_dispatches_review_without_loading_the_agent_for_other_kinds(monkeypatch):
    called = []
    monkeypatch.setattr(pr, "run_daily_review", lambda job: called.append(job["id"]) or (True, "r", ""))
    assert jobs.run_job({"id": "j1", "kind": "review", "prompt": "ignored"}) == (True, "r", "")
    assert called == ["j1"]


def test_review_tools_exist_and_are_read_only():
    allowed = registry.resolve_tools(pr.REVIEW_TOOLS)
    for name in allowed - {"propose"}:
        assert registry.all_skills()[name].danger == Danger.SAFE, name


# -- self-review findings ------------------------------------------------------

def _script(stub, orch, child=None):
    n = {"orch": 0, "child": 0}
    scripts = {"orch": orch, "child": child or [say("OUTCOME: done\nSUMMARY: ok")]}

    def complete(messages, *, system="", tools=None, **kw):
        role = "child" if "## Your role: system-ops" in system else "orch"
        script = scripts[role]
        reply = script[min(n[role], len(script) - 1)]
        n[role] += 1
        return reply(messages) if callable(reply) else reply

    stub.complete = complete


def test_the_model_cannot_approve_its_own_proposal_in_the_same_request(wipes):
    agent, stub = _agent_with([say("x")], profile="orchestrator", source="web")
    _script(stub, [
        call("propose", title="Wipe x", reasons=["old"], agent="system-ops",
             task="wipe x", risk="major"),
        lambda m: call("decide_proposal", id=pr.listing()[0]["id"], decision="approve"),
        say("done"),
    ])
    agent.ask("suggest something and do it")
    (p,) = pr.listing()
    assert p["status"] == "pending"
    assert any(r["event"] == "proposal.self_approval_refused" for r in audit_events())


class Cards:
    """A confirmer that records every card and answers yes or no."""

    def __init__(self, answer=True):
        self.answer, self.cards = answer, []

    def __call__(self, action, detail, danger):
        self.cards.append((action, danger))
        return self.answer


def test_a_later_request_from_the_owner_may_decide_it_on_a_card(wipes):
    p = make("Wipe x", task="wipe x")
    cards = Cards(True)
    agent, stub = _agent_with([say("x")], profile="orchestrator", source="web", confirmer=cards)
    _script(stub, [call("decide_proposal", id=p["id"], decision="approve"), say("ok")])
    agent.ask("yes, approve it")
    assert pr.get(p["id"])["status"] == "done"
    (action, danger), *_ = cards.cards
    assert action.startswith(f"Approve proposal #{p['id']}: Wipe x") and "system-ops" in action
    assert int(danger) == int(Danger.CRITICAL) or str(danger).lower().startswith("crit")


def test_approve_through_the_tool_denied_leaves_it_pending(wipes):
    p = make("Wipe x", task="wipe x")
    cards = Cards(False)
    agent, stub = _agent_with([say("x")], profile="orchestrator", source="web", confirmer=cards)
    _script(stub, [call("decide_proposal", id=p["id"], decision="approve"), say("ok")])
    agent.ask("ok")                                    # any owner message, no approval on the card
    assert len(cards.cards) == 1
    assert pr.get(p["id"])["status"] == "pending" and wipes == []


def test_auto_mode_still_asks_for_a_proposal_approval(wipes, monkeypatch):
    p = make("Wipe x", task="wipe x")
    safety.set_confirmation_mode("auto", seconds=600, by="web")
    try:
        cards = Cards(False)
        agent, stub = _agent_with([say("x")], profile="orchestrator", source="web", confirmer=cards)
        _script(stub, [call("decide_proposal", id=p["id"], decision="approve"), say("ok")])
        agent.ask("hello")
        assert len(cards.cards) == 1                   # CRITICAL is never auto-answered
        assert pr.get(p["id"])["status"] == "pending"
    finally:
        safety.set_confirmation_mode("ask", by="test")


def test_approve_through_the_tool_without_a_confirmer_is_refused(wipes):
    p = make("Wipe x", task="wipe x")
    agent, stub = _agent_with([say("x")], profile="orchestrator", source="web", confirmer=None)
    _script(stub, [call("decide_proposal", id=p["id"], decision="approve"), say("ok")])
    agent.ask("hello")
    assert pr.get(p["id"])["status"] == "pending"


def test_decline_through_the_tool_needs_no_card(wipes):
    p = make("Wipe x", task="wipe x")
    cards = Cards(True)
    agent, stub = _agent_with([say("x")], profile="orchestrator", source="web", confirmer=cards)
    _script(stub, [call("decide_proposal", id=p["id"], decision="decline"), say("ok")])
    agent.ask("no, skip that")
    assert cards.cards == [] and pr.get(p["id"])["status"] == "declined"


def test_voice_approval_of_an_already_approved_proposal_still_asks_then_starts_it(wipes):
    p = make("Wipe x", task="wipe x")
    pr.decide(p["id"], "approve", source="web")        # approved earlier, nobody was there
    cards = Cards(True)
    agent, stub = _agent_with([say("x")], profile="orchestrator", source="voice", confirmer=cards)
    _script(stub, [call("decide_proposal", id=p["id"], decision="approve"), say("ok")])
    agent.ask("do it now")
    assert len(cards.cards) == 1 and pr.get(p["id"])["status"] == "done"


def test_manage_job_cannot_pause_the_review_job():
    config._settings_cache["agents"]["daily_review"] = True
    pr.ensure_daily_review()
    (job,) = [j for j in jobs.all_jobs() if j["kind"] == "review"]
    assert jobs.set_paused(job["id"], True) == ""
    assert [j["state"] for j in jobs.all_jobs() if j["kind"] == "review"] == ["pending"]


def test_rest_skips_a_busy_session_and_picks_an_idle_one(web, monkeypatch):
    p = make()
    _router(monkeypatch, {"orch": [say("ok")], "child": [say("OUTCOME: done\nSUMMARY: ok")]})
    with web.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as a:
        a.receive_text()
        with web.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as b:
            b.receive_text()
            sessions = list(web.app.state.sessions.items())
            sessions[-1].busy.set()                      # the newest one is busy
            r = web.post(f"/api/proposals/{p['id']}/decide", headers=AUTH,
                         json={"decision": "approve"})
            assert r.json()["proposal"]["status"] in ("running", "done")
            sessions[-1].busy.clear()


def test_a_turn_started_for_an_approval_cannot_decide_another_proposal(wipes):
    other = make("Wipe y", task="wipe y")
    agent, stub = _agent_with([say("x")], profile="orchestrator", source="web")
    _script(stub, [call("decide_proposal", id=other["id"], decision="approve"), say("ok")])
    agent.ask(f"{pr.APPROVED_PREFIX}zzz: wipe x")
    assert pr.get(other["id"])["status"] == "pending"
    assert any(r["event"] == "proposal.decide_in_approved_turn_refused" for r in audit_events())


def test_an_approval_made_while_the_session_is_busy_starts_when_it_goes_idle(web, monkeypatch):
    p = make()
    _router(monkeypatch, {"orch": [say("hi")], "child": [say("OUTCOME: done\nSUMMARY: ok")]})
    with web.websocket_connect(f"/ws?token={TOKEN}", headers=WS_HEADERS) as ws:
        assert json.loads(ws.receive_text())["t"] == "hello"
        held = list(web.app.state.sessions.items())[0]
        held.busy.set()
        r = web.post(f"/api/proposals/{p['id']}/decide", headers=AUTH,
                     json={"decision": "approve"})
        assert r.json()["proposal"]["status"] == "approved"
        held.busy.clear()
        ws.send_text(json.dumps({"t": "ask", "id": "t1", "text": "hello"}))
        deadline = time.time() + 8
        while pr.get(p["id"])["status"] != "done" and time.time() < deadline:
            time.sleep(0.05)
    assert pr.get(p["id"])["status"] == "done"
