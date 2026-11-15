"""Runtime.submit, durable runs, parked approvals, resume (#247, v3 phase 2a).

The model is scripted (``llm.client().complete`` patched); the skills are
test-only ``zz_*`` tools; every run writes to the per-test agents.db.
"""

from __future__ import annotations

import ast
import json
import time
from pathlib import Path

import pytest

from kravein import audit, jobs, llm, registry, runtime, safety
from kravein.agents import AgentProfile, proposals
from kravein.llm import Reply, ToolCall
from kravein.registry import skill
from kravein.safety import Danger, Verdict

RAN: list[tuple[str, dict]] = []
WORLD: dict = {"files": 3, "blocked": set()}
PROFILE = AgentProfile(name="unatt-test", purpose="p",
                       tools=("zz_status", "zz_send", "zz_wipe", "zz_crit", "zz_secret", "zz_blocked"))


@pytest.fixture(autouse=True)
def world(monkeypatch):
    RAN.clear()
    WORLD.update(files=3, blocked=set())
    made = []

    def reg(fn, **kw):
        skill(**kw)(fn)
        made.append(kw["name"])

    def zz_status() -> str:
        """Status."""
        RAN.append(("zz_status", {}))
        return "all fine"

    def zz_send(channel: str, text: str, _source: str = "local") -> str:
        """Send.

        Args:
            channel: Where.
            text: What.
        """
        safety.enforce(Verdict(danger=Danger.GUARDED, reasons=["sends"]), f"send to '{channel}'",
                       skill="zz_send", source=_source,
                       targets=safety.describe_targets(f"channel:{channel}"))
        RAN.append(("zz_send", {"channel": channel, "text": text}))
        return f"sent to {channel}"

    def zz_wipe(target: str, _source: str = "local") -> str:
        """Wipe.

        Args:
            target: What.
        """
        verdict = Verdict(danger=Danger.DESTRUCTIVE, reasons=["deletes"])
        if target in WORLD["blocked"]:
            verdict.blocked, verdict.block_reason = True, "protected"
        # The card names the current state of the world: it changes if the world does.
        safety.enforce(verdict, f"wipe '{target}' ({WORLD['files']} files)", skill="zz_wipe",
                       source=_source, targets=safety.describe_targets(f"wipe:{target}"))
        RAN.append(("zz_wipe", {"target": target}))
        return f"wiped {target}"

    def zz_crit(what: str, _source: str = "local") -> str:
        """Critical.

        Args:
            what: What.
        """
        safety.enforce(Verdict(danger=Danger.CRITICAL), f"critical '{what}'", skill="zz_crit",
                       source=_source, targets=safety.describe_targets(f"crit:{what}"))
        RAN.append(("zz_crit", {"what": what}))
        return "done"

    def zz_secret(path: str, _source: str = "local") -> str:
        """Secret read.

        Args:
            path: Path.
        """
        safety.enforce(Verdict(danger=Danger.GUARDED, secret_read=True), f"read secret '{path}'",
                       skill="zz_secret", source=_source)
        RAN.append(("zz_secret", {"path": path}))
        return "secret"

    def zz_blocked(path: str, _source: str = "local") -> str:
        """Always refused.

        Args:
            path: Path.
        """
        safety.enforce(Verdict(danger=Danger.DESTRUCTIVE, blocked=True, block_reason="protected"),
                       f"touch '{path}'", skill="zz_blocked", source=_source)
        RAN.append(("zz_blocked", {}))
        return "x"

    reg(zz_status, name="zz_status")
    reg(zz_send, name="zz_send", danger=Danger.GUARDED, scope=("channel",), outward=True)
    reg(zz_wipe, name="zz_wipe", danger=Danger.DESTRUCTIVE)
    reg(zz_crit, name="zz_crit", danger=Danger.CRITICAL)
    reg(zz_secret, name="zz_secret", danger=Danger.GUARDED)
    reg(zz_blocked, name="zz_blocked", danger=Danger.DESTRUCTIVE)
    yield
    for name in made:
        registry._registry.pop(name, None)


@pytest.fixture
def model(monkeypatch):
    """A scripted model: each call pops the next Reply (then 'done')."""
    script: list[Reply] = []
    calls: list[list[dict]] = []

    def complete(messages, **kw):
        calls.append(messages)
        return script.pop(0) if script else Reply(text="done")

    monkeypatch.setattr(llm.client(), "complete", complete)
    return script


def tool(name, **args):
    return Reply(tool_calls=[ToolCall(name, args, f"c-{name}")])


def unattended(text="do the job", **kw):
    return runtime.submit(runtime.Run(source="schedule:j1", text=text, attended=False,
                                      profile=PROFILE, trigger="job:j1", **kw))


def approval_for(turn):
    run = runtime.get(turn.run_id)
    return proposals.get(run["proposal"])


