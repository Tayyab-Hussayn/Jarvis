"""The agent loop.

Replaces ``KraveinLive`` in ``main.py``: takes a request, asks the model,
runs whatever skills it calls, feeds the results back, and repeats until the
model answers in prose or the iteration budget runs out.

The loop is deliberately transport-agnostic — the REPL, the daemon and any
future GUI all drive the same :class:`Agent`.
"""

from __future__ import annotations

import inspect
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from . import audit, config, registry, toolselect
from .agents import AgentProfile, ProfileError, history as _history, load_profile
from .llm import LLMError, ToolCall, client
from .platform import detect
from .safety import Confirmer, TurnMemory, confirmer_scope, retried_declined_target

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
to work around a confirmation.
- If an action is refused for safety, explain why and stop. Do not look for a \
way around it. Never leave it at a bare refusal: say in a sentence or two what \
the risk is and offer the nearest safer way to get what the user wanted.
- An approval covers only the action it was given for. Anything beyond that \
scope needs its own approval.

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
    # Why a delegated run ran out of rounds or tokens ("" = it did not). Set only
    # when `Agent.partial_on_budget` is on; the top-level loop still sets `error`.
    budget_exhausted: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


DECLINED_REPLY = "Okay, I won't do that."
ORCHESTRATOR = "orchestrator"

# Old turns are folded into a rolling summary by one tool-less model call.
SUMMARY_SYSTEM = (
    "You compress an assistant's conversation history. Write a compact summary "
    "of the PREVIOUS SUMMARY plus the NEW MESSAGES: what the user asked, what "
    "was actually done (only what a tool result confirmed), facts and "
    "preferences worth remembering, and anything still unresolved. Plain text, "
    "no preamble, under 250 words."
)
SUMMARY_MAX_CHARS = 3000
_SUMMARY_INPUT_MAX_CHARS = 24000
# After a trim the history is cut down to this share of the budget, so the
# next turns do not trigger another summary call straight away.
_TRIM_TARGET = 0.6


def estimate_tokens(message: dict[str, Any]) -> int:
    """Rough token count (chars / 4). Providers differ; this only has to be
    stable and in the right range to decide when to trim."""
    chars = len(str(message.get("content") or ""))
    calls = message.get("tool_calls")
    if calls:
        chars += len(json.dumps(calls, default=str))
    return chars // 4 + 4


