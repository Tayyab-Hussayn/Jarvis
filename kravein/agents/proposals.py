"""Proposals: the Orchestrator suggests, the owner decides.

A proposal is advice with a state machine, stored in ``agents.db``::

    pending -> approved | declined | expired
    approved -> running -> done | failed          (approved -> expired if never started)

Rules that must not be "improved" casually:

* **Approving a proposal confirms nothing inside it.** It only *starts* the
  task. The task then runs as an ordinary Orchestrator turn in the owner's live
  session, with that session's confirmer, so every DESTRUCTIVE step still asks.
  There is no second approval path and no pre-confirmed flag anywhere here.
* **Only a live owner source may decide** (``OWNER_SOURCES``). Jobs, the daemon
  and WhatsApp can *propose* but never decide or execute. The gate is in
  :func:`decide` itself, so REST and the ``decide_proposal`` skill share it.
* Every transition is one ``UPDATE ... WHERE status = <expected>``: deciding
  twice, or two sessions racing to start the same task, can only win once.
* The daily review is the Orchestrator with a code-fixed read-only tool list
  plus ``propose``, no confirmer. Anything else it tries is refused by
  ``registry.dispatch(allowed=)``.
"""

from __future__ import annotations

import dataclasses
import json
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from typing import Any

from .. import audit, config
from ..registry import skill
from ..safety import Danger
from . import history
from .profile import list_profiles, load_profile

ORCHESTRATOR = "orchestrator"

# Live owner sources: a person is at a client right now. Mirrors
# safety.AUTO_SOURCES plus the phone's UI. NOT jobs, daemon or WhatsApp.
OWNER_SOURCES = frozenset({"web", "web-panel", "phone", "voice", "voice-live", "cli", "cli-ask"})

KINDS = ("delegate", "note")
RISKS = ("low", "major")
STATUSES = ("pending", "approved", "declined", "expired", "running", "done", "failed")

# What the daily review may call besides `propose`. Read-only status tools only.
REVIEW_TOOLS = (
    "system_status", "disk_usage", "network_status", "battery_status",
    "weather", "list_apps", "recall", "propose",
)
REVIEW_PROMPT = (
    "Daily review. Look at this machine's state with your read-only tools "
    "(system, disk, network, battery, memory notes). For anything the owner "
    "should act on, call `propose` with a short title, concrete reasons and "
    "risk 'major' for anything that deletes, installs, spends or sends. Do not "
    "try to fix anything yourself. If nothing needs attention, propose nothing "
    "and say so in one line."
)