def approve(turn, **kw):
    p = approval_for(turn)
    proposals.decide(p["id"], "approve", source="web", by="web")
    return runtime.resume(turn.run_id, **kw)


# -- one entry point -----------------------------------------------------------------------

def test_no_agent_is_built_outside_the_runtime():
    root = Path(registry.__file__).parent
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "runtime.py" and path.parent == root:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
                if name == "Agent":
                    offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert offenders == [], f"Agent(...) outside runtime.py: {offenders}"


def test_an_attended_run_is_recorded(model):
    model.append(tool("zz_status"))
    turn = runtime.submit(runtime.Run(source="web", text="status?", principal="p1",
                                      confirmer=lambda *a: True, trigger="chat"))
    row = runtime.get(turn.run_id)
    assert turn.status == row["status"] == "done" and row["attended"] == 1
    assert row["steps"] == ["zz_status"] and row["principal"] == "p1"


def test_runtime_ask_for_a_session_agent(model):
    agent = runtime.new_agent(source="web", confirmer=lambda *a: True)
    turn = runtime.ask(agent, "hi", trigger="chat")
    assert turn.status == "done" and runtime.get(turn.run_id)["source"] == "web"


# -- unattended: the profile, parking, D4 --------------------------------------------------

def test_the_unattended_profile_is_read_mostly():
    from kravein.agents import load_profile

    tools = registry.resolve_tools(load_profile(runtime.UNATTENDED_PROFILE).tools)
    assert {"schedule_job", "manage_job", "delegate", "write_file", "delete_path"}.isdisjoint(tools)
    assert {"system_status", "send_notification", "run_command", "propose"} <= tools
    agent = runtime.unattended_agent(source="schedule:x")
    assert agent.profile.name == runtime.UNATTENDED_PROFILE and agent.confirmer is None


def test_a_card_parks_the_run_and_nothing_runs(model):
    model.append(tool("zz_send", channel="general", text="hello"))
    model.append(tool("zz_status"))                    # must never be reached
    turn = unattended()
    assert turn.status == "waiting_approval" and RAN == []
    row = runtime.get(turn.run_id)
    parked = row["parked"]
    assert parked["skill"] == "zz_send" and json.loads(parked["args"]) == {"channel": "general",
                                                                          "text": "hello"}
    assert parked["action"] == "send to 'general'" and parked["danger"] == "GUARDED"
    assert parked["requested_at"] > 0 and parked["idem_key"].startswith(turn.run_id)
    p = approval_for(turn)
    assert p["kind"] == "approval" and p["status"] == "pending" and p["task"] == turn.run_id
    assert "send to 'general'" in p["title"]


def test_a_grant_runs_unattended_without_parking(model):
    # The owner's Always on a GUARDED card, in a live session.
    model.append(tool("zz_send", channel="general", text="1"))
    agent = runtime.new_agent(source="web", profile=PROFILE, confirmer=lambda *a: safety.ALWAYS)
    agent.principal = "p1"
    runtime.ask(agent, "send", trigger="chat")
    RAN.clear()
    model.append(tool("zz_send", channel="general", text="2"))
    turn = unattended(grant_principal="p1")
    assert turn.status == "done" and RAN == [("zz_send", {"channel": "general", "text": "2"})]


def test_a_hard_refusal_is_never_parked(model):
    model.append(tool("zz_blocked", path="/etc/x"))
    turn = unattended()
    assert turn.status == "done" and runtime.get(turn.run_id)["parked"] is None and RAN == []


def test_a_full_queue_fails_the_run_honestly(model, monkeypatch):
    monkeypatch.setattr(proposals, "create",
                        lambda **kw: (_ for _ in ()).throw(ValueError("too many proposals")))
    model.append(tool("zz_send", channel="general", text="hi"))
    turn = unattended()
    assert turn.status == "failed" and "could not ask" in runtime.get(turn.run_id)["error"]


def test_the_parker_never_approves():
    parker = runtime.Parker()
    assert parker("x", "y", Danger.GUARDED) is False and parker.attended() is False


# -- resume: exactly once, re-validated ----------------------------------------------------

def test_approve_resumes_once_under_the_owner(model):
    model.append(tool("zz_send", channel="general", text="hello"))
    turn = unattended()
    model.append(Reply(text="sent it"))
    resumed = approve(turn)
    assert RAN == [("zz_send", {"channel": "general", "text": "hello"})]
    assert resumed.status == "done" and runtime.get(turn.run_id)["status"] == "done"
    assert approval_for(turn)["status"] == "done"
    # A second approval (double click, a second session, a retry): nothing runs.
    assert runtime.resume(turn.run_id) is None and len(RAN) == 1