class Agent:
    """Stateful conversation with tool access."""

    # Class-level defaults: some tests build an Agent without __init__.
    profile: "AgentProfile | None" = None
    _allowed: "frozenset[str] | None" = None
    thread_id: str | None = None
    summary: str = ""
    max_history: int = 0
    depth: int = 0                      # 0 = talks to the owner, 1 = a delegated sub-agent
    max_rounds: int = 0                 # 0 = llm.max_tool_iterations
    extra_instructions: str = ""        # appended to the system prompt (delegate's report format)
    _round_allowed: "frozenset[str] | None" = None  # this round's narrowed tool set (escalation)
    _stop: Any = None                   # the current turn's stop flag, for delegate to link
    end_turn_check: Any = None          # delegate: callable -> True ends the turn (owner declined, unattended refusal)
    _memory: "TurnMemory | None" = None  # shared denial memory (a sub-agent shares its parent's)
    _stamp: float = 0.0                 # store stamp at our last load/save (see _refresh)
    principal: str = ""                 # who owns this conversation (set by the web server)
    run_id: str = ""                    # delegated child: its delegation/task id (checkpoints are keyed by it)
    run_principal: str = ""             # delegated child: the delegating owner's principal (NOT `principal`)
    partial_on_budget: bool = False    # delegated run: out of rounds/tokens = a partial report, not an error (#217)
    token_budget: int = 0               # 0 = none; else the estimated tokens a run may add to its transcript
    delegate_wait: bool = False         # force blocking delegate (an approved proposal's turn needs the real outcome)
    _turn_allowed: "frozenset[str] | None" = None  # this ask()'s own tool narrowing (report follow-up)

    def __init__(
        self,
        *,
        source: str = "local",
        observer: Observer | None = None,
        confirmer: Confirmer | None = None,
        max_history: int = 0,
        profile: "AgentProfile | str | None" = None,
        thread_id: str | None = None,
        depth: int = 0,
        max_rounds: int = 0,
        memory: "TurnMemory | None" = None,
    ):
        """``profile`` restricts this agent to that profile's tools (enforced in
        ``registry.dispatch``) and adds its instructions to the base prompt;
        ``None`` is the all-tools agent every transport used before.
        ``thread_id`` makes the history persistent (SQLite, keyed by profile
        and thread); without it the history lives in memory only.
        ``max_history`` is an optional extra cap on message count; the real
        limit is the token budget ``llm.history_token_budget``."""
        if isinstance(profile, str):
            profile = load_profile(profile)
        self.profile = profile
        self._allowed: frozenset[str] | None = None
        if profile is not None:
            try:
                self._allowed = registry.resolve_tools(profile.tools)
            except ValueError as exc:
                raise ProfileError(f"profile '{profile.name}': {exc}") from None
        # Nested delegation is impossible by construction: only the Orchestrator
        # profile may hold `delegate`, and never below depth 0. The prompt says
        # so too, but this is the enforcement.
        if self._allowed is not None and "delegate" in self._allowed:
            if profile.name != ORCHESTRATOR or depth > 0:
                raise ProfileError(
                    f"profile '{profile.name}' may not use delegate "
                    "(only the Orchestrator, and never as a sub-agent)"
                )
        self.depth = depth
        # Per-agent-run scratch for skills (read tracking, shell cwd). One dict
        # per Agent instance, so a delegated child starts empty and cannot see
        # or forge its parent's state. Never persisted, never shown to the model.
        self.tool_state: dict[str, Any] = {}
        self.max_rounds = max_rounds
        self._memory = memory
        self.thread_id = thread_id
        self.summary = ""
        self.source = source
        self.observer = _normalize_observer(observer)
        self.confirmer = confirmer
        self.max_history = max_history
        self.messages: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._llm = client()
        if profile is not None and profile.model:
            known = self._llm.known_providers()   # built-ins + anthropic + endpoints (#231)
            if profile.model.partition(":")[0] not in known:
                raise ProfileError(
                    f"profile '{profile.name}': unknown provider in model "
                    f"'{profile.model}' (known: {', '.join(sorted(known))})"
                )
        # Skill categories this conversation has used or asked for. They stay
        # exposed on later turns so a follow-up ("now close it") is not shown
        # a toolset narrowed to its own wording. None of this is read unless
        # tool routing is on.
        self._tool_categories: set[str] = set()
        self._turn_categories: set[str] | None = None
        if thread_id:
            try:
                self._stamp = _history.stamp(self._history_key, thread_id)
                self.messages, self.summary = _history.load(self._history_key, thread_id)
                self._note_seen()
            except Exception as exc:
                # A broken history file must not stop the assistant starting.
                audit.record("agent.history_load_failed", error=str(exc),
                             profile=self._history_key)

    @property
    def _history_key(self) -> str:
        return self.profile.name if self.profile else "default"

    def _persist(self) -> None:
        if not self.thread_id:
            return
        with self._lock:
            try:
                self._keep_foreign_notes()
                self._stamp = _history.save(self._history_key, self.thread_id,
                                            self.messages, self.summary)
            except Exception as exc:
                audit.record("agent.history_save_failed", error=str(exc),
                             profile=self._history_key)

    def _keep_foreign_notes(self) -> None:
        """Before a save: keep notes another writer appended during this turn.

        A turn that was already running when a task report (or a spoken turn)
        was appended would otherwise replace the thread and erase it. Messages
        carry an ``xid`` when written by ``history.append``; any we have never
        seen is added to the end of ours. Ones we already saw (and perhaps
        trimmed into the summary) are not resurrected.
        """
        if self.depth or _history.stamp(self._history_key, self.thread_id) == self._stamp:
            return
        stored, _ = _history.load(self._history_key, self.thread_id)
        mine = {m.get("xid") for m in self.messages if m.get("xid")}
        seen = self.__dict__.setdefault("_seen_xids", set())
        extra = [m for m in stored if m.get("xid") and m["xid"] not in mine
                 and m["xid"] not in seen]
        if extra:
            self.messages.extend(extra)
            self._note_seen()
            audit.record("agent.history_foreign_kept", count=len(extra),
                         profile=self._history_key)

    def _note_seen(self) -> None:
        self.__dict__.setdefault("_seen_xids", set()).update(
            m["xid"] for m in self.messages if m.get("xid"))

    def _refresh(self) -> None:
        """Pick up notes another writer appended to this thread (task reports).

        They go to the store, not to this list, so without this the next
        ``_persist`` would replace the thread and erase them. Only a changed
        stamp reloads, so an agent built around a hand-set ``messages`` list
        (tests) is left alone.
        """
        if not self.thread_id or self.depth:
            return
        try:
            stamp = _history.stamp(self._history_key, self.thread_id)
            if stamp != self._stamp:
                self.messages, self.summary = _history.load(self._history_key, self.thread_id)
                self._stamp = stamp
                self._note_seen()
        except Exception as exc:
            audit.record("agent.history_load_failed", error=str(exc), profile=self._history_key)

    # -- helpers ---------------------------------------------------------

    @property
    def model_role(self) -> str:
        """``models.roles`` key for this agent's turns (#228)."""
        if self.profile is None or self.profile.name == ORCHESTRATOR:
            return "orchestrator"
        return f"worker:{self.profile.name}"

    def _usage_tags(self) -> dict[str, str]:
        """Usage-ledger labels. A delegated child is billed to its owner."""
        return {
            "principal": self.principal or self.run_principal or "",
            "thread": self.thread_id or "",
            "agent": self.profile.name if self.profile is not None else "default",
        }

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

        if self.profile is not None:
            prompt += (
                f"\n\n## Your role: {self.profile.name}\n{self.profile.purpose}"
            )
            if self.profile.instructions:
                prompt += "\n\n" + self.profile.instructions

        owner = config.owner_instructions_block()
        if owner:
            prompt += "\n\n" + owner

        if self.extra_instructions:
            prompt += "\n\n" + self.extra_instructions

        memory = context_for_prompt()
        if memory:
            prompt += "\n\n" + memory
        return prompt

    def _with_context(self, extra: str = "") -> list[dict[str, Any]]:
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
            note = ""

        # The plan (update_plan) rides in the tail for the same reason the clock
        # does: history trimming cannot lose it, and it is never stored.
        from .agents import plan as _plan

        plan_note = _plan.context_note(getattr(self, "tool_state", {}).get("plan") or [])
        tail = [{"role": "user", "content": n} for n in (note, plan_note, extra) if n]
        return [*self._history_messages(), *tail]

    def _history_messages(self) -> list[dict[str, Any]]:
        """History as sent: the rolling summary (if any) leads."""
        if not self.summary:
            return list(self.messages)
        lead = {
            "role": "user",
            "content": "[summary of the earlier conversation]\n" + self.summary,
        }
        return [lead, *self.messages]

    def reset(self) -> None:
        """Clear the conversation history."""
        with self._lock:
            self.messages.clear()
            self.summary = ""
            self._tool_categories.clear()
            if self.thread_id:
                try:
                    _history.delete(self._history_key, self.thread_id)
                except Exception as exc:
                    audit.record("agent.history_save_failed", error=str(exc))
        audit.record("agent.reset", source=self.source)

    def _trim_history(self) -> None:
        """Keep the history inside the token budget.

        The oldest whole turns are folded into the rolling summary; if the
        summary call fails the same turns are simply dropped. A cut is only
        ever made at a user message, so a tool call is never separated from
        its result, and the current turn (the last user message) is never cut.
        """
        budget = int(config.get("llm.history_token_budget", 24000))
        cap = self.max_history
        sizes = [estimate_tokens(m) for m in self.messages]
        used = sum(sizes) + len(self.summary) // 4
        over_count = bool(cap) and len(self.messages) > cap
        if not over_count and (budget <= 0 or used <= budget):
            return

        users = [i for i, m in enumerate(self.messages) if m.get("role") == "user"]
        if len(users) < 2:
            return  # one turn: nothing older to fold; tool results are capped
        target = int(budget * _TRIM_TARGET) - len(self.summary) // 4
        cut = users[-1]
        for i in users:
            if i == 0:
                continue
            fits_budget = budget <= 0 or sum(sizes[i:]) <= target
            fits_count = not cap or len(self.messages) - i <= cap
            if fits_budget and fits_count:
                cut = i
                break

        dropped = self.messages[:cut]
        summary = self._summarize(dropped)
        if summary:
            self.summary = summary
        del self.messages[:cut]
        audit.record(
            "agent.history_trimmed", source=self.source, dropped=len(dropped),
            summarized=bool(summary), kept=len(self.messages), tokens_before=used,
        )

    def _summarize(self, dropped: list[dict[str, Any]]) -> str:
        """Fold ``dropped`` into the rolling summary; ``""`` on any failure."""
        lines: list[str] = []
        for m in dropped:
            role = m.get("role", "?")
            text = str(m.get("content") or "")[:600]
            if m.get("tool_calls"):
                names = ", ".join(c.get("name", "?") for c in m["tool_calls"])
                text = (text + f" [called: {names}]").strip()
            if text:
                lines.append(f"{role}: {text}")
        transcript = "\n".join(lines)[-_SUMMARY_INPUT_MAX_CHARS:]
        prompt = (
            f"PREVIOUS SUMMARY:\n{self.summary or '(none)'}\n\nNEW MESSAGES:\n{transcript}"
        )
        try:
            reply = self._llm.complete(
                [{"role": "user", "content": prompt}], system=SUMMARY_SYSTEM, tools=None,
                role="utility", tags=self._usage_tags(),
            )
            text = (reply.text or "").strip()
            wants_tools = reply.wants_tools
        except Exception as exc:
            audit.record("agent.summary_failed", source=self.source, error=str(exc))
            return ""
        if not text or wants_tools:
            audit.record("agent.summary_failed", source=self.source, error="empty reply")
            return ""
        return text[:SUMMARY_MAX_CHARS]

    @staticmethod
    def _cap_result(name: str, result: str) -> str:
        """Cap a tool result for the history; the audit log keeps the full text."""
        limit = int(config.get("llm.tool_result_max_chars", 12000))
        if limit <= 0 or len(result) <= limit:
            return result
        audit.record(
            "agent.tool_result_truncated", tool=name, chars=len(result), kept=limit,
        )
        return (
            result[:limit]
            + f"\n[… cut: {len(result) - limit} of {len(result)} characters "
            "not shown; the full result is in the audit log]"
        )

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
        if self._allowed is not None:
            # A profile's tool set is fixed; routing would only fight it.
            self._turn_categories = None
            return registry.tool_declarations(names=self._allowed)
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

    # -- escalation ------------------------------------------------------

    def _can_escalate(self) -> bool:
        """An Orchestrator with someone to hand the task to."""
        if self._allowed is None or "delegate" not in self._allowed or self.depth:
            return False
        from .agents import list_profiles

        return any(n != ORCHESTRATOR for n in list_profiles())

    # -- main loop -------------------------------------------------------

    def ask(self, text: str, stop: threading.Event | None = None, *,
            allowed: "frozenset[str] | None" = None) -> Turn:
        """Handle one user request (see ``_ask``) and persist the history.

        ``allowed`` narrows this one turn's tools below the profile's (schema
        and dispatch both). The follow-up that announces a task report uses it:
        a report is text a sub-agent produced, so the turn that reads it may
        speak but must not act.
        """
        name = self.profile.name if self.profile is not None else "orchestrator"
        with self._lock:
            self._refresh()
        if allowed is not None:
            allowed = frozenset(allowed)
            if self._allowed is not None:
                allowed &= self._allowed       # narrowing only, never widening
        self._turn_allowed = allowed
        self._emit("agent_state", "", phase="turn_start", agent=name, task=text)
        turn: Turn | None = None
        try:
            turn = self._ask(text, stop)
            return turn
        finally:
            outcome, summary = "failed", "The turn crashed."
            if turn is not None:
                if turn.cancelled:
                    outcome, summary = "failed", "Stopped at your request."
                elif turn.error:
                    outcome, summary = "failed", turn.error
                elif turn.reply == DECLINED_REPLY:
                    outcome, summary = "declined", turn.reply
                else:
                    outcome, summary = "done", turn.reply
            self._emit("agent_state", "", phase="turn_end", agent=name,
                       outcome=outcome, summary=summary)
            self._persist()

    def _ask(self, text: str, stop: threading.Event | None = None) -> Turn:
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

        max_iterations = self.max_rounds or int(config.get("llm.max_tool_iterations", 8))
        escalate_after = int(config.get("agents.escalate_after_rounds", 3))
        escalate_ok = escalate_after > 0 and self._can_escalate() and self._turn_allowed is None
        escalated = False
        self._stop = stop
        self._round_allowed = None
        # Lets `decide_proposal` tell "proposed earlier" from "proposed this turn".
        self.turn_no = getattr(self, "turn_no", 0) + 1
        self.turn_text = text
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
        # A plan belongs to one request: a stale one from the last ask would
        # be shown to the model as if it were current.
        getattr(self, "tool_state", {}).pop("plan", None)

        with self._lock:
            self.messages.append({"role": "user", "content": text})
            self._trim_history()
            run_start = len(self.messages) - 1
            exhausted = ""

            tools = self._tools_for_turn(text)
            if self._turn_allowed is not None:
                tools = registry.tool_declarations(names=self._turn_allowed)
            system = self.system_prompt()

            # The confirmer must be installed for the whole loop, since skills
            # run on this thread and read it from thread-local state.
            with confirmer_scope(self.confirmer, self._memory, principal=self.principal or None):
                for iteration in range(1, max_iterations + 1):
                    turn.iterations = iteration
                    if cancelled():
                        return give_up("before_model")

                    # Token ceiling (delegated runs): what this run has added to
                    # its transcript, by the same chars/4 estimate as trimming.
                    if self.token_budget and iteration > 1:
                        used = sum(estimate_tokens(m) for m in self.messages[run_start:])
                        if used > self.token_budget:
                            exhausted = (f"reached its token budget (about {used} of "
                                         f"{self.token_budget} tokens) after {iteration - 1} tool rounds")
                            break

                    # Escalation, in code: after N tool rounds without an
                    # answer the Orchestrator's next round can only delegate
                    # (or reply). Both the schema and dispatch are narrowed, so
                    # a model that names another tool anyway is refused.
                    if escalate_ok and not escalated and iteration > escalate_after:
                        escalated = True
                        audit.record(
                            "agent.escalated", source=self.source,
                            after_rounds=iteration - 1,
                        )
                    round_allowed = self._allowed if self._turn_allowed is None else self._turn_allowed
                    note = ""
                    if escalated:
                        round_allowed = frozenset({"delegate"})
                        tools = registry.tool_declarations(names=round_allowed)
                        note = (
                            f"[context] You have used {iteration - 1} tool rounds on "
                            "this request without finishing. From here your only tool "
                            "is delegate: hand the remaining work to the right "
                            "sub-agent, or answer now with what you have."
                        )

                    try:
                        if not escalated:
                            tools = self._current_tools(text, tools)
                        self._emit("agent_state", "", phase="llm_start",
                                   agent=self.profile.name if self.profile else "orchestrator")
                        kwargs = {"prefer": self.profile.model} if (
                            self.profile is not None and self.profile.model) else {}
                        reply = self._llm.complete(
                            self._with_context(note), system=system, tools=tools,
                            role=self.model_role, tags=self._usage_tags(), **kwargs,
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

                    declined = False
                    for call in reply.tool_calls:
                        if declined:
                            self.messages.append({
                                "role": "tool",
                                "name": call.name,
                                "tool_call_id": call.id or call.name,
                                "content": "Not run — the user already declined that.",
                            })
                            continue
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
                        self._round_allowed = round_allowed
                        result = self._run_tool(call)
                        turn.tool_calls.append(call.name)
                        self.messages.append({
                            "role": "tool",
                            "name": call.name,
                            "tool_call_id": call.id or call.name,
                            "content": self._cap_result(call.name, result),
                        })
                        declined = (
                            declined or retried_declined_target()
                            or (self.end_turn_check is not None and bool(self.end_turn_check()))
                        )

                    if declined:
                        # The owner said no. Another round would only let the
                        # model try a workaround, so end the turn here.
                        turn.reply = DECLINED_REPLY
                        self.messages.append(
                            {"role": "assistant", "content": turn.reply}
                        )
                        self._emit("answer", turn.reply)
                        audit.record(
                            "safety.declined_turn_stop", source=self.source,
                            at="agent_loop", iterations=iteration,
                        )
                        return turn

                    if cancelled():
                        return give_up("after_tools")

                # Budget exhausted.
                if self.partial_on_budget:
                    return self._wrap_up(
                        turn, system,
                        exhausted or f"used all {max_iterations} tool rounds",
                    )
                turn.error = (
                    f"Stopped after {max_iterations} tool rounds without a final "
                    "answer. The request may be too broad — try narrowing it."
                )
                self._emit("error", turn.error)
                audit.record("agent.budget_exhausted", source=self.source)
                return turn

    def _wrap_up(self, turn: Turn, system: str, reason: str) -> Turn:
        """A delegated run out of budget: one tool-less call for a partial report.

        No error is raised: the caller gets whatever was done. If this call
        fails or says nothing, `turn.reply` stays empty and `run_child` builds
        the report from the plan and the tool log instead.
        """
        turn.budget_exhausted = reason
        audit.record("agent.budget_exhausted", source=self.source, reason=reason,
                     iterations=turn.iterations, wrap_up=True)
        note = (
            f"[context] You have {reason}. Do not call any tool. Write your final "
            "report now, in the Reporting format, with OUTCOME: partial. Say what "
            "you did (with the evidence a tool result gave), keep your PLAN with "
            "each item's real status, and list under LEFT what still has to be done."
        )
        try:
            kwargs = {"prefer": self.profile.model} if (
                self.profile is not None and self.profile.model) else {}
            # utility role; the profile's model stays the fallback preference
            # (LLM._plan), so with no models.roles nothing changes.
            reply = self._llm.complete(self._with_context(note), system=system,
                                       tools=None, role="utility",
                                       tags=self._usage_tags(), **kwargs)
            text = (reply.text or "").strip()
        except Exception as exc:                      # LLMError or anything else
            audit.record("agent.wrap_up_failed", source=self.source, error=str(exc))
            text = ""
        if text:
            turn.reply = text
            self.messages.append({"role": "assistant", "content": text})
            self._emit("answer", text)
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
            allowed=self._round_allowed if self._round_allowed is not None else self._allowed,
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


def make_agent(*, source: str, thread_id: str | None = None, **kwargs: Any) -> Agent:
    """The Agent for a transport the owner talks to (web, voice, CLI).

    ``agents.orchestrator`` (default off) switches these to the Orchestrator
    profile. Off, this is exactly ``Agent(source=...)`` as before. Jobs, the
    daemon and WhatsApp do not use it: unattended runs keep their old shape.
    """
    if config.get("agents.orchestrator", False):
        try:
            return Agent(source=source, thread_id=thread_id, profile=ORCHESTRATOR, **kwargs)
        except ProfileError as exc:
            # A broken profile must not take the assistant down; fall back to
            # the all-tools agent and say so.
            audit.record("agent.orchestrator_unavailable", error=str(exc))
    return Agent(source=source, thread_id=thread_id, **kwargs)


def make_voice_agent(*, observer: Observer | None, thread_id: str | None) -> Agent | None:
    """The Orchestrator context a Live voice session runs as, or ``None``.

    Gemini Live is the model, so this agent never calls ``ask``: it supplies the
    system prompt, the tool set (``_allowed``), the ``delegate`` context and the
    persistent thread. ``None`` (switch off, or a broken profile) means the
    session keeps its old all-tools behaviour exactly. ``observer`` is passed
    by the transport so voice activity reaches the state tracker without
    emitting the text UI per-turn ``tool_call`` frames.
    """
    if not config.get("agents.orchestrator", False):
        return None
    try:
        return Agent(source="voice-live", observer=observer, thread_id=thread_id,
                     profile=ORCHESTRATOR)
    except Exception as exc:                  # voice must still start, as before
        audit.record("agent.orchestrator_unavailable", error=str(exc), at="voice")
        return None
