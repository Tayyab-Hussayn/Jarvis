"""Event frames and the worker-thread ↔ event-loop bridge for the web UI.

Transport-agnostic on purpose: nothing here imports FastAPI. ``server.py``
owns the websocket; this module owns the two hard parts.

**The threading model.** The agent loop is synchronous and blocking — skills
shell out, poll windows and sleep. It therefore runs on a worker thread while
the websocket lives on the asyncio event loop. Two things have to cross that
boundary:

1. Events, worker → loop. Pushed with ``loop.call_soon_threadsafe``; never
   touch the asyncio queue from the worker directly.
2. Confirmations, loop → worker. ``safety.Confirmer`` is a *blocking* call:
   the skill thread stops inside ``safety.enforce()`` until it gets a verdict.
   So the worker parks on a ``threading.Event`` while the browser is asked.

**Silence is never approval.** A confirmation that times out, or whose session
drops, resolves to ``False``. The default is deny at every exit from
:meth:`Session.confirm`, including the exception paths.
"""

from __future__ import annotations

import asyncio
import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from . import audit, config, phone
from .safety import Danger

# How long a confirmation card may sit unanswered before it is denied. A
# generous default: the user may have walked away mid-turn, and re-asking is
# cheap compared with silently killing a process they never approved.
DEFAULT_CONFIRM_TIMEOUT = 120.0


# Confirmation ids are unique across *all* sessions, not per session: a paired
# phone is shown confirmations from every open browser/voice session and
# answers by id alone, so two sessions both minting "c1" would let it approve
# the wrong one.
_confirm_ids = itertools.count(1)
_open_confirms: "dict[str, tuple[Session, dict[str, Any]]]" = {}
_open_lock = threading.Lock()


def open_confirms() -> "list[tuple[str, dict[str, Any]]]":
    """Every confirmation currently waiting, as ``(id, info)``. For a phone
    that connects while one is pending."""
    with _open_lock:
        return [(cid, dict(info)) for cid, (_, info) in _open_confirms.items()]


def resolve_open(confirm_id: str, approve: bool) -> bool:
    """Answer a pending confirmation in whichever session holds it.

    The phone's entry point. Same path as the browser's (``resolve_confirm``);
    an unknown, late or already-answered id returns False and approves nothing.
    """
    with _open_lock:
        entry = _open_confirms.get(confirm_id)
    if entry is None:
        return False
    return entry[0].resolve_confirm(confirm_id, approve, by="phone")


def frame(t: str, **fields: Any) -> dict[str, Any]:
    """Build one wire frame. ``t`` is the discriminator the client switches on."""
    return {"t": t, **fields}


@dataclass
class _Pending:
    """A confirmation the browser has been asked about but not yet answered."""

    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False
    by: str = ""