def test_a_restart_between_park_and_approve_changes_nothing(model, monkeypatch):
    model.append(tool("zz_wipe", target="tmp"))
    turn = unattended()
    # A new process: in-memory state gone, orphans swept.
    monkeypatch.setattr(runtime, "_started", False)
    runtime.start()
    assert runtime.get(turn.run_id)["status"] == "waiting_approval"
    approve(turn)
    assert RAN == [("zz_wipe", {"target": "tmp"})]


def test_a_run_that_died_mid_step_ends_failed_and_is_never_rerun(model, monkeypatch):
    model.append(tool("zz_wipe", target="tmp"))
    turn = unattended()
    p = approval_for(turn)
    proposals.decide(p["id"], "approve", source="web", by="web")
    # Claimed by a process that then died before finishing.
    assert runtime._claim(turn.run_id, "waiting_approval", "running", pid=999999999)
    runtime.fail_orphans()
    row = runtime.get(turn.run_id)
    assert row["status"] == "failed" and "interrupted" in row["error"]
    assert runtime.resume(turn.run_id) is None and RAN == []


def test_fail_orphans_spares_a_live_process():
    rid = "rlive"
    with runtime._lock(), runtime.closing(runtime._connect()) as conn:
        conn.execute("INSERT INTO runs (id, source, attended, status, pid, created) VALUES "
                     "(?,?,?,?,?,?)", (rid, "web", 1, "running", __import__("os").getpid(), time.time()))
        conn.commit()
    runtime.fail_orphans()
    assert runtime.get(rid)["status"] == "running"


def test_a_changed_world_refuses_at_resume(model):
    model.append(tool("zz_wipe", target="tmp"))
    turn = unattended()
    WORLD["files"] = 900                      # the card now says something else
    asked = []
    resumed = approve(turn, owner_confirmer=lambda a, d, g: asked.append(a) or False)
    assert RAN == [] and asked == ["wipe 'tmp' (900 files)"]     # the owner is asked anew
    assert resumed.status in ("done", "declined")


def test_a_target_that_became_protected_refuses_at_resume(model):
    model.append(tool("zz_wipe", target="tmp"))
    turn = unattended()
    WORLD["blocked"].add("tmp")
    approve(turn)
    assert RAN == []


def test_critical_and_secret_reads_are_never_approved_by_the_queue_alone(model):
    for name, args in (("zz_crit", {"what": "reboot"}), ("zz_secret", {"path": "/k"})):
        RAN.clear()
        model.append(tool(name, **args))
        turn = unattended()
        assert turn.status == "waiting_approval"
        # No live owner: the replay may not answer it, so it parks again.
        again = approve(turn)
        assert RAN == [] and again.status == "waiting_approval"
        # With the owner live, their own click decides.
        model.append(Reply(text="ok"))
        clicks = []
        approve(again, owner_confirmer=lambda a, d, g: clicks.append(a) or True)
        assert len(clicks) == 1 and RAN and RAN[0][0] == name


def test_the_resume_confirmer_approves_only_the_parked_card():
    parked = {"skill": "zz_send", "args": json.dumps({"channel": "a", "text": "t"}, sort_keys=True),
              "action": "send to 'a'", "danger": "GUARDED"}
    rc = runtime.ResumeConfirmer(parked, fallback=None)
    ctx = safety.CallContext(tool="zz_send", scope=(), args=parked["args"])
    with safety.call_context(ctx):
        assert rc("send to 'b'", "", Danger.GUARDED) is False       # different card
        assert rc("send to 'a'", "", Danger.DESTRUCTIVE) is False   # different danger
        assert rc("send to 'a'", "", Danger.GUARDED) is True
        assert rc("send to 'a'", "", Danger.GUARDED) is False       # only once
    other = safety.CallContext(tool="zz_send", scope=(), args=json.dumps({"channel": "a", "text": "x"}))
    rc2 = runtime.ResumeConfirmer(parked, fallback=None)
    with safety.call_context(other):
        assert rc2("send to 'a'", "", Danger.GUARDED) is False      # different arguments


# -- decline, expiry, who may decide -------------------------------------------------------

def test_decline_ends_the_run_and_nothing_runs(model):
    model.append(tool("zz_send", channel="general", text="hi"))
    turn = unattended()
    proposals.decide(approval_for(turn)["id"], "decline", source="web", by="web")
    assert runtime.get(turn.run_id)["status"] == "declined"
    assert runtime.resume(turn.run_id) is None and RAN == []


def test_expiry_declines_after_the_ttl(model, monkeypatch):
    model.append(tool("zz_send", channel="general", text="hi"))
    turn = unattended()
    later = time.time() + 25 * 3600
    monkeypatch.setattr(runtime.time, "time", lambda: later)
    runtime.sweep()
    assert runtime.get(turn.run_id)["status"] == "declined"
    assert approval_for(turn)["status"] == "expired"
    assert runtime.resume(turn.run_id) is None and RAN == []