_write_lock = threading.Lock()
# Set by the web server at startup: frames can then go to its sessions
# directly; any other process (the jobs tick) posts to the server instead.
in_server = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    reasons    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    agent      TEXT NOT NULL DEFAULT '',
    task       TEXT NOT NULL DEFAULT '',
    risk       TEXT NOT NULL,
    status     TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    decided_at REAL,
    decided_by TEXT,
    result     TEXT
);
"""


class NotAllowed(PermissionError):
    """The caller is not a live owner source."""


def _connect() -> sqlite3.Connection:
    conn = history._connect()
    conn.executescript(_SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def _public(row: sqlite3.Row) -> dict[str, Any]:
    out = {k: row[k] for k in row.keys()}
    out["reasons"] = json.loads(out["reasons"] or "[]")
    for key in ("decided_at", "decided_by", "result"):
        if out.get(key) is None:
            out.pop(key)
    out["created_at"] = int(out["created_at"])
    if "decided_at" in out:
        out["decided_at"] = int(out["decided_at"])
    if not out["agent"]:
        out.pop("agent")
    if not out["task"]:
        out.pop("task")
    return out


def _ttl_seconds() -> float:
    return max(1.0, float(config.get("agents.proposal_ttl_days", 7) or 7)) * 86400.0


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def get(pid: str) -> dict[str, Any] | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM proposals WHERE id = ?", (pid,)).fetchone()
    return _public(row) if row else None


def listing(status: str = "") -> list[dict[str, Any]]:
    """Proposals, newest first, after sweeping the expired ones."""
    sweep_expired()
    with closing(_connect()) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM proposals WHERE status = ? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM proposals ORDER BY created_at DESC").fetchall()
    return [_public(r) for r in rows]


def sweep_expired(now: float | None = None) -> int:
    """Expire pending and never-started approved proposals past the TTL."""
    now = time.time() if now is None else now
    cutoff = now - _ttl_seconds()
    with _write_lock, closing(_connect()) as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM proposals WHERE status IN ('pending','approved') AND created_at < ?",
            (cutoff,),
        )]
        for pid in ids:
            conn.execute(
                "UPDATE proposals SET status='expired', decided_at=COALESCE(decided_at, ?) "
                "WHERE id = ? AND status IN ('pending','approved')", (now, pid),
            )
        conn.commit()
    for pid in ids:
        audit.record("proposal.expired", id=pid)
        announce(pid)
    return len(ids)


def _transition(pid: str, frm: tuple[str, ...], to: str, **fields: Any) -> bool:
    marks = ",".join(f"{k}=?" for k in fields)
    sql = f"UPDATE proposals SET status=?{',' + marks if marks else ''} " \
          f"WHERE id=? AND status IN ({','.join('?' * len(frm))})"
    with _write_lock, closing(_connect()) as conn:
        cur = conn.execute(sql, (to, *fields.values(), pid, *frm))
        conn.commit()
        return cur.rowcount == 1


def create(
    title: str, reasons: list[str], kind: str = "delegate", agent: str = "",
    task: str = "", risk: str = "low", created_by: str = "",
) -> dict[str, Any]:
    """Store a pending proposal and announce it. Raises ``ValueError`` on bad input."""
    title = (title or "").strip()[:200]
    reasons = [str(r).strip()[:400] for r in (reasons or []) if str(r).strip()][:8]
    kind = (kind or "").strip().lower()
    risk = (risk or "").strip().lower()
    agent = (agent or "").strip().lower()
    task = (task or "").strip()[:4000]
    if not title:
        raise ValueError("a proposal needs a title")
    if not reasons:
        raise ValueError("a proposal needs at least one reason")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if risk not in RISKS:
        raise ValueError(f"risk must be one of {', '.join(RISKS)}")
    if kind == "delegate":
        names = [n for n in list_profiles() if n != ORCHESTRATOR]
        if agent not in names:
            raise ValueError(f"a delegate proposal needs agent, one of: {', '.join(names)}")
        if not task:
            raise ValueError("a delegate proposal needs a task")
    else:
        agent, task = "", ""

    sweep_expired()
    cap = int(config.get("agents.proposal_max_pending", 20) or 20)
    with _write_lock, closing(_connect()) as conn:
        pending = conn.execute(
            "SELECT title FROM proposals WHERE status='pending'"
        ).fetchall()
        if any(r["title"].lower() == title.lower() for r in pending):
            raise ValueError("an identical proposal is already pending")
        if len(pending) >= cap:
            raise ValueError("too many proposals are waiting for the owner")
        pid = "p" + secrets.token_hex(3)
        conn.execute(
            "INSERT INTO proposals (id,title,reasons,kind,agent,task,risk,status,created_by,created_at)"
            " VALUES (?,?,?,?,?,?,?,'pending',?,?)",
            (pid, title, json.dumps(reasons), kind, agent, task, risk, created_by, time.time()),
        )
        conn.commit()
    audit.record("proposal.created", id=pid, kind=kind, risk=risk, agent=agent, by=created_by)
    announce(pid)
    return get(pid) or {}


def decide(pid: str, decision: str, *, source: str, by: str = "") -> tuple[dict[str, Any], bool]:
    """Approve or decline. Returns ``(proposal, changed)``.

    ``changed`` is False when it was already decided (idempotent: nothing is
    flipped and nothing runs twice). Raises :class:`NotAllowed` for a source
    that is not a live owner and ``KeyError`` for an unknown id.
    """
    if source not in OWNER_SOURCES:
        audit.record("proposal.decide_refused", id=pid, source=source)
        raise NotAllowed("only the owner, at a live session, can decide a proposal")
    decision = (decision or "").strip().lower()
    if decision not in ("approve", "decline"):
        raise ValueError("decision must be 'approve' or 'decline'")
    sweep_expired()
    if get(pid) is None:
        raise KeyError(pid)
    new = "approved" if decision == "approve" else "declined"
    changed = _transition(pid, ("pending",), new, decided_at=time.time(), decided_by=by or source)
    p = get(pid) or {}
    if changed:
        audit.record("proposal.decided", id=pid, decision=new, by=by or source)
        if new == "approved" and p.get("kind") == "note":
            # Advice only: nothing to run, so approving it just closes it.
            _transition(pid, ("approved",), "done", result="Acknowledged.")
            p = get(pid) or p
        announce(pid)
    return p, changed


def claim(pid: str) -> dict[str, Any] | None:
    """approved -> running, once. Returns the proposal if this caller won."""
    if not _transition(pid, ("approved",), "running"):
        return None
    audit.record("proposal.running", id=pid)
    announce(pid)
    return get(pid)


def finish(pid: str, ok: bool, result: str) -> None:
    if _transition(pid, ("running",), "done" if ok else "failed", result=(result or "")[:2000]):
        audit.record("proposal.finished", id=pid, ok=ok)
        announce(pid)


def fail_orphans() -> int:
    """At server start nothing can be running: mark leftover ``running`` rows failed."""
    with _write_lock, closing(_connect()) as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM proposals WHERE status='running'")]
        conn.execute(
            "UPDATE proposals SET status='failed', result='The server restarted while this was running.' "
            "WHERE status='running'"
        )
        conn.commit()
    for pid in ids:
        audit.record("proposal.orphaned", id=pid)
        announce(pid)
    return len(ids)


def approved_ids() -> list[str]:
    """Approved, unexpired proposals nobody started yet (oldest first)."""
    sweep_expired()
    with closing(_connect()) as conn:
        return [r["id"] for r in conn.execute(
            "SELECT id FROM proposals WHERE status='approved' AND kind='delegate' "
            "ORDER BY created_at"
        )]


APPROVED_PREFIX = "Owner approved proposal #"


def approval_prompt(p: dict[str, Any]) -> str:
    """The Orchestrator turn that starts an approved task."""
    return (
        f"{APPROVED_PREFIX}{p['id']}:{p.get('task', '')}\n"
        f"Hand it to the `{p.get('agent', '')}` agent with delegate and report the result. "
        "Anything destructive still needs the owner's confirmation."
    )


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------

def announce(pid: str) -> None:
    """Push the proposal's current state to every owner session.

    In the server process that is a broadcast to web sessions plus a
    notification on connected phones; anywhere else (the jobs tick) the server
    is asked to do it.
    """
    p = get(pid)
    if p is None:
        return
    if not in_server:
        try:
            from .. import delivery
            delivery.push("/api/proposal-event", {"id": pid})
        except Exception:
            pass
        return
    announce_local(p)


def announce_local(p: dict[str, Any]) -> None:
    try:
        from .. import events, phone
        events.broadcast("proposal", proposal=p)
        if p.get("status") == "pending":
            phone.notify_proposal(p)
    except Exception as exc:  # a dead client must never fail the store
        audit.record("proposal.announce_failed", id=p.get("id"), error=type(exc).__name__)


# ---------------------------------------------------------------------------
# Skills (Orchestrator only)
# ---------------------------------------------------------------------------

def _is_orchestrator(parent: Any, tool: str) -> bool:
    profile = getattr(parent, "profile", None)
    allowed = getattr(parent, "_allowed", None)
    return (
        profile is not None and profile.name == ORCHESTRATOR
        and allowed is not None and tool in allowed
        and getattr(parent, "depth", 1) == 0
    )


@skill(name="propose", category="agents", danger=Danger.SAFE)
def propose(
    title: str, reasons: list[str], kind: str = "delegate", agent: str = "",
    task: str = "", risk: str = "low", _source: str = "", _context: Any = None,
) -> str:
    """Suggest a next step to the owner and wait for their decision. Nothing runs now.

    Use it for anything major (deleting, installing, spending, sending) and for
    useful follow-ups. The owner approves or declines in the app or by voice.

    Args:
        title: One short line, what you suggest.
        reasons: Two or three concrete reasons ("do this, not that, because ...").
        kind: delegate (a sub-agent does it after approval) or note (advice only).
        agent: For delegate: the sub-agent that does it, for example system-ops.
        task: For delegate: the whole task, self-contained.
        risk: low or major. Use major for deleting, installing, spending or sending.
    """
    if not _is_orchestrator(_context, "propose"):
        audit.record("propose.refused", reason="caller is not the Orchestrator", source=_source)
        return "propose is only available to the Orchestrator."
    try:
        p = create(title, reasons, kind, agent, task, risk, created_by=_source or "orchestrator")
    except ValueError as exc:
        return f"Not proposed: {exc}"
    try:
        # Remember which turn made it: the model must not approve its own
        # proposal in the same breath (see decide_proposal).
        proposed = _context.__dict__.setdefault("_proposed", {})
        proposed[p["id"]] = getattr(_context, "turn_no", 0)
    except Exception:
        pass
    return f"Proposal #{p['id']} sent to the owner ({p['risk']} risk). Nothing runs until they approve."


@skill(name="decide_proposal", category="agents", danger=Danger.SAFE)
def decide_proposal(id: str, decision: str, _source: str = "", _context: Any = None) -> str:
    """Record the owner's decision on a proposal, when they say it in this conversation.

    Only call it after the owner clearly said to approve or decline that
    proposal. Approving starts the task now; every destructive step inside it
    still asks the owner for confirmation.

    Args:
        id: The proposal id, for example p1a2b3c.
        decision: approve or decline.
    """
    if not _is_orchestrator(_context, "decide_proposal"):
        return "decide_proposal is only available to the Orchestrator."
    pid = id.strip().lstrip("#")
    if str(getattr(_context, "turn_text", "")).startswith(APPROVED_PREFIX):
        # A turn the server started for an approved proposal carries no new
        # owner words: it must not approve a different proposal (or be steered
        # to by text read during the task).
        audit.record("proposal.decide_in_approved_turn_refused", id=pid, source=_source)
        return "This turn only carries out an approved proposal; it cannot decide others."
    if (getattr(_context, "_proposed", {}) or {}).get(pid) == getattr(_context, "turn_no", -1):
        # Code, not prompt: the owner's yes must come in a later message than
        # the one in which the model made the proposal.
        audit.record("proposal.self_approval_refused", id=pid, source=_source)
        return "You proposed that in this same request; wait for the owner's own answer."
    try:
        p, changed = decide(pid, decision, source=_source, by=_source)
    except NotAllowed as exc:
        return str(exc)
    except KeyError:
        return f"No proposal #{id}."
    except ValueError as exc:
        return str(exc)
    if p.get("status") == "declined":
        return f"Proposal #{p['id']} declined."
    if p.get("kind") == "note" or p.get("status") != "approved":
        return (f"Proposal #{p['id']} {p.get('status')}." if changed else
                f"Proposal #{p['id']} was already {p.get('status')}; nothing more to do.")
    if claim(p["id"]) is None:
        return f"Proposal #{p['id']} was already started."
    # Run it here, on this thread and under this session's confirmer: exactly
    # what `delegate` does for any other task. No nested turn, no new approval.
    from .delegate import delegate as run_delegate
    try:
        report = run_delegate(p["agent"], p["task"], f"Owner approved proposal #{p['id']}.",
                              _context=_context)
        ok = "outcome: done" in report.lower()
        finish(p["id"], ok, report)
        return report
    except Exception as exc:
        finish(p["id"], False, f"{type(exc).__name__}: {exc}")
        raise


# ---------------------------------------------------------------------------
# Daily review
# ---------------------------------------------------------------------------

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def review_cron() -> str:
    m = _TIME_RE.match(str(config.get("agents.daily_review_time", "09:00")).strip())
    hour, minute = (int(m.group(1)), int(m.group(2))) if m else (9, 0)
    return f"{minute} {hour} * * *"


def ensure_daily_review() -> str:
    """Make the stored review job match the settings. Returns what it did.

    One internal job through the existing scheduler: created when the setting
    is on, re-timed when the time changed, removed when it is off.
    """
    from .. import jobs

    existing = [j for j in jobs.all_jobs() if j.get("kind") == "review"]
    if not config.get("agents.daily_review", False):
        for j in existing:
            jobs.cancel(j["id"], _internal=True)
        return "removed" if existing else "off"
    cron = review_cron()
    if existing:
        keep = existing[0]
        for extra in existing[1:]:
            jobs.cancel(extra["id"], _internal=True)
        if keep.get("cron") != cron:
            jobs.edit(keep["id"], cron=cron, _internal=True)
            return "retimed"
        return "unchanged"
    jobs.add(prompt=REVIEW_PROMPT, cron=cron, kind="review", title="Daily review",
             _internal=True)
    return "created"


def run_daily_review(job: dict[str, Any]) -> tuple[bool, str, str]:
    """Run the Orchestrator unattended with read-only tools plus ``propose``.

    No confirmer, source ``schedule:<id>``: it can propose but not decide, and
    ``dispatch(allowed=)`` refuses any other tool the model tries.
    """
    from ..agent import Agent

    started = time.time()
    source = f"schedule:{job.get('id', '?')}"
    try:
        base = load_profile(ORCHESTRATOR)
        profile = dataclasses.replace(base, tools=REVIEW_TOOLS)
        turn = Agent(source=source, confirmer=None, profile=profile).ask(REVIEW_PROMPT)
    except Exception as exc:
        return False, "", f"{type(exc).__name__}: {exc}"
    if turn.error:
        return False, turn.reply or "", turn.error
    made = [p for p in listing() if p.get("created_by") == source and p["created_at"] >= int(started)]
    text = (f"Daily review: {len(made)} proposal(s) waiting for you."
            if made else f"Daily review: nothing needs attention. {turn.reply or ''}".strip())
    return True, text, ""
