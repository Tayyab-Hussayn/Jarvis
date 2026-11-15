"""One brain: every run starts here (#247, v3 phase 2a).

``Runtime.submit(Run(...))`` is the only way a request becomes a run, and
``new_agent`` is the only place an :class:`~kravein.agent.Agent` is built (a
test fails on any other ``Agent(`` in the code). The web server, the CLI, the
daemon, jobs, the daily review, delegated sub-agents and approved proposals
all come through here.

Every run is a row in ``agents.db`` ``runs``: who started it, how, and how it
ended, decided by code (never by reading the model's words).

**Unattended runs park instead of failing.** A run with nobody to ask (a job,
the daemon, the review) gets a :class:`Parker` as its confirmer. A grant still
answers first (owner ruling D4). Any card that reaches the Parker is recorded
exactly (skill, arguments, action text, danger, time); the Parker says NO, the
run stops, and the action appears in the owner's proposals queue as kind
``approval``. Nothing unattended ever approves anything.

**Resume, exactly once.** When the owner approves, :func:`resume` claims the
run with one conditional UPDATE (a second approval, a duplicate request or a
restart cannot claim it again), replays the parked skill with its exact
arguments, and continues the run. The replay goes through ``safety`` again:
a hard refusal, a changed target or a different card refuses. The replay
confirmer approves only the card the owner saw, never a CRITICAL, secret-read
or owner-only card on their behalf; those go to the owner's live confirmer or
park again. Declined or expired approvals end the run ``declined``. A run that
was mid-tool when its process died ends ``failed``: it is never re-run.
"""

from __future__ import annotations

import dataclasses
import json
import os
import secrets
import threading
import time
from contextlib import closing
from dataclasses import dataclass, field
from typing import Any, Callable

from . import audit, config, safety

STATUSES = ("queued", "running", "waiting_approval", "done", "partial", "failed",
            "declined", "cancelled")
UNATTENDED_PROFILE = "orchestrator-unattended"
STEP_LOG_MAX = 40
CONTINUE_PROMPT = (
    "[context] The owner approved the action that was waiting ({action}). It has been run; its "
    "result is in the transcript. Continue the original task from there and finish it."
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    principal     TEXT NOT NULL DEFAULT '',
    grant_owner   TEXT NOT NULL DEFAULT '',
    profile       TEXT NOT NULL DEFAULT '',
    profile_spec  TEXT,
    attended      INTEGER NOT NULL,
    trigger       TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL,
    pid           INTEGER,
    created       REAL NOT NULL,
    started       REAL,
    ended         REAL,
    text          TEXT NOT NULL DEFAULT '',
    taint         TEXT NOT NULL DEFAULT '[]',
    steps         TEXT NOT NULL DEFAULT '[]',
    result        TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    parked        TEXT,
    transcript    TEXT,
    proposal      TEXT NOT NULL DEFAULT '',
    executed_key  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS runs_status ON runs (status);
"""


@dataclass
class Run:
    """One request to run. ``agent`` = a session's long-lived agent (web, CLI,
    daemon session); without it the Runtime builds a fresh one."""

    source: str
    text: str
    principal: str = ""
    profile: Any = None                 # a profile name, an AgentProfile, or None
    attended: bool = True
    trigger: str = ""
    thread_id: str | None = None
    confirmer: Any = None
    observer: Any = None
    stop: Any = None
    agent: Any = None
    allowed: frozenset[str] | None = None
    grant_principal: str = ""           # a job's creator (D4)
    taint: tuple[str, ...] = ()         # carried in from the job's creating run


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def _connect():
    import sqlite3

    from .agents import history

    conn = history._connect()
    conn.executescript(_SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def _lock():
    from .agents import history

    return history._write_lock


def _update(rid: str, **fields: Any) -> None:
    sets = ",".join(f"{k}=?" for k in fields)
    with _lock(), closing(_connect()) as conn:
        conn.execute(f"UPDATE runs SET {sets} WHERE id=?", (*fields.values(), rid))
        conn.commit()


def get(rid: str) -> dict[str, Any] | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    if row is None:
        return None
    out = {k: row[k] for k in row.keys()}
    for key in ("taint", "steps", "parked"):
        out[key] = json.loads(out[key]) if out.get(key) else ([] if key != "parked" else None)
    out.pop("transcript", None)
    return out


def listing(status: str = "", limit: int = 50) -> list[dict[str, Any]]:
    with closing(_connect()) as conn:
        sql, args = "SELECT id FROM runs", []
        if status:
            sql, args = sql + " WHERE status=?", [status]
        ids = [r["id"] for r in conn.execute(sql + " ORDER BY created DESC LIMIT ?", (*args, limit))]
    return [r for r in (get(i) for i in ids) if r]


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return b"kravein" in fh.read()
    except (OSError, ProcessLookupError):
        return False


def fail_orphans() -> int:
    """A run left ``running`` by a process that is gone ended mid-step: mark it
    ``failed``. It is never re-run (a side effect may already have happened).
    Pid-checked, so one process never fails another live process's runs."""
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT id, pid FROM runs WHERE status IN ('running','queued')").fetchall()
    dead = [r["id"] for r in rows if not _pid_alive(r["pid"])]
    for rid in dead:
        with _lock(), closing(_connect()) as conn:
            conn.execute("UPDATE runs SET status='failed', ended=?, error=? WHERE id=? "
                         "AND status IN ('running','queued')",
                         (time.time(), "interrupted: Kravein stopped while this run was in progress",
                          rid))
            conn.commit()
        audit.record("run.orphaned", run=rid)
    return len(dead)