def test_only_an_owner_source_decides_and_the_model_cannot_file_an_approval(model):
    model.append(tool("zz_send", channel="general", text="hi"))
    turn = unattended()
    with pytest.raises(proposals.NotAllowed):
        proposals.decide(approval_for(turn)["id"], "approve", source="schedule:j1")
    out = proposals.propose("Approve: send", ["x"], kind="approval", _source="web")
    assert "only by Kravein" in out


def test_resume_needs_a_stored_profile(model):
    model.append(tool("zz_send", channel="general", text="hi"))
    turn = unattended()
    with runtime._lock(), runtime.closing(runtime._connect()) as conn:
        conn.execute("UPDATE runs SET profile_spec=NULL WHERE id=?", (turn.run_id,))
        conn.commit()
    proposals.decide(approval_for(turn)["id"], "approve", source="web", by="web")
    assert runtime.resume(turn.run_id) is None
    assert runtime.get(turn.run_id)["status"] == "failed" and RAN == []


def test_resume_never_widens_the_profile(model):
    model.append(tool("zz_send", channel="general", text="hi"))
    narrow = AgentProfile(name="orchestrator", purpose="p", tools=("zz_send",))
    turn = runtime.submit(runtime.Run(source="schedule:j1", text="x", attended=False,
                                      profile=narrow))
    seen = []
    real = runtime.unattended_agent

    def spy(**kw):
        agent = real(**kw)
        seen.append(set(agent._allowed))
        return agent

    runtime_unattended = runtime.unattended_agent
    try:
        runtime.unattended_agent = spy
        approve(turn)
    finally:
        runtime.unattended_agent = runtime_unattended
    assert seen and seen[0] == {"zz_send"}


# -- jobs, daemon, review ------------------------------------------------------------------

def test_a_job_outcome_is_the_run_status(model, monkeypatch):
    monkeypatch.setattr(runtime, "UNATTENDED_PROFILE", "unatt-test")
    from kravein.agents import profile as profile_mod

    real_load = profile_mod.load_profile
    monkeypatch.setattr(profile_mod, "load_profile",
                        lambda name, **kw: PROFILE if name == "unatt-test" else real_load(name, **kw))
    import kravein.agent as agent_mod
    monkeypatch.setattr(agent_mod, "load_profile", profile_mod.load_profile)
    model.append(tool("zz_send", channel="general", text="hi"))
    ok, text, err = jobs.run_job({"id": "j9", "kind": "agent", "prompt": "send it"})
    assert not ok and "waiting for your approval: send to 'general'" in err and RAN == []
    model.append(tool("zz_status"))
    ok, text, err = jobs.run_job({"id": "j10", "kind": "agent", "prompt": "check"})
    assert ok and err == "" and RAN == [("zz_status", {})]


def test_jobs_no_longer_guess_from_words():
    source = Path(jobs.__file__).read_text(encoding="utf-8")
    assert "no way to ask" not in source and "needs confirmation" not in source


def test_the_daemon_parks_unless_the_client_confirms(model, tmp_path, monkeypatch):
    from kravein import daemon

    monkeypatch.setattr(runtime, "unattended_agent",
                        lambda source, **kw: runtime.new_agent(source=source, profile=PROFILE,
                                                               confirmer=None))
    server = daemon._Server(tmp_path / "k.sock")
    try:
        model.append(tool("zz_wipe", target="tmp"))
        out = server.dispatch({"action": "ask", "text": "wipe tmp", "session": "s1"})
        assert out["status"] == "waiting_approval" and not out["ok"] and RAN == []
        model.append(tool("zz_wipe", target="tmp2"))
        out = server.dispatch({"action": "ask", "text": "wipe tmp2", "session": "s1", "confirm": True})
        assert out["status"] == "done" and RAN == [("zz_wipe", {"target": "tmp2"})]
    finally:
        server.server_close()


def test_the_review_goes_through_the_runtime(model, monkeypatch):
    monkeypatch.setattr(proposals, "REVIEW_TOOLS", ("zz_status",))
    model.append(tool("zz_status"))
    ok, text, err = proposals.run_daily_review({"id": "rev"})
    assert ok and runtime.listing()[0]["trigger"] == "review:rev"


def test_audit_records_park_and_resume(model, monkeypatch):
    events = []
    real = audit.record
    monkeypatch.setattr(audit, "record", lambda e, **f: (events.append(e), real(e, **f)))
    model.append(tool("zz_send", channel="general", text="hi"))
    turn = unattended()
    approve(turn)
    for e in ("run.started", "run.parked_action", "run.parked", "run.resumed",
              "run.resume_approved", "run.ended"):
        assert e in events
