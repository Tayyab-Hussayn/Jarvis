"""``delegate``: the Orchestrator hands a task to a sub-agent.

Safety model (this is the part that must not be "improved" casually):

* ``delegate`` starts a **background task** by default (``tasks.py``, which has
  its own safety notes: confirmations go to the owner's attached sessions or
  deny, denial memory per task, reports are untrusted). ``wait=True`` — and any
  process with no task host (CLI, jobs tick) — runs it on the caller's thread as
  described below. Both paths call :func:`run_child`, so the rules below hold
  for both; nothing in it reads the caller's thread (``Parent`` is a snapshot).
* In blocking mode the sub-agent runs on the **caller's thread**, inside the
  caller's request.
  Its confirmer is the owner's confirmer with one change: the card is prefixed
  with the agent name. It returns exactly what the owner's confirmer returned;
  nothing here can approve anything. Timeout, dropped socket and "no confirmer
  at all" all reach it as a refusal.
* Its ``source`` is the parent's. Auto-approve and "unattended" decisions key on
  that string, so a sub-agent is never more trusted than the agent that called
  it. The agent name goes into the audit log and the cards, not into ``source``.
* It shares the parent's denial memory, so a "no" holds across both.
* A decline (or an unattended refusal) ends the delegated task; the report says
  so, and the outcome is set by code, never by what the model claims.
* Sub-agents cannot delegate: ``Agent`` refuses to build any profile but the
  Orchestrator with this tool, and this function refuses any other caller.
* The sub-agent's raw tool output stays in its own history; the caller only
  receives the rendered report.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import audit, config, safety
from ..registry import skill
from ..safety import Danger
from .profile import ProfileError, list_profiles, load_profile

ORCHESTRATOR = "orchestrator"
OUTCOMES = ("done", "partial", "failed", "declined", "needs-approval")

REPORT_INSTRUCTION = """\
## Reporting
You were given this task by the Orchestrator, not by the owner. Finish it with \
the tools you have, then end with a report in exactly this form:

OUTCOME: done | partial | failed
SUMMARY: one or two sentences — what you did and what the owner needs to know
CHANGED:
- each thing you changed (omit the section if nothing)
EVIDENCE:
- what a tool result confirmed (a verified effect, not an acknowledgement)
QUESTIONS:
- anything you could not decide without the owner (omit if none)
PLAN:
- [completed|in_progress|pending] each plan item (only if you kept a plan)
LEFT:
- what still has to be done (omit if nothing)
CONCERNS:
- a risk you saw or a request you pushed back on, with the safer way you offered (omit if none)
IMPROVEMENTS:
- a better approach or a missing capability worth the owner's attention (omit if none)