def _ttl() -> float:
    return max(60.0, float(config.get("agents.approval_ttl_hours", 24) or 24) * 3600.0)


def sweep() -> int:
    """Parked runs whose approval was declined, expired, or is too old -> declined."""
    from .agents import proposals

    proposals.sweep_expired()
    now = time.time()
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT id, proposal, parked FROM runs WHERE status='waiting_approval'").fetchall()
    ended = 0
    for row in rows:
        parked = json.loads(row["parked"] or "{}")
        p = proposals.get(row["proposal"]) if row["proposal"] else None
        too_old = now - float(parked.get("requested_at") or 0) > _ttl()
        if p is None or p.get("status") in ("declined", "expired") or too_old:
            if too_old and p is not None and p.get("status") == "pending":
                proposals._transition(p["id"], ("pending",), "expired", decided_at=now)
                proposals.announce(p["id"])
            why = "approval expired" if too_old or (p or {}).get("status") == "expired" else "declined"
            if _claim(row["id"], "waiting_approval", "declined", ended=now, result=why):
                audit.record("run.declined", run=row["id"], reason=why)
                ended += 1
    return ended


def _claim(rid: str, frm: str, to: str, **fields: Any) -> bool:
    sets = ",".join(f"{k}=?" for k in ("status", *fields))
    with _lock(), closing(_connect()) as conn:
        cur = conn.execute(f"UPDATE runs SET {sets} WHERE id=? AND status=?",
                           (to, *fields.values(), rid, frm))
        conn.commit()
        return cur.rowcount == 1


# ---------------------------------------------------------------------------
# Confirmers for runs nobody watches
# ---------------------------------------------------------------------------

class Parker:
    """The confirmer of an unattended run: records the first card and says no.

    Never approves anything. ``attended()`` is False, so auto-approve and the
    #199 rules never answer for it; a GUARDED grant is checked before any
    confirmer, so D4 still holds.
    """

    def __init__(self) -> None:
        self.parked: dict[str, Any] | None = None

    def attended(self) -> bool:
        return False

    def __call__(self, action: str, detail: str, danger: Any) -> bool:
        ctx = safety.current_call()
        card = safety.current_card() or {}
        if self.parked is None and ctx is not None:
            self.parked = {
                "skill": ctx.tool, "args": ctx.args, "action": action, "detail": detail,
                "danger": safety.Danger(danger).name, "no_auto": bool(card.get("no_auto")),
                "targets": list(card.get("targets") or []), "requested_at": time.time(),
            }
            audit.record("run.parked_action", skill=ctx.tool, action=action[:300],
                         danger=self.parked["danger"])
        return False


