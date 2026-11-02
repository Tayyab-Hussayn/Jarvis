"""Live state of the Orchestrator and its sub-agents, for the v2 UI (#146).

One tracker per owner principal (the browser, or one paired phone), fed from
the agent observer path that already exists: ``Agent`` emits ``tool`` /
``tool_result`` and the new ``agent_state`` kind (turn and model-call
boundaries), ``delegate`` forwards its child's events tagged with
``agent`` / ``delegation`` and ends each delegation with ``delegate_end``,
and ``Session.confirm`` reports a card opening and closing. There is no
second event system.

The tracker holds no tool arguments and no tool output: a frame carries a
state, the skill name being run, a task line cut to ``TASK_MAX`` characters
and a one-line report. The task text is the owner's own request or the
Orchestrator's delegation text, already visible in the chat.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from .. import config

ORCHESTRATOR = "orchestrator"
TASK_MAX = 200
SUMMARY_MAX = 200

# A delegation outcome -> the state the agent ends in. The report keeps the
# real outcome, so "partial" and "needs-approval" are not lost.
_END_STATE = {"done": "done", "declined": "declined", "needs-approval": "declined"}


def clip(text: Any, limit: int = TASK_MAX) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class StateTracker:
    """Per-agent state for one principal. Thread-safe; ``emit(frame_fields)``
    is called (outside the lock) for every change."""

    def __init__(self, emit: Callable[[dict[str, Any]], None]) -> None:
        self._emit = emit
        self._lock = threading.Lock()
        self._states: dict[str, dict[str, Any]] = {}
        self._waiting_prev: dict[str, tuple[str, str]] = {}
        self._born = time.time()

    # -- feeding ----------------------------------------------------------

    def handle(self, kind: str, text: str, data: dict[str, Any]) -> None:
        """One agent observer event. Unknown kinds are ignored."""
        agent = str(data.get("agent") or ORCHESTRATOR)
        delegation = str(data.get("delegation") or "")
        if kind == "tool":
            self._set(agent, "working", tool=str(data.get("name") or ""), delegation=delegation)
        elif kind == "tool_result":
            self._set(agent, "thinking", delegation=delegation)
        elif kind == "agent_state":
            phase = data.get("phase")
            if phase in ("turn_start", "llm_start"):
                task = clip(data["task"]) if phase == "turn_start" and data.get("task") else None
                self._set(agent, "thinking", task=task, delegation=delegation)
            elif phase == "turn_end" and not delegation:
                # A sub-agent's turn ends inside a delegation; `delegate_end`
                # carries the outcome the owner cares about.
                self._finish(agent, str(data.get("outcome") or "done"), data.get("summary"), "")
            elif phase == "delegate_end":
                self._finish(agent, str(data.get("outcome") or "failed"),
                             data.get("summary"), delegation)

    def waiting(self, on: bool) -> None:
        """A confirmation card opened (``on``) or closed for the agent working now."""
        frames: list[dict[str, Any]] = []
        with self._lock:
            if on:
                live = [(n, s) for n, s in self._states.items()
                        if s["state"] in ("thinking", "working")]
                if not live:
                    return
                # The deepest agent (inside a delegation) asked, else the newest.
                name, st = max(live, key=lambda p: (bool(p[1].get("delegation")), p[1]["since"]))
                self._waiting_prev[name] = (st["state"], st.get("tool", ""))
                frames.append(self._apply(name, "waiting", keep=True))
            else:
                for name, (prev, tool) in list(self._waiting_prev.items()):
                    st = self._states.get(name)
                    if st and st["state"] == "waiting":
                        frames.append(self._apply(name, prev, tool=tool, keep=True))
                    self._waiting_prev.pop(name, None)
        self._send(frames)

    # -- reading ----------------------------------------------------------

    def snapshot(self, *, seen_only: bool = False) -> list[dict[str, Any]]:
        """Every known agent, idle ones included, so a reconnecting client is
        correct at once. ``seen_only`` lists just the agents that have had any
        state (what a websocket replays: nothing at all before the first turn,
        so a quiet connection stays quiet)."""
        with self._lock:
            known = {n: dict(s) for n, s in self._states.items()}
        if seen_only:
            return list(known.values())
        out = []
        for name in agent_names():
            out.append(known.pop(name, None) or {"agent": name, "state": "idle", "since": self._born})
        out.extend(known.values())      # e.g. a profile removed mid-run
        return out

    # -- internals --------------------------------------------------------

    def _apply(self, name: str, state: str, *, tool: str = "", task: str | None = None,
               delegation: str = "", report: dict | None = None, keep: bool = False) -> dict[str, Any]:
        """Update under the lock; returns the frame fields."""
        prev = self._states.get(name, {})
        new: dict[str, Any] = {"agent": name, "state": state, "since": time.time()}
        if keep:
            tool = tool or prev.get("tool", "")
            delegation = delegation or prev.get("delegation", "")
            task = prev.get("task") if task is None else task
        else:
            task = prev.get("task") if task is None and state in ("thinking", "working") else task
        if state == "working" and tool:
            new["tool"] = tool
        elif state in ("thinking", "waiting") and tool and keep:
            new["tool"] = tool
        if task and state in ("thinking", "working", "waiting"):
            new["task"] = task
        if delegation and state != "idle":
            new["delegation"] = delegation
        if report is not None:
            new["report"] = report
        elif prev.get("report") and state == "idle":
            new["report"] = prev["report"]      # the last report stays readable
        self._states[name] = new
        return dict(new)

    def _set(self, name: str, state: str, *, tool: str = "", task: str | None = None,
             delegation: str = "") -> None:
        with self._lock:
            prev = self._states.get(name)
            if prev and prev["state"] == state and prev.get("tool", "") == tool and task is None:
                return                                  # no change, no frame
            frame = self._apply(name, state, tool=tool, task=task, delegation=delegation)
        self._send([frame])

    def _finish(self, name: str, outcome: str, summary: Any, delegation: str) -> None:
        end = _END_STATE.get(outcome, "failed")
        report = {"outcome": outcome, "summary": clip(summary, SUMMARY_MAX)}
        with self._lock:
            self._waiting_prev.pop(name, None)
            done = self._apply(name, end, delegation=delegation, report=report)
            idle = self._apply(name, "idle")
        self._send([done, idle])

    def _send(self, frames: list[dict[str, Any]]) -> None:
        for fields in frames:
            try:
                self._emit(fields)
            except Exception:
                pass            # a dead socket must never stall the agent


def agent_names() -> list[str]:
    """The agents the UI shows: just the default one with the switch off."""
    if not config.get("agents.orchestrator", False):
        return [ORCHESTRATOR]
    from . import list_profiles
    return [ORCHESTRATOR] + [n for n in list_profiles() if n != ORCHESTRATOR]


def agent_list() -> dict[str, Any]:
    """``GET /api/agents``: built from the profiles on disk. No instructions,
    no tool names - only a count."""
    from .. import registry
    from . import load_profile

    enabled = bool(config.get("agents.orchestrator", False))
    agents: list[dict[str, Any]] = []
    if not enabled:
        agents.append({
            "name": ORCHESTRATOR, "purpose": "Kravein, the single default assistant.",
            "model": "", "tools": len(registry.tool_declarations()), "role": "orchestrator",
        })
        return {"agents": agents, "orchestrator_enabled": False}
    for name in agent_names():
        try:
            p = load_profile(name)
        except Exception:
            continue                    # a broken profile must not break the list
        agents.append({
            "name": name, "purpose": p.purpose, "model": p.model,
            "tools": len(registry.resolve_tools(p.tools)),
            "role": "orchestrator" if name == ORCHESTRATOR else "sub",
        })
    return {"agents": agents, "orchestrator_enabled": True}
