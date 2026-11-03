"""``update_plan``: a visible todo list that survives history trimming (#217).

The design reference is Claude Code's TodoWrite: items carry a content and a
status, and exactly one may be in progress. Claude Code enforces that in its
prompt only; here the skill rejects a bad call, with an error that tells the
model how to fix it.

Where the plan lives: ``Agent.tool_state["plan"]`` (per agent run, never
persisted, never in ``messages``). ``Agent._with_context`` renders it into the
volatile trailing ``[context]`` message on every round, so folding old turns
into the summary cannot lose it - the same reason the clock lives there.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from .. import audit
from ..registry import skill
from ..safety import Danger

STATUSES = ("pending", "in_progress", "completed")
MAX_ITEMS = 20
MAX_CONTENT = 300
_MARK = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}


class PlanItem(TypedDict):
    content: str
    status: Literal["pending", "in_progress", "completed"]


def validate(items: Any) -> tuple[list[dict[str, str]], str]:
    """``(plan, "")`` for a good call, ``([], error)`` for a bad one.

    Every error says what to change, because the reader is a model that will
    retry from the text alone.
    """
    if not isinstance(items, list):
        return [], ("items must be a list of {content, status} objects, "
                    "for example [{\"content\": \"Find the files\", \"status\": \"in_progress\"}].")
    if len(items) > MAX_ITEMS:
        return [], (f"A plan holds at most {MAX_ITEMS} items and you sent {len(items)}. "
                    "Merge related steps or drop the ones that no longer matter, then call update_plan again.")
    plan: list[dict[str, str]] = []
    for n, raw in enumerate(items, 1):
        if not isinstance(raw, dict):
            return [], f"Item {n} is not an object. Send {{\"content\": ..., \"status\": ...}} for every item."
        content = raw.get("content")
        if not isinstance(content, str) or not content.strip():
            return [], (f"Item {n} has no content. Give every item a short description of the step, "
                        "for example \"Rename the setting in config.py\".")
        content = " ".join(content.split())
        if len(content) > MAX_CONTENT:
            return [], (f"Item {n} is {len(content)} characters; keep each item under {MAX_CONTENT}. "
                        "Put detail in your notes, not in the plan.")
        status = str(raw.get("status") or "").strip().lower()
        if status not in STATUSES:
            return [], (f"Item {n} has status {raw.get('status')!r}. "
                        "Use exactly one of: pending, in_progress, completed.")
        plan.append({"content": content, "status": status})
    active = [n for n, item in enumerate(plan, 1) if item["status"] == "in_progress"]
    if len(active) > 1:
        which = ", ".join(str(n) for n in active)
        return [], (f"Only one item may be in_progress at a time; items {which} all are. "
                    "Mark the finished ones completed, leave the rest pending, "
                    "and call update_plan again with the whole list.")
    return plan, ""


def render(plan: list[dict[str, str]]) -> str:
    """The compact text form: one line per item, with a progress count."""
    done = sum(1 for i in plan if i["status"] == "completed")
    lines = [f"Plan ({done}/{len(plan)} done):"]
    lines += [f"{_MARK[i['status']]} {i['content']}" for i in plan]
    return "\n".join(lines)


def context_note(plan: list[dict[str, str]]) -> str:
    """The trailing ``[context]`` message that keeps the plan in front of the model."""
    if not plan:
        return ""
    return (
        "[context] Your current plan (kept in code, not in the conversation). "
        "Update it with update_plan as you go: keep exactly one item in_progress, "
        "and mark an item completed only after a tool result proves it.\n" + render(plan)
    )


def remaining(plan: list[dict[str, str]]) -> list[str]:
    """What is not finished yet, for a partial report."""
    return [i["content"] for i in plan if i["status"] != "completed"]


@skill(name="update_plan", category="agents", danger=Danger.SAFE)
def update_plan(items: list[PlanItem], _context: Any = None) -> str:
    """Keep a todo plan for a task of three or more steps; replaces the whole plan.

    Send every item each time, not only the changes. Exactly one item may be
    in_progress. Mark an item completed only when a tool result shows it is
    done. An empty list clears the plan.

    Args:
        items: The whole plan, in order. Each item is {content: short step description, status: pending | in_progress | completed}.
    """
    state = getattr(_context, "tool_state", None)
    if not isinstance(state, dict):
        return "update_plan is not available here: there is no agent run to keep a plan for."
    plan, error = validate(items)
    if error:
        return f"Plan not saved: {error}"
    if plan:
        state["plan"] = plan
    else:
        state.pop("plan", None)
    emit = getattr(_context, "_emit", None)
    if callable(emit):
        name = getattr(getattr(_context, "profile", None), "name", "") or "orchestrator"
        try:
            emit("agent_state", "", phase="plan", agent=name, plan=list(plan))
        except Exception:
            pass
    audit.record("agent.plan_updated", items=len(plan),
                 done=sum(1 for i in plan if i["status"] == "completed"))
    return render(plan) if plan else "Plan cleared."