class ResumeConfirmer:
    """Approves exactly one card: the one the owner approved in the queue.

    Same tool, same arguments, same action text, same danger, and never a
    CRITICAL, secret-read or owner-only card (those always reach a live click).
    Anything else goes to ``fallback``: the owner's live confirmer, or a new
    :class:`Parker` (the run parks again).
    """

    def __init__(self, parked: dict[str, Any], fallback: Any) -> None:
        self.parked = parked
        self.fallback = fallback
        self.used = False

    def attended(self) -> bool:
        check = getattr(self.fallback, "attended", None)
        return bool(check()) if callable(check) else self.fallback is not None

    def __call__(self, action: str, detail: str, danger: Any) -> Any:
        ctx = safety.current_call()
        card = safety.current_card() or {}
        p = self.parked
        same = (not self.used and ctx is not None and ctx.tool == p["skill"]
                and ctx.args == p["args"] and action == p["action"]
                and safety.Danger(danger).name == p["danger"])
        if same and safety.Danger(danger) < safety.Danger.CRITICAL and not card.get("no_auto"):
            self.used = True
            audit.record("run.resume_approved", skill=p["skill"], action=action[:300])
            return True
        if same:
            audit.record("run.resume_needs_owner", skill=p["skill"], danger=p["danger"])
        return self.fallback(action, detail, danger) if self.fallback is not None else False


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

def new_agent(**kwargs: Any):
    """The only ``Agent`` constructor (a test greps for others)."""
    from .agent import Agent

    return Agent(**kwargs)


def session_agent(*, source: str, thread_id: str | None = None, **kwargs: Any):
    """The Agent for a transport the owner talks to (web, voice, CLI).

    ``agents.orchestrator`` (default off) makes it the Orchestrator profile;
    off, exactly ``Agent(source=...)``. A broken profile falls back to the
    all-tools agent rather than taking the assistant down.
    """
    from .agent import ORCHESTRATOR
    from .agents import ProfileError

    if config.get("agents.orchestrator", False):
        try:
            return new_agent(source=source, thread_id=thread_id, profile=ORCHESTRATOR, **kwargs)
        except ProfileError as exc:
            audit.record("agent.orchestrator_unavailable", error=str(exc))
    return new_agent(source=source, thread_id=thread_id, **kwargs)


def voice_agent(*, observer: Any, thread_id: str | None):
    """The Orchestrator context a Live voice session runs as, or ``None``."""
    from .agent import ORCHESTRATOR

    if not config.get("agents.orchestrator", False):
        return None
    try:
        return new_agent(source="voice-live", observer=observer, thread_id=thread_id,
                         profile=ORCHESTRATOR)
    except Exception as exc:                  # voice must still start, as before
        audit.record("agent.orchestrator_unavailable", error=str(exc), at="voice")
        return None


def unattended_agent(*, source: str, profile: Any = None, **kwargs: Any):
    """An agent for a run nobody watches: the read-mostly unattended profile
    (unless a caller passes its own narrower one) and no confirmer."""
    return new_agent(source=source, profile=profile or UNATTENDED_PROFILE, confirmer=None, **kwargs)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def _profile_name(agent: Any, run: Run) -> str:
    prof = getattr(agent, "profile", None)
    if prof is not None:
        return str(getattr(prof, "name", "") or "")
    return run.profile if isinstance(run.profile, str) else ""


def _status(turn: Any, parker: Parker | None) -> str:
    from .agent import DECLINED_REPLY

    if parker is not None and parker.parked is not None:
        return "waiting_approval"
    if turn.cancelled:
        return "cancelled"
    if turn.error:
        return "failed"
    if turn.reply == DECLINED_REPLY:
        return "declined"
    if turn.budget_exhausted:
        return "partial"
    return "done"


def submit(run: Run):
    """Start ``run`` and return its :class:`~kravein.agent.Turn` with ``run_id``
    and ``status`` set. Blocks until the run ends or parks."""
    from .agent import Turn

    if not run.attended:
        sweep()
    rid = "r" + secrets.token_hex(5)
    now = time.time()
    agent = run.agent
    if agent is None:
        if run.attended:
            agent = new_agent(source=run.source, profile=run.profile, thread_id=run.thread_id,
                              observer=run.observer, confirmer=run.confirmer)
        else:
            agent = unattended_agent(source=run.source, profile=run.profile,
                                     thread_id=run.thread_id, observer=run.observer)
    if run.principal and not getattr(agent, "principal", ""):
        agent.principal = run.principal
    if run.grant_principal:
        agent.grant_principal = run.grant_principal
    if run.taint:
        agent.initial_taint = frozenset(run.taint)
    # The exact profile the run had (the review narrows the Orchestrator's
    # tools): a resume rebuilds THIS, never a profile looked up by name, which
    # could carry more tools than the run was given.
    prof = getattr(agent, "profile", None)
    spec = json.dumps(dataclasses.asdict(prof)) if prof is not None else None
    with _lock(), closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO runs (id, source, principal, grant_owner, profile, profile_spec, attended, "
            "trigger, status, pid, created, started, text, taint) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, run.source, run.principal, run.grant_principal, _profile_name(agent, run), spec,
             int(bool(run.attended)), run.trigger, "running", os.getpid(), now, now,
             run.text[:4000], json.dumps(sorted(run.taint))))
        conn.commit()
    audit.record("run.started", run=rid, source=run.source, attended=bool(run.attended),
                 trigger=run.trigger)
    return _execute(rid, agent, run.text, run, stop=run.stop, allowed=run.allowed)