class Session:
    """One connected client: an outbound queue plus its pending confirmations.

    Created on the event loop, then handed to a worker thread. Every method is
    documented with which side is expected to call it, because getting that
    wrong is how this class breaks.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        confirm_timeout: float | None = None,
        max_queue: int = 1000,
    ) -> None:
        self._loop = loop
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue)
        self._pending: dict[str, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._closed = threading.Event()
        self.turn_id: str = ""
        self.confirm_timeout = (
            confirm_timeout
            if confirm_timeout is not None
            else float(config.get("web.confirm_timeout_seconds", DEFAULT_CONFIRM_TIMEOUT))
        )

    # -- outbound: worker thread -> event loop ---------------------------

    def emit(self, t: str, **fields: Any) -> None:
        """Queue a frame for the client. Safe from any thread."""
        if self._closed.is_set():
            return
        payload = frame(t, **fields)
        try:
            self._loop.call_soon_threadsafe(self._offer, payload)
        except RuntimeError:
            # Loop already closed — the client is gone; drop the frame.
            self._closed.set()

    def _offer(self, payload: dict[str, Any]) -> None:
        """Enqueue on the loop thread, dropping rather than blocking it."""
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            # A slow or wedged client must never stall the agent. Losing a
            # progress frame is survivable; blocking the event loop is not.
            pass

    async def next_frame(self) -> dict[str, Any]:
        """Await the next outbound frame. Event loop only."""
        return await self._queue.get()

    # -- confirmations: blocking on the worker, answered on the loop -----

    def confirm(self, action: str, detail: str, danger: Danger) -> bool:
        """A :data:`safety.Confirmer`. **Blocks the calling worker thread.**

        Returns ``False`` on timeout, on a closed session, or if the client
        answers "no" — deny is the default at every exit.
        """
        if self._closed.is_set():
            return False

        confirm_id = f"c{next(_confirm_ids)}"
        pending = _Pending()
        info = {
            "action": action, "detail": detail, "danger": danger.name,
            "expires_at": time.time() + self.confirm_timeout,
        }
        with self._pending_lock:
            self._pending[confirm_id] = pending
        with _open_lock:
            _open_confirms[confirm_id] = (self, info)

        audit.record(
            "web.confirm_requested",
            confirm_id=confirm_id,
            action=action,
            danger=danger.name,
            turn=self.turn_id,
        )

        self.emit(
            "confirm_request",
            id=self.turn_id,
            confirm_id=confirm_id,
            action=action,
            detail=detail,
            danger=danger.name,
            timeout=self.confirm_timeout,
        )

        # A phone that raises must not leave the browser card unanswerable.
        try:
            phone.relay_confirm_request(confirm_id, info)
        except Exception:
            pass

        started = time.monotonic()
        answered = pending.event.wait(timeout=self.confirm_timeout)
        approved = bool(answered and pending.approved)

        with self._pending_lock:
            self._pending.pop(confirm_id, None)
        with _open_lock:
            _open_confirms.pop(confirm_id, None)
        # Resolved anywhere (browser, phone, cancel, timeout): dismiss the
        # notification. Runs on every exit, so it cannot be skipped.
        try:
            phone.relay_confirm_closed(
                confirm_id,
                "expired" if not answered else "approved" if approved else "denied",
            )
        except Exception:
            pass

        audit.record(
            "web.confirm_resolved",
            confirm_id=confirm_id,
            action=action,
            danger=danger.name,
            approved=approved,
            answered_by=pending.by,
            timed_out=not answered,
            waited_seconds=round(time.monotonic() - started, 2),
        )

        if not answered:
            # Tell the client its card is dead, so it stops offering a button
            # that no longer resolves anything.
            self.emit(
                "confirm_expired", id=self.turn_id, confirm_id=confirm_id,
            )
        return approved

    def resolve_confirm(self, confirm_id: str, approve: bool, by: str = "web") -> bool:
        """Answer a pending confirmation. Returns False if it was unknown or
        already answered (first answer wins).

        Called from the event loop when a ``confirm_reply`` frame arrives. An
        unknown id means the card already timed out or was never issued —
        report it rather than inventing an approval.
        """
        with self._pending_lock:
            pending = self._pending.get(confirm_id)
            if pending is None or pending.event.is_set():
                # Already answered but the worker has not woken to pop it yet:
                # a second answer must not flip the first (deny, then approve).
                return False
            # Set the verdict before waking the worker, or it can observe the
            # default False and deny an action the user actually approved.
            pending.approved = bool(approve)
            pending.by = by
        pending.event.set()
        return True

    def deny_pending(self) -> int:
        """Release every waiting confirmation as a denial. Returns how many.

        Cancelling a turn that is parked on a confirmation has to answer it,
        or the worker thread stays blocked until the 120s timeout and the stop
        does nothing visible for two minutes. Denial is the only safe
        resolution: a cancelled request must not carry on and perform the very
        action it was waiting to be allowed to do.

        Unlike `close()` this leaves the session usable — the socket is fine,
        it is the turn that is being abandoned.
        """
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for p in pending:
            p.approved = False
            p.event.set()
        return len(pending)

    # -- teardown --------------------------------------------------------

    def close(self) -> None:
        """Release every blocked worker. Safe from any thread, idempotent."""
        self._closed.set()
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for p in pending:
            # approved stays False: a dropped connection cannot approve.
            p.event.set()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()


# ---------------------------------------------------------------------------
# Live session registry
# ---------------------------------------------------------------------------
#
# The scheduled-jobs tick is a separate short-lived process, so it cannot reach
# a browser on its own. It posts to the running server, which fans the event out
# to whatever sockets are connected — this is the set they are held in. A
# desktop notification goes out regardless; this is the extra, not the
# guarantee.

_live: "set[Session]" = set()
_live_lock = threading.Lock()


def register(session: "Session") -> None:
    with _live_lock:
        _live.add(session)


def unregister(session: "Session") -> None:
    with _live_lock:
        _live.discard(session)


def broadcast(t: str, **fields: Any) -> int:
    """Emit a frame to every connected client. Returns how many got it."""
    with _live_lock:
        targets = [s for s in _live if not s.closed]
    for session in targets:
        try:
            session.emit(t, **fields)
        except Exception:
            pass
    return len(targets)


# ---------------------------------------------------------------------------
# Agent observer -> frames
# ---------------------------------------------------------------------------

def observer_for(session: Session) -> Any:
    """Adapt :data:`agent.Observer` callbacks onto the wire protocol."""

    def observe(kind: str, text: str, data: dict[str, Any]) -> None:
        turn = session.turn_id
        if kind == "tool":
            session.emit(
                "tool_call", id=turn,
                call_id=data.get("call_id", ""),
                name=data.get("name", ""),
                args=data.get("args", {}),
                display=text,
            )
        elif kind == "tool_result":
            session.emit(
                "tool_result", id=turn,
                call_id=data.get("call_id", ""),
                name=data.get("name", ""),
                result=data.get("result", text),
            )
        elif kind == "thinking":
            session.emit("thinking", id=turn, text=text)
        elif kind == "answer":
            session.emit("answer", id=turn, text=text)
        elif kind == "error":
            session.emit("error", id=turn, message=text)

    return observe
