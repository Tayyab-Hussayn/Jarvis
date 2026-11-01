"""``delegate``: the Orchestrator hands a task to a sub-agent.

Safety model (this is the part that must not be "improved" casually):

* The sub-agent runs on the **caller's thread**, inside the caller's request.
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
from typing import Any

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

Say "done" only if a tool result proves it. Do not paste raw tool output.\
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


_HEAD = re.compile(r"^\s*(OUTCOME|SUMMARY|CHANGED|EVIDENCE|QUESTIONS)\s*:\s*(.*)$", re.I)


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
    )


def render(report: Report, *, agent: str, delegation: str, tools: list[str], limit: int) -> str:
    """The text the caller sees, capped at ``limit`` characters."""
    head = f"[{agent} report #{delegation}] outcome: {report.outcome}"
    parts = [f"Summary: {report.summary or '(none given)'}"]
    for label, rows in (("Changed", report.changed), ("Evidence", report.evidence),
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


@skill(name="delegate", category="agents", danger=Danger.SAFE)
def delegate(agent: str, task: str, context: str = "", _context: Any = None) -> str:
    """Hand a longer or uncertain task to a specialist sub-agent and get back a short report.

    Use it for multi-step work, research, shell, packages, services or bulk file
    work. The sub-agent asks the owner for confirmation itself; its raw tool
    output is not returned, only the report.

    Args:
        agent: Name of the sub-agent profile, for example system-ops.
        task: The whole task, self-contained: what to do and what done looks like.
        context: Facts from this conversation the sub-agent needs (optional).
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

    try:
        sub_profile = load_profile(name)
        from ..agent import Agent

        did = secrets.token_hex(4)
        limit = sub_profile.report_max_chars or int(config.get("agents.report_max_chars", 1500))
        deadline = time.monotonic() + float(config.get("agents.delegate_timeout_seconds", 300))
        stop = _LinkedStop(getattr(parent, "_stop", None), deadline)

        real = safety.get_confirmer()
        unattended = real is safety._deny          # nobody to ask: stay that way
        memory = safety.current_memory()
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

        parent_observer = getattr(parent, "observer", None)

        def observer(kind: str, text: str, data: dict) -> None:
            # Activity only: the sub-agent's answer/error are not the turn's.
            if parent_observer is None or kind not in ("tool", "tool_result", "thinking"):
                return
            data = {**data, "agent": name, "delegation": did}
            if data.get("call_id"):
                data["call_id"] = f"{did}:{data['call_id']}"
            parent_observer(kind, f"[{name}] {text}", data)

        child = Agent(
            source=parent.source,
            profile=sub_profile,
            thread_id=f"d:{parent.thread_id}" if parent.thread_id else None,
            observer=observer,
            confirmer=None if unattended else confirm,
            depth=1,
            max_rounds=int(config.get("agents.delegate_max_rounds", 6)),
            memory=memory,
        )
    except ProfileError as exc:
        audit.record("delegate.refused", reason=str(exc), agent=name)
        return f"Could not start sub-agent '{name}': {exc}"

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
        if report is None or not report.summary:
            fb = _fallback(turn) if turn is not None else Report(
                outcome="failed", summary=f"The sub-agent crashed: {crashed}")
            report = fb

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
        elif turn is not None and turn.error:
            report.outcome = "failed"
            report.summary = f"{turn.error} " + report.summary
        elif report.outcome not in OUTCOMES:
            report.outcome = "partial"

        audit.record("agent.delegate_end", outcome=report.outcome, tools=tools,
                     rounds=getattr(turn, "iterations", 0))
    return render(report, agent=name, delegation=did, tools=tools, limit=limit)