def _execute(rid: str, agent: Any, text: str, run: Run, *, stop: Any = None,
             allowed: frozenset[str] | None = None, parker: Parker | None = None,
             keep_confirmer: bool = False):
    """Run one ``ask`` for run ``rid`` and record how it ended.

    An unattended run gets a fresh :class:`Parker` unless the caller already
    chose its confirmer (``resume`` continuing under the owner's)."""
    from .agent import Turn

    if not run.attended and parker is None and not keep_confirmer:
        parker = Parker()
        agent.confirmer = parker
    elif parker is not None:
        agent.confirmer = parker
    previous_check = getattr(agent, "end_turn_check", None)
    if parker is not None:
        # Stop the moment something parks: the run waits for the owner, it
        # does not try the next thing.
        agent.end_turn_check = (lambda: parker.parked is not None or (
            previous_check is not None and bool(previous_check())))
    try:
        turn = agent.ask(text, stop, allowed=allowed) if allowed is not None else agent.ask(text, stop)
    except Exception as exc:                         # a run always ends with a status
        turn = Turn(error=f"{type(exc).__name__}: {exc}")
    finally:
        if parker is not None:
            agent.end_turn_check = previous_check
    status = _status(turn, parker)
    steps = list(turn.tool_calls)[-STEP_LOG_MAX:]
    fields: dict[str, Any] = {"steps": json.dumps(steps), "result": (turn.reply or "")[:2000],
                              "error": (turn.error or "")[:500]}
    if status == "waiting_approval":
        status, fields = _park(rid, agent, run, parker.parked, fields)
    else:
        fields["ended"] = time.time()
    _update(rid, status=status, **fields)
    audit.record("run.ended", run=rid, status=status)
    turn.run_id, turn.status = rid, status
    if status == "waiting_approval":
        turn.parked = parker.parked
    return turn


def _park(rid: str, agent: Any, run: Run, parked: dict[str, Any],
          fields: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Store the transcript and the action; file the owner's approval."""
    from .agents import history, proposals

    # The tool message that was refused: its result is replaced at resume.
    for m in reversed(agent.messages):
        if m.get("role") == "tool" and m.get("name") == parked["skill"]:
            parked["tool_call_id"] = m.get("tool_call_id") or ""
            break
    parked["idem_key"] = f"{rid}:{parked.get('tool_call_id') or 'x'}:{int(parked['requested_at'])}"
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(parked["requested_at"]))
    try:
        p = proposals.create(
            title=f"Approve: {parked['action']}"[:200],
            reasons=[parked["detail"] or parked["danger"].lower(),
                     f"requested {when} by {run.source} (run {rid}, waiting for you)"],
            kind="approval", task=rid, risk="major", created_by=run.source,
        )
    except ValueError as exc:
        # Could not reach the owner (queue full, duplicate): an honest failure.
        audit.record("run.park_failed", run=rid, error=str(exc))
        fields.update(ended=time.time(), error=f"needed your approval but could not ask: {exc}")
        return "failed", fields
    fields.update(parked=json.dumps(parked), proposal=p["id"],
                  transcript=json.dumps(agent.messages, ensure_ascii=False, default=history._encode))
    audit.record("run.parked", run=rid, proposal=p["id"], skill=parked["skill"])
    return "waiting_approval", fields