Say "done" only if a tool result proves it. Do not paste raw tool output. \
If your budget runs out you will be asked for this report with OUTCOME: partial.\
"""


class _LinkedStop:
    """A stop flag for the sub-agent: the caller's, or the wall-clock deadline.

    ``Agent`` only ever calls ``is_set()``, so no helper thread is needed.
    """

    def __init__(self, parent: Any, deadline: float) -> None:
        self.parent = parent
        self.deadline = deadline

    def is_set(self) -> bool:
        return self.stopped_by_owner() or self.timed_out()

    def stopped_by_owner(self) -> bool:
        return self.parent is not None and self.parent.is_set()

    def timed_out(self) -> bool:
        return time.monotonic() >= self.deadline


@dataclass
class Report:
    outcome: str = ""
    summary: str = ""
    changed: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    # The plan as the child's code holds it (never the model's own claim) and
    # what is not finished; both appear in a partial report.
    plan: list[dict[str, str]] = field(default_factory=list)
    left: list[str] = field(default_factory=list)
    # Judgment (#220): risks the agent saw, better ways it would suggest, and
    # the proposals it filed (taken from code, like the plan).
    concerns: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    proposals: list[dict[str, str]] = field(default_factory=list)
    # "<run id> (N file(s))" when file changes of this run can be undone (#223).
    undo: str = ""


_HEAD = re.compile(
    r"^\s*(OUTCOME|SUMMARY|CHANGED|EVIDENCE|QUESTIONS|PLAN|LEFT|CONCERNS|IMPROVEMENTS)"
    r"\s*:\s*(.*)$", re.I)


def parse_report(text: str) -> Report | None:
    """Read the last OUTCOME/SUMMARY/... block in ``text``; None if there is none."""
    lines = (text or "").splitlines()
    starts = [i for i, ln in enumerate(lines)
              if (m := _HEAD.match(ln)) and m.group(1).lower() == "outcome"]
    if not starts:
        return None
    buf: dict[str, list[str]] = {}
    current = ""
    for line in lines[starts[-1]:]:
        m = _HEAD.match(line)
        if m:
            current = m.group(1).lower()
            buf.setdefault(current, []).append(m.group(2))
        elif current:
            buf[current].append(line)

    def items(key: str) -> list[str]:
        out = []
        for raw in buf.get(key, []):
            item = raw.strip().lstrip("-*• ").strip()
            if item:
                out.append(item)
        return out

    word = (items("outcome") or [""])[0].split()[0].lower().strip(".,") if items("outcome") else ""
    return Report(
        outcome=word if word in ("done", "partial", "failed") else "",
        summary=" ".join(items("summary")),
        changed=items("changed"),
        evidence=items("evidence"),
        questions=items("questions"),
        left=items("left"),
        concerns=items("concerns"),
        improvements=items("improvements"),
    )


def render(report: Report, *, agent: str, delegation: str, tools: list[str], limit: int) -> str:
    """The text the caller sees, capped at ``limit`` characters."""
    head = f"[{agent} report #{delegation}] outcome: {report.outcome}"
    parts = [f"Summary: {report.summary or '(none given)'}"]
    # Right after the summary: the length cap below cuts from the end, and the
    # plan is the part of a partial report the caller can least rebuild.
    if report.plan:
        from . import plan as _plan

        parts.append(_plan.render(report.plan))
    if report.undo:
        parts.append(f"Undo available: {report.undo} - undo_changes(task=\"{report.undo.split()[0]}\") "
                     "restores the file changes only (not packages or services).")
    filed = [f"#{p['id']}: {p['title']}" for p in report.proposals]
    # Concerns and ideas go before Changed/Evidence: the length cap cuts from
    # the end, and these are what the Orchestrator must pass on to the owner.
    for label, rows in (("Concerns", report.concerns), ("Left to do", report.left),
                        ("Improvements", report.improvements),
                        ("Proposals filed (waiting for the owner)", filed),
                        ("Changed", report.changed), ("Evidence", report.evidence),
                        ("Open questions", report.questions)):
        if rows:
            parts.append(f"{label}:\n" + "\n".join(f"- {r}" for r in rows))
    if tools:
        parts.append("Tools used: " + ", ".join(tools[:20]) + (" …" if len(tools) > 20 else ""))
    text = head + "\n" + "\n".join(parts)
    if limit > 0 and len(text) > limit:
        text = text[: max(limit - 20, len(head))].rstrip() + "\n…[report cut]"
    return text


def _fallback(turn: Any) -> Report:
    """A report built from the turn itself, for a model that gave none."""
    said = (getattr(turn, "reply", "") or getattr(turn, "error", "") or "").strip()
    tools = list(getattr(turn, "tool_calls", []) or [])
    return Report(
        outcome="partial",
        summary=(said[:600] or "The sub-agent returned no text.")
        + " (No structured report was given; this is its last message.)",
        evidence=[f"tool calls made: {', '.join(tools)}"] if tools else [],
    )


def _budget_report(turn: Any, plan: list[dict[str, str]]) -> Report:
    """The report for a run that ran out of budget and could not write one.

    Built from what code knows: the plan and the tool log. Nothing in it is a
    claim by the model.
    """
    from collections import Counter

    from . import plan as _plan

    tools = list(getattr(turn, "tool_calls", []) or [])
    counts = Counter(tools)
    done = [i["content"] for i in plan if i["status"] == "completed"]
    why = getattr(turn, "budget_exhausted", "")
    if plan:
        summary = f"Stopped before finishing: {why}. {len(done)} of {len(plan)} plan items are completed."
    else:
        summary = f"Stopped before finishing: {why}. No plan was kept, so the tool log is all there is."
    evidence = [f"completed: {c}" for c in done]
    if tools:
        evidence.append("tool calls made: " + ", ".join(
            f"{n} x{c}" if c > 1 else n for n, c in counts.most_common(12)))
    return Report(outcome="partial", summary=summary, evidence=evidence,
                  left=_plan.remaining(plan))


@dataclass
class Parent:
    """Everything the sub-agent needs from the agent that delegated.

    A snapshot, not a live object: a background task outlives the parent's turn,
    its thread and its thread-local confirmer, so nothing here may refer to them.
    """

    source: str
    thread_id: str | None = None
    principal: str = ""
    turn_no: int = 0
    origin: str = ""                  # which agent object (browser tab) started it
    stop: Any = None                  # anything with is_set(): the owner's Stop
    emit: Callable[[str, str, dict], None] | None = None   # activity sink
    confirm: Callable[..., bool] | None = None             # None = nobody can be asked
    memory: Any = None                # the denial memory this task shares


def run_child(name: str, task: str, context: str, parent: Parent, did: str,
              timeout: float | None = None) -> tuple[str, "Report"]:
    """Run sub-agent ``name`` to completion and return ``(rendered, report)``.

    The whole safety model in the module docstring lives here, once, for both
    the blocking call and the background task. The caller's thread is never
    consulted: the confirmer, memory and stop flag all come from ``parent``.
    """
    try:
        sub_profile = load_profile(name)
        from ..agent import Agent

        limit = sub_profile.report_max_chars or int(config.get("agents.report_max_chars", 1500))
        if timeout is None:
            timeout = float(config.get("agents.delegate_timeout_seconds", 300))
        deadline = time.monotonic() + timeout
        stop = _LinkedStop(parent.stop, deadline)

        real = parent.confirm
        unattended = real is None          # nobody to ask: stay that way
        memory = parent.memory
        base_targets = len(memory.targets)
        base_retried = memory.retried_declined
        state = {"declined": False}

        def confirm(action: str, detail: str, danger: Danger) -> bool:
            try:
                ok = bool(real(f"{name}: {action}", detail, danger))
            except Exception:
                ok = False
            if not ok:
                state["declined"] = True
            return ok

        # `attended()` travels with the confirmer: safety.enforce() uses it to
        # refuse auto-approve when nobody is there to be asked.
        attended = getattr(real, "attended", None)
        if attended is not None:
            confirm.attended = attended  # type: ignore[attr-defined]

        sink = parent.emit

        def observer(kind: str, text: str, data: dict) -> None:
            # Activity only: the sub-agent's answer/error are not the turn's.
            if sink is None or kind not in ("tool", "tool_result", "thinking", "agent_state"):
                return
            data = {**data, "agent": name, "delegation": did}
            if data.get("call_id"):
                data["call_id"] = f"{did}:{data['call_id']}"
            sink(kind, f"[{name}] {text}" if text else "", data)

        child = Agent(
            source=parent.source,
            profile=sub_profile,
            thread_id=f"d:{parent.thread_id}" if parent.thread_id else None,
            observer=observer,
            confirmer=None if unattended else confirm,
            depth=1,
            max_rounds=int(config.get("agents.delegate_max_rounds", 25)),
            memory=memory,
        )
        # File checkpoints are keyed by this run and by the delegating owner.
        # `run_principal`, not `principal`: setting that would make the child
        # eligible for "Always" rules, which sub-agents must not be.
        child.run_id = did
        child.run_principal = parent.principal
        child.partial_on_budget = True
        child.token_budget = int(config.get("agents.delegate_token_budget", 60000))
    except ProfileError as exc:
        audit.record("delegate.refused", reason=str(exc), agent=name)
        text = f"Could not start sub-agent '{name}': {exc}"
        return text, Report(outcome="failed", summary=text)

    child.extra_instructions = REPORT_INSTRUCTION
    # A retry of a target the owner already declined is refused before any card
    # is shown, so the confirmer never sees it; the shared memory flag does.
    child.end_turn_check = lambda: state["declined"] or (
        memory.retried_declined and not base_retried) or (
        unattended and len(memory.targets) > base_targets
    )
    request = task + (f"\n\nContext from the Orchestrator:\n{context.strip()}" if context.strip() else "")

    with audit.context(delegation=did, agent=name, parent_source=parent.source):
        audit.record("agent.delegate_start", task=task[:500])
        turn = None
        crashed = ""
        try:
            turn = child.ask(request, stop)
        except Exception as exc:                      # never take the caller down
            crashed = f"{type(exc).__name__}: {exc}"

        report = (parse_report(turn.reply) if turn is not None else None) or None
        tools = list(turn.tool_calls) if turn is not None else []
        plan = [dict(i) for i in (child.tool_state.get("plan") or [])]
        budget_hit = bool(turn is not None and turn.budget_exhausted)
        if report is None or not report.summary:
            if budget_hit:
                report = _budget_report(turn, plan)
            else:
                report = _fallback(turn) if turn is not None else Report(
                    outcome="failed", summary=f"The sub-agent crashed: {crashed}")
        # The plan in the report is the one code holds. What is left is the
        # model's list if it gave one, else whatever the plan says is unfinished.
        report.plan = plan
        # Likewise the proposals it filed: code's list, so the Orchestrator can
        # tell the owner exactly what is waiting for them.
        report.proposals = [dict(p) for p in (child.tool_state.get("proposals") or [])]
        try:
            from . import checkpoints as _ck

            n = _ck.change_count(_ck.principal_of(child), did)
        except Exception:
            n = 0
        if n:
            report.undo = f"{did} ({n} file(s))"
        if not report.left and plan and (budget_hit or report.outcome != "done"):
            from . import plan as _plan

            report.left = _plan.remaining(plan)

        # The outcome is decided here, from what actually happened.
        if state["declined"] or (memory.retried_declined and not base_retried):
            report.outcome = "declined"
            report.summary = (
                f"The owner declined an action by {name}; the task was stopped. "
                + report.summary
            ).strip()
        elif unattended and len(memory.targets) > base_targets:
            report.outcome = "needs-approval"
            report.summary = (
                f"{name} needs the owner's approval for an action and nobody was "
                "available to ask; nothing was done for it. " + report.summary
            ).strip()
        elif crashed:
            report.outcome = "failed"
        elif turn is not None and turn.cancelled:
            report.outcome = "partial"
            why = "ran out of time" if stop.timed_out() and not stop.stopped_by_owner() \
                else "was stopped by the owner"
            report.summary = f"{name} {why}. " + report.summary
        elif budget_hit:
            # Rounds or tokens ran out: whatever was done is reported, not lost.
            report.outcome = "partial"
            if "stopped before finishing" not in report.summary.lower():
                report.summary = f"{name} {turn.budget_exhausted}. " + report.summary
        elif turn is not None and turn.error:
            report.outcome = "failed"
            report.summary = f"{turn.error} " + report.summary
        elif report.outcome not in OUTCOMES:
            report.outcome = "partial"

        audit.record("agent.delegate_end", outcome=report.outcome, tools=tools,
                     rounds=getattr(turn, "iterations", 0))
        if sink is not None:
            # The end of the delegation, with the outcome decided above: this is
            # what the UI shows (done / failed / declined), not the child's turn end.
            try:
                sink("agent_state", "", {
                    "phase": "delegate_end", "agent": name, "delegation": did,
                    "outcome": report.outcome, "summary": report.summary,
                })
            except Exception:
                pass
    return render(report, agent=name, delegation=did, tools=tools, limit=limit), report


@skill(name="delegate", category="agents", danger=Danger.SAFE)
def delegate(agent: str, task: str, context: str = "", wait: bool = False,
             _context: Any = None) -> str:
    """Hand a longer or uncertain task to a specialist sub-agent.

    Use it for multi-step work, research, shell, packages, services or bulk file
    work. By default the sub-agent works in the background: you get a task id
    at once and can keep helping the owner; the report arrives on its own as a
    "[task <id> report]" message, and task_status shows progress. The sub-agent
    asks the owner for confirmation itself; its raw tool output is not returned,
    only the report.

    Args:
        agent: Name of the sub-agent profile, for example system-ops.
        task: The whole task, self-contained: what to do and what done looks like.
        context: Facts from this conversation the sub-agent needs (optional).
        wait: True only if you need the report in this same turn; blocks until it ends.
    """
    parent = _context
    allowed = getattr(parent, "_allowed", None)
    profile = getattr(parent, "profile", None)
    if (
        profile is None or profile.name != ORCHESTRATOR or allowed is None
        or "delegate" not in allowed or getattr(parent, "depth", 1) != 0
    ):
        audit.record("delegate.refused", reason="caller is not the Orchestrator",
                     caller=getattr(profile, "name", None))
        return "delegate is only available to the Orchestrator."

    name = (agent or "").strip().lower()
    available = [n for n in list_profiles() if n != ORCHESTRATOR]
    if name not in available:
        return f"Unknown sub-agent '{agent}'. Available: {', '.join(available) or 'none'}."
    task = (task or "").strip()
    if not task:
        return "delegate needs a task."

    from . import tasks

    real = safety.get_confirmer()
    unattended = real is safety._deny              # nobody to ask: stay that way
    principal = str(getattr(parent, "principal", "") or "")
    host = tasks.get_host()
    wait = wait or bool(getattr(parent, "delegate_wait", False))
    if not wait and host is not None and principal:
        # Background: the parent's thread, confirmer and memory will be gone
        # when this runs, so only a snapshot crosses over.
        mem = safety.TurnMemory()
        mem.targets = set(safety.current_memory().targets)   # declined in this very turn
        snap = Parent(
            source=parent.source, thread_id=parent.thread_id, principal=principal,
            turn_no=getattr(parent, "turn_no", 0), origin=str(id(parent)), memory=mem,
        )
        return tasks.manager.submit(snap, name, task, context, unattended=unattended)

    parent_observer = getattr(parent, "observer", None)
    snap = Parent(
        source=parent.source, thread_id=parent.thread_id, principal=principal,
        turn_no=getattr(parent, "turn_no", 0), stop=getattr(parent, "_stop", None),
        emit=parent_observer, confirm=None if unattended else real,
        memory=safety.current_memory(),
    )
    rendered, _ = run_child(name, task, context, snap, secrets.token_hex(4))
    return rendered
