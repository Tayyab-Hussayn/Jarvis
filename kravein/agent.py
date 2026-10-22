"""The agent loop.

Replaces ``KraveinLive`` in ``main.py``: takes a request, asks the model,
runs whatever skills it calls, feeds the results back, and repeats until the
model answers in prose or the iteration budget runs out.

The loop is deliberately transport-agnostic — the REPL, the daemon and any
future GUI all drive the same :class:`Agent`.
"""

from __future__ import annotations

import inspect
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from . import audit, config, registry, toolselect
from .llm import LLMError, ToolCall, client
from .platform import detect
from .safety import Confirmer, confirmer_scope

# Called with (kind, text) — or (kind, text, data) for observers that want the
# structured form. kind is one of: thinking, tool, tool_result, answer, error.
#
# The terminal REPL and the voice session only ever want the human-readable
# string, so the two-argument shape stays supported. The web transport needs
# the tool name and arguments as separate fields rather than re-parsing them
# out of a formatted string, which is what ``data`` carries.
Observer = Callable[..., None]


def _normalize_observer(fn: Observer | None) -> Callable[[str, str, dict], None] | None:
    """Adapt a 2- or 3-argument observer to a uniform 3-argument call.

    Arity is resolved once here rather than by catching ``TypeError`` at each
    emit — a TypeError raised *inside* a two-argument observer would otherwise
    be silently misread as "this one takes three arguments".
    """
    if fn is None:
        return None

    wants_data = True
    try:
        params = inspect.signature(fn).parameters
        positional = [
            p for p in params.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        has_varargs = any(p.kind is p.VAR_POSITIONAL for p in params.values())
        wants_data = has_varargs or len(positional) >= 3
    except (TypeError, ValueError):
        # Builtins and some C callables have no introspectable signature.
        wants_data = False

    if wants_data:
        return fn
    return lambda kind, text, data: fn(kind, text)


SYSTEM_PROMPT = """\
You are {name}, a Linux system assistant with real control over this machine.

You are direct, competent and concise — think a capable systems engineer, not \
a chatbot. Answer in plain language. Do not pad, do not moralise, and do not \
narrate what you are about to do unless the user asks.

## Using tools
- You have real tools. Use them. Never claim you did something without calling \
the matching tool, and never invent output you did not receive.
- Prefer one precise tool call over several speculative ones.
- If a tool reports it is unavailable, say so plainly and give the install \
command it suggested. Do not silently try a worse approach.
- After a tool returns, answer the user's actual question using its output. \
Do not simply restate the raw output unless it is already the answer.

## Never overclaim
- Report only what a tool result actually confirms. If no tool result says an \
action happened, do not say it happened.
- If the user asks for something you have no tool for, say plainly that you \
cannot do it. Do NOT substitute a loosely related tool and describe the result \
as if it were the thing they asked for. Opening a URL is not sending an email; \
listing files is not cleaning them up.
- Words like "Done", "Sent" or "Created" require a tool result proving it. \
Otherwise say what you actually did, or that you could not.

## Know when to stop
- Investigate with a few targeted calls, then answer. You have a limited number \
of tool rounds and running out means the user gets nothing at all.
- Never call the same tool repeatedly on different inputs hoping something \
turns up. If two or three calls have not answered the question, report what you \
found and say what is still unknown.
- For a broad or vague request, gather a small amount of evidence and come back \
with findings and a suggested next step. Do not try to resolve the whole thing \
in one turn.

## Safety
- Destructive actions (deleting, killing processes, shutting down, installing \
packages) will pause and ask the user to confirm. That is expected. Never try \
to work around a confirmation, and never pass confirm=true unless the user has \
clearly already agreed to that specific action in this conversation.
- If an action is refused for safety, explain why and stop. Do not look for a \
way around it.

## This machine
{environment}

The user's home directory is {home}.
"""


@dataclass
class Turn:
    """The outcome of one user request."""

    reply: str = ""
    tool_calls: list[str] = field(default_factory=list)
    iterations: int = 0
    error: str = ""
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return not self.error


class Agent:
    """Stateful conversation with tool access."""

    def __init__(
        self,
        *,
        source: str = "local",
        observer: Observer | None = None,
        confirmer: Confirmer | None = None,
        max_history: int = 40,
    ):
        self.source = source
        self.observer = _normalize_observer(observer)
        self.confirmer = confirmer
        self.max_history = max_history
        self.messages: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._llm = client()
        # Skill categories this conversation has used or asked for. They stay
        # exposed on later turns so a follow-up ("now close it") is not shown
        # a toolset narrowed to its own wording. None of this is read unless
        # tool routing is on.
        self._tool_categories: set[str] = set()
        self._turn_categories: set[str] | None = None

    # -- helpers ---------------------------------------------------------

    def _emit(self, kind: str, text: str, **data: Any) -> None:
        if self.observer is not None:
            try:
                self.observer(kind, text, data)
            except Exception:
                pass

    def system_prompt(self) -> str:
        env = detect.environment()
        from .skills.memory import context_for_prompt

        prompt = SYSTEM_PROMPT.format(
            name=config.get("assistant_name", "Kravein"),
            environment=env.summary(),
            home=config.HOME,
        )

        memory = context_for_prompt()
        if memory:
            prompt += "\n\n" + memory
        return prompt

    def _with_context(self) -> list[dict[str, Any]]:
        """The outgoing messages plus volatile context, appended last.

        The model needs to know what time it is — otherwise "5 PM tomorrow"
        gets resolved against whatever date it believes today to be, which is
        its training cutoff. The obvious place to put a clock is the system
        prompt, and that is the wrong place: the system prompt is the stable
        cached prefix, and a value that changes every second would invalidate
        it on every single request.

        So it goes at the *end* instead. Everything before it is untouched, so
        the cached prefix still matches; only the tail differs, which it does
        every turn regardless.

        It is deliberately not stored in `self.messages`. History replays on
        later turns, and a timestamp baked into it would age into a lie.
        """
        from datetime import datetime

        try:
            stamp = datetime.now().astimezone()
            zone = stamp.tzname() or "local"
            note = (
                f"[context] Current date and time: {stamp.isoformat(timespec='seconds')} "
                f"({zone}). Use this to resolve any relative date or time — "
                "'tomorrow', 'next Tuesday', 'in two weeks' — into an absolute "
                "ISO 8601 timestamp before passing it to a tool."
            )
        except Exception:
            return list(self.messages)

        return [*self.messages, {"role": "user", "content": note}]

    def reset(self) -> None:
        """Clear the conversation history."""
        with self._lock:
            self.messages.clear()
            self._tool_categories.clear()
        audit.record("agent.reset", source=self.source)

    def _trim_history(self) -> None:
        if len(self.messages) <= self.max_history:
            return
        # Drop from the front, but never orphan a tool result from its call.
        excess = len(self.messages) - self.max_history
        cut = excess
        while cut < len(self.messages) and self.messages[cut].get("role") == "tool":
            cut += 1
        del self.messages[:cut]

    # -- tool routing ----------------------------------------------------

    def _routing_on(self) -> bool:
        return str(config.get("llm.tool_routing", "off")).lower() == "auto"

    def enable_categories(self, wanted: list[str]) -> None:
        """Add categories for the rest of this turn (called by ``more_tools``)."""
        self._tool_categories.update(wanted)
        if self._turn_categories is not None:
            self._turn_categories.update(wanted)

    def _tools_for_turn(self, text: str) -> list[dict[str, Any]]:
        """Tool schemas for this turn; every skill unless routing narrows it."""
        if not self._routing_on():
            self._turn_categories = None
            return registry.tool_declarations()
        chosen = toolselect.select(text, self._tool_categories)
        self._turn_categories = set(chosen) if chosen is not None else None
        # `more_tools` only means something when the list was narrowed.
        decls = registry.tool_declarations(
            categories=chosen, routing=chosen is not None
        )
        audit.record(
            "agent.tools_routed", source=self.source,
            categories=sorted(chosen) if chosen is not None else "all",
            tools=len(decls),
        )
        return decls

    def _current_tools(self, text: str, cached: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Reuse the turn's list, rebuilding only if ``more_tools`` widened it.

        Rebuilding on every round would be harmless but pointless; the list
        stays byte-identical between rounds so the provider's prompt cache
        keeps matching until the model actually asks for something new.
        """
        if self._turn_categories is None:
            return cached
        wanted = len({d["name"] for d in cached})
        fresh = registry.tool_declarations(
            categories=self._turn_categories, routing=True
        )
        return fresh if len(fresh) != wanted else cached

    # -- main loop -------------------------------------------------------

    def ask(self, text: str, stop: threading.Event | None = None) -> Turn:
        """Handle one user request, running tools as needed.

        ``stop`` makes the turn interruptible. The loop runs on a worker
        thread, so cancelling the asyncio task that wraps it does nothing —
        the thread keeps going through all eight tool rounds. Stopping has to
        be cooperative, which means checking a flag at the points where the
        loop is between things:

        * before asking the model again,
        * before each tool call,
        * after each tool result.

        What it cannot do is interrupt work already in flight — an HTTP
        request to a provider, or a tool that is mid-execution. Worst case you
        wait for the current one to finish, and nothing further is started.
        Claiming otherwise would be the kind of "it stopped" that is a lie.
        """
        text = (text or "").strip()
        if not text:
            return Turn(reply="")

        max_iterations = int(config.get("llm.max_tool_iterations", 8))
        turn = Turn()

        def cancelled() -> bool:
            return stop is not None and stop.is_set()

        def give_up(where: str) -> Turn:
            turn.cancelled = True
            turn.error = "Stopped at your request."
            self._emit("cancelled", turn.error)
            audit.record(
                "agent.cancelled", source=self.source,
                at=where, iterations=turn.iterations,
                tool_calls=len(turn.tool_calls),
            )
            return turn

        audit.record("agent.request", source=self.source, text=text)

        with self._lock:
            self.messages.append({"role": "user", "content": text})
            self._trim_history()

            tools = self._tools_for_turn(text)
            system = self.system_prompt()

            # The confirmer must be installed for the whole loop, since skills
            # run on this thread and read it from thread-local state.
            with confirmer_scope(self.confirmer):
                for iteration in range(1, max_iterations + 1):
                    turn.iterations = iteration
                    if cancelled():
                        return give_up("before_model")

                    try:
                        tools = self._current_tools(text, tools)
                        reply = self._llm.complete(
                            self._with_context(), system=system, tools=tools
                        )
                    except LLMError as exc:
                        turn.error = str(exc)
                        self._emit("error", turn.error)
                        audit.record("agent.llm_failed", source=self.source, error=str(exc))
                        return turn

                    if not reply.wants_tools:
                        turn.reply = reply.text or "(no reply)"
                        self.messages.append(
                            {"role": "assistant", "content": turn.reply}
                        )
                        self._emit("answer", turn.reply)
                        audit.record(
                            "agent.answer", source=self.source,
                            iterations=iteration, chars=len(turn.reply),
                        )
                        return turn

                    # Record the assistant's tool-call turn before running it,
                    # so the transcript stays valid for the next request.
                    self.messages.append({
                        "role": "assistant",
                        "content": reply.text,
                        "tool_calls": [
                            {
                                "id": c.id,
                                "name": c.name,
                                "arguments": c.arguments,
                                # Gemini 3.x requires this echoed back.
                                "signature": c.signature,
                            }
                            for c in reply.tool_calls
                        ],
                    })

                    if reply.text:
                        self._emit("thinking", reply.text)

                    for call in reply.tool_calls:
                        if cancelled():
                            # The assistant's tool-call message is already in
                            # history, so every call it named needs a result or
                            # the transcript is malformed for the next request.
                            # Say plainly that it did not run.
                            self.messages.append({
                                "role": "tool",
                                "name": call.name,
                                "tool_call_id": call.id or call.name,
                                "content": "Not run — the user stopped the request.",
                            })
                            continue
                        result = self._run_tool(call)
                        turn.tool_calls.append(call.name)
                        self.messages.append({
                            "role": "tool",
                            "name": call.name,
                            "tool_call_id": call.id or call.name,
                            "content": result,
                        })

                    if cancelled():
                        return give_up("after_tools")

                # Budget exhausted.
                turn.error = (
                    f"Stopped after {max_iterations} tool rounds without a final "
                    "answer. The request may be too broad — try narrowing it."
                )
                self._emit("error", turn.error)
                audit.record("agent.budget_exhausted", source=self.source)
                return turn

    def _run_tool(self, call: ToolCall) -> str:
        """Execute one tool call and return its textual result."""
        args_text = ", ".join(f"{k}={v!r}" for k, v in list(call.arguments.items())[:4])
        call_id = call.id or call.name
        self._emit(
            "tool", f"{call.name}({args_text})",
            name=call.name, args=call.arguments, call_id=call_id,
        )

        result = registry.dispatch(
            call.name,
            call.arguments,
            source=self.source,
            context=self,
        )
        used = registry.get_skill(call.name)
        if used is not None and used.category != "tools":
            self._tool_categories.add(used.category)
        self._emit(
            "tool_result", result,
            name=call.name, call_id=call_id, result=result,
        )
        return result


def one_shot(text: str, *, source: str = "cli", confirmer: Confirmer | None = None) -> str:
    """Run a single request with no persistent history."""
    agent = Agent(source=source, confirmer=confirmer)
    turn = agent.ask(text)
    return turn.reply or turn.error