def resume(rid: str, *, owner_confirmer: Any = None, decided_by: str = ""):
    """Run the parked action of ``rid`` once and continue the run.

    Returns the continuation's Turn (``status`` set), or ``None`` when this
    caller did not win the claim (already resumed, declined, expired, unknown).
    """
    from .agents import history, proposals

    sweep()
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    if row is None or row["status"] != "waiting_approval":
        return None
    parked = json.loads(row["parked"] or "{}")
    if not parked:
        return None
    # Exactly once: one conditional UPDATE wins; the key records what ran.
    if not _claim(rid, "waiting_approval", "running", pid=os.getpid(),
                  executed_key=parked["idem_key"], started=time.time()):
        audit.record("run.resume_lost_claim", run=rid)
        return None
    audit.record("run.resumed", run=rid, by=decided_by, skill=parked["skill"])
    run = Run(source=row["source"], text=row["text"], principal=row["principal"],
              profile=row["profile"] or None, attended=False, trigger=row["trigger"],
              grant_principal=row["grant_owner"], taint=tuple(json.loads(row["taint"] or "[]")))
    fallback = owner_confirmer if owner_confirmer is not None else Parker()
    replay = ResumeConfirmer(parked, fallback)
    try:
        from .agents import AgentProfile

        spec = json.loads(row["profile_spec"] or "null")
        if not isinstance(spec, dict):
            raise ValueError("the parked run has no stored profile")
        spec["tools"] = tuple(spec.get("tools") or ())
        agent = unattended_agent(source=run.source, profile=AgentProfile(**spec))
        messages = json.loads(row["transcript"] or "[]")
        for m in messages:
            history._restore_signatures(m)
        agent.messages = messages
        agent.grant_principal = run.grant_principal
        agent.initial_taint = frozenset(run.taint)
        agent.confirmer = replay
        from . import registry
        from .safety import TurnMemory, confirmer_scope

        with confirmer_scope(replay, TurnMemory(), principal=run.principal or None,
                             grant_principal=run.grant_principal or None):
            result = registry.dispatch(parked["skill"], json.loads(parked["args"]),
                                       source=run.source, allowed=agent._allowed, context=agent)
    except Exception as exc:
        _update(rid, status="failed", ended=time.time(), error=f"resume failed: {type(exc).__name__}")
        audit.record("run.resume_failed", run=rid, error=type(exc).__name__)
        _finish_proposal(row["proposal"], False, "The resumed run failed.")
        return None
    for m in agent.messages:
        if m.get("role") == "tool" and parked.get("tool_call_id") and \
                m.get("tool_call_id") == parked["tool_call_id"]:
            m["content"] = agent._cap_result(parked["skill"], result)
    # Continue the run under the same rules: the owner's live confirmer for
    # anything else, or park again. The replay approval is spent.
    parker = fallback if isinstance(fallback, Parker) else None
    if parker is None:
        agent.confirmer = fallback
    turn = _execute(rid, agent, CONTINUE_PROMPT.format(action=parked["action"][:200]), run,
                    parker=parker, keep_confirmer=parker is None)
    _finish_proposal(row["proposal"], turn.status in ("done", "partial", "waiting_approval"),
                     turn.reply or turn.error or turn.status)
    return turn


def _finish_proposal(pid: str, ok: bool, result: str) -> None:
    if not pid:
        return
    from .agents import proposals

    proposals.claim(pid)
    proposals.finish(pid, ok, result)


def proposal_decided(p: dict[str, Any]) -> None:
    """A decided ``approval`` proposal: a decline ends its run now."""
    if p.get("kind") != "approval" or not p.get("task"):
        return
    if p.get("status") in ("declined", "expired"):
        if _claim(p["task"], "waiting_approval", "declined", ended=time.time(),
                  result=f"the owner {p['status']} the action"):
            audit.record("run.declined", run=p["task"], reason=p["status"])


def ask(agent: Any, text: str, stop: Any = None, *, source: str = "", principal: str = "",
        allowed: frozenset[str] | None = None, attended: bool = True, trigger: str = "chat"):
    """``submit`` for a session's own agent: what the transports call per turn."""
    return submit(Run(source=source or getattr(agent, "source", "") or "session", text=text,
                      principal=principal or getattr(agent, "principal", "") or "",
                      attended=attended, trigger=trigger, agent=agent, stop=stop, allowed=allowed))


_started = False
_started_lock = threading.Lock()


def start() -> None:
    """Once per process that hosts runs (server, daemon): settle leftovers."""
    global _started
    with _started_lock:
        if _started:
            return
        _started = True
    try:
        fail_orphans()
        sweep()
    except Exception as exc:
        audit.record("run.start_failed", error=type(exc).__name__)
