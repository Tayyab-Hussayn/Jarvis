"""Tests: system-ops judgment, CONCERNS/IMPROVEMENTS, sub-agent proposals (#220)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from kravein import agent as agent_mod  # noqa: E402
from kravein import audit, config, events, jobs, phone, registry  # noqa: E402
from kravein.agents import delegate as dg, load_profile, proposals as pr  # noqa: E402
from kravein.agents.history import _connect as history_connect  # noqa: E402
from conftest import _agent_with  # noqa: E402
from test_delegate import (  # noqa: E402
    _zz_skills, call, orchestrate, say, setting,  # noqa: F401  (fixture is autouse there)
)

DELEGATE = call("delegate", agent="system-ops", task="clean up", wait=True)
DONE = "OUTCOME: done\nSUMMARY: ok\n"
FAILED_COUNT = "Not proposed"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "AUDIT_LOG", tmp_path / "audit.jsonl")
    monkeypatch.setattr(audit, "_fd", None)
    monkeypatch.setattr(jobs, "STORE", tmp_path / "jobs.json")
    monkeypatch.setattr(jobs, "LOCK", tmp_path / "jobs.lock")
    monkeypatch.setattr(jobs, "HISTORY", tmp_path / "job-runs.jsonl")
    monkeypatch.setattr(pr, "in_server", False)
    from kravein import delivery
    monkeypatch.setattr(delivery, "push", lambda *a, **k: False)
    yield
    monkeypatch.setattr(audit, "_fd", None)


def propose(**kw):
    args = {"title": "t", "reasons": ["because"], "kind": "capability"}
    args.update(kw)
    return call("propose", **args)


def run(child_script, *, source="test"):
    agent, router = orchestrate([DELEGATE, say("ok")], child_script, confirmer=lambda *a: True)
    agent.source = source
    agent.ask("tidy up")
    report = [m for m in agent.messages if m["role"] == "tool"][0]["content"]
    child_results = [
        m["content"] for call_msgs in router.seen["system-ops"][-1:]
        for m in call_msgs["messages"] if m.get("role") == "tool"
    ]
    return report, child_results, router


# -- the profiles ------------------------------------------------------------------

def test_system_ops_profile_holds_the_new_tools_and_never_decides():
    ops = load_profile("system-ops")
    tools = registry.resolve_tools(ops.tools)
    assert {"propose", "update_plan"} <= tools
    assert "decide_proposal" not in tools and "delegate" not in tools
    assert {"propose", "decide_proposal", "delegate"} <= registry.resolve_tools(
        load_profile("orchestrator").tools)
    # Only the profiles that list propose can use it.
    for name in ("research", "reviewer"):
        assert "propose" not in registry.resolve_tools(load_profile(name).tools)


def test_the_prompts_carry_the_judgment_words():
    ops = load_profile("system-ops").instructions
    assert "## Judgment" in ops and "CONCERNS" in ops and "IMPROVEMENTS" in ops
    assert "never a bare" in ops.lower() and "decision" in ops
    assert "bare refusal" in agent_mod.SYSTEM_PROMPT and "scope" in agent_mod.SYSTEM_PROMPT
    orch = load_profile("orchestrator").instructions
    assert "Concerns" in orch and "never decide it yourself" in orch
    assert "CONCERNS:" in dg.REPORT_INSTRUCTION and "IMPROVEMENTS:" in dg.REPORT_INSTRUCTION


# -- sub-agent proposals -------------------------------------------------------------

def test_a_subagent_files_a_capability_proposal_with_its_origin():
    report, _, _ = run([propose(title="No way to dry-run rm"), say(DONE)], source="web")
    [p] = pr.listing()
    assert p["kind"] == "capability" and p["origin"] == "system-ops" and p["status"] == "pending"
    assert p["created_by"] == "web"                       # never more trusted than its parent
    assert "agent" not in p and "task" not in p
    assert f"#{p['id']}: No way to dry-run rm" in report and "Proposals filed" in report


def test_a_subagent_note_works_too():
    run([propose(kind="note", title="Disk is 90% full"), say(DONE)])
    assert [(p["kind"], p["origin"]) for p in pr.listing()] == [("note", "system-ops")]


def test_a_subagent_cannot_file_a_delegate_proposal():
    _, results, _ = run([
        propose(kind="delegate", title="Wipe the cache", agent="system-ops", task="rm -rf ~/.cache"),
        say(DONE)])
    assert pr.listing() == []
    assert any("may only file kind 'note' or 'capability'" in r for r in results)


def test_unknown_kinds_are_refused_for_a_subagent_too():
    run([propose(kind="whatever"), say(DONE)])
    assert pr.listing() == []


def test_at_most_two_proposals_per_run_and_refusals_do_not_count():
    report, results, _ = run([
        propose(kind="delegate", title="no", agent="system-ops", task="x"),      # refused: not counted
        propose(title="one"), propose(title="two"), propose(title="three"),
        say(DONE)])
    assert sorted(p["title"] for p in pr.listing()) == ["one", "two"]
    assert any("at most 2 proposals per task" in r for r in results)
    assert "#" in report and report.count("one") >= 1 and "three" not in report.split("Tools used")[0]


def test_the_cap_is_per_run_not_per_process():
    run([propose(title="a1"), propose(title="a2"), say(DONE)])
    run([propose(title="b1"), propose(title="b2"), say(DONE)])
    assert len(pr.listing()) == 4


def test_a_subagent_cannot_decide_a_proposal():
    p = pr.create("advice", ["r"], "note", created_by="orchestrator")
    child, _ = _agent_with([say("x")], profile="system-ops", depth=1)
    # Dispatch refuses it: it is not in the profile's tool set ...
    out = registry.dispatch("decide_proposal", {"id": p["id"], "decision": "approve"},
                            allowed=child._allowed, context=child, source="web")
    assert out == "Skill 'decide_proposal' is not available to this agent."
    # ... and the skill itself refuses the caller too.
    assert "only available to the Orchestrator" in pr.decide_proposal(
        p["id"], "approve", _source="web", _context=child)
    assert pr.get(p["id"])["status"] == "pending"


def test_propose_still_belongs_to_the_orchestrator_and_listed_subagents_only():
    top, _ = _agent_with([say("x")], profile="system-ops")          # depth 0: not a sub-agent run
    assert "only available" in pr.propose("x", ["y"], "note", _context=top)
    assert "only available" in pr.propose("x", ["y"], "note", _context=None)
    assert pr.listing() == []


def test_the_orchestrators_own_proposals_are_unchanged():
    orch, _ = _agent_with([say("x")], profile="orchestrator")
    out = pr.propose("Do it", ["why"], "delegate", "system-ops", "clean", "major",
                     _source="web", _context=orch)
    assert out.startswith("Proposal #")
    [p] = pr.listing()
    assert "origin" not in p and p["kind"] == "delegate"


# -- origin on the wire --------------------------------------------------------------

def test_origin_is_in_the_rest_payload_and_the_frame(monkeypatch):
    from fastapi.testclient import TestClient

    from kravein.server import create_app

    monkeypatch.setattr(phone, "notify_proposal", lambda p: 1)
    config._settings_cache["agents"]["orchestrator"] = True
    sub = pr.create("A gap", ["r"], "capability", created_by="web", origin="system-ops")
    own = pr.create("Mine", ["r"], "note", created_by="orchestrator")
    token = "test-token-value"
    with TestClient(create_app(token), base_url="http://127.0.0.1:8787") as c:
        rows = c.get("/api/proposals", headers={"Authorization": f"Bearer {token}"}).json()["proposals"]
    by = {r["id"]: r for r in rows}
    assert by[sub["id"]]["origin"] == "system-ops" and "origin" not in by[own["id"]]

    class Fake:
        closed = False

        def __init__(self):
            self.frames = []

        def emit(self, t, **fields):
            self.frames.append((t, fields))

    fake = Fake()
    events.register(fake)
    monkeypatch.setattr(pr, "in_server", True)
    try:
        pr.create("Another", ["r"], "capability", created_by="web", origin="system-ops")
    finally:
        events.unregister(fake)
    frame = [f for t, f in fake.frames if t == "proposal"][0]["proposal"]
    assert frame["origin"] == "system-ops" and frame["kind"] == "capability"


def test_a_capability_proposal_is_advice_only():
    p = pr.create("A gap", ["r"], "capability", created_by="web", origin="system-ops")
    done, changed = pr.decide(p["id"], "approve", source="web")
    assert changed and done["status"] == "done" and done["result"] == "Acknowledged."
    assert pr.approved_ids() == []                        # nothing is ever started for it


def test_an_old_proposals_table_gets_the_origin_column():
    conn = history_connect()
    conn.executescript(
        "CREATE TABLE proposals (id TEXT PRIMARY KEY, title TEXT NOT NULL, reasons TEXT NOT NULL,"
        " kind TEXT NOT NULL, agent TEXT NOT NULL DEFAULT '', task TEXT NOT NULL DEFAULT '',"
        " risk TEXT NOT NULL, status TEXT NOT NULL, created_by TEXT NOT NULL, created_at REAL NOT NULL,"
        " decided_at REAL, decided_by TEXT, result TEXT);"
        "INSERT INTO proposals VALUES ('pold','Old','[\"r\"]','note','','','low','pending','web',1.0,"
        "NULL,NULL,NULL);")
    conn.commit()
    conn.close()
    assert pr.get("pold")["title"] == "Old" and "origin" not in pr.get("pold")
    new = pr.create("New", ["r"], "capability", created_by="web", origin="system-ops")
    assert new["origin"] == "system-ops"
    with sqlite3.connect(history_connect().execute("PRAGMA database_list").fetchone()[2]) as c:
        assert "origin" in {r[1] for r in c.execute("PRAGMA table_info(proposals)")}


# -- CONCERNS / IMPROVEMENTS ----------------------------------------------------------------

NEW = (
    "OUTCOME: partial\nSUMMARY: Cleaned ~/.cache only.\n"
    "CONCERNS:\n- rm -rf ~ would erase your files; I cleaned ~/.cache instead\n"
    "IMPROVEMENTS:\n- a trash step before deleting would make this undoable\n"
    "EVIDENCE:\n- list_files showed it empty\n"
)
OLD = ("OUTCOME: done\nSUMMARY: Restarted it.\nCHANGED:\n- bluetooth\n"
       "EVIDENCE:\n- service_status active\nQUESTIONS:\n- none\n")


def test_concerns_and_improvements_parse():
    r = dg.parse_report(NEW)
    assert r.concerns == ["rm -rf ~ would erase your files; I cleaned ~/.cache instead"]
    assert r.improvements == ["a trash step before deleting would make this undoable"]
    assert r.evidence == ["list_files showed it empty"]


def test_old_reports_still_parse_and_render_without_the_new_blocks():
    r = dg.parse_report(OLD)
    assert r.concerns == [] and r.improvements == [] and r.proposals == []
    text = dg.render(r, agent="system-ops", delegation="x", tools=[], limit=1500)
    assert "Concerns" not in text and "Improvements" not in text and "Restarted it." in text


def test_they_render_before_evidence():
    text = dg.render(dg.parse_report(NEW), agent="system-ops", delegation="x", tools=[], limit=1500)
    assert text.index("Concerns:") < text.index("Improvements:") < text.index("Evidence:")


def test_truncation_keeps_concerns_and_cuts_evidence_first():
    r = dg.parse_report(NEW)
    r.evidence = [f"line {n} " + "e" * 90 for n in range(40)]
    r.changed = [f"changed {n} " + "c" * 90 for n in range(20)]
    text = dg.render(r, agent="system-ops", delegation="x", tools=["a"], limit=700)
    assert "rm -rf ~ would erase" in text and "[report cut]" in text
    assert len(text) <= 700 and "line 39" not in text


def test_a_partial_run_report_end_to_end_carries_the_concerns():
    report, _, _ = run([say(NEW)])
    assert "outcome: partial" in report
    assert "Concerns:\n- rm -rf ~ would erase" in report and "Improvements:" in report
