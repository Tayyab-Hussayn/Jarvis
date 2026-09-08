"""The resident WhatsApp session.

Started by `kravein-whatsapp.service`; nothing else imports `neonize`.

**Why this process exists at all.** `neonize.client.connect()` does not open a
connection and return — it starts a Go runtime and blocks its thread until
`stop()` cancels the Go context. A short-lived process therefore cannot send a
WhatsApp message: it would pay a full connect and pairing handshake per
message, and repeated connect/disconnect churn is what makes a linked device
look unstable to WhatsApp's servers. So the session lives here, always on, and
the minutely tick reaches it through a unix socket.

**Threading.** `connect()` owns a thread for the whole session, so it runs on
one; the socket server runs on another; sends are marshalled onto the client
from the socket thread, which neonize permits because the Go side is
thread-safe. The main thread does nothing but wait, so Ctrl-C and SIGTERM stay
responsive — a service you cannot stop cleanly is a service that gets killed
with -9 and leaves a half-written session database.

**The session database is a credential.** `session.db` under the data dir is a
full WhatsApp device login: anyone who copies it is that device. Written 0600
in a 0700 directory, refused if the mode is wrong, and listed in `safety.py`'s
protected paths so no skill can read or move it.

**The QR is a credential too**, for its rotation window. It is held in memory
and handed out only over the already-authenticated socket. It is never logged,
never audited, and never written to the state directory.

**Inbound is a confirmation channel, not a chat.** A message arriving here is
untrusted input from the network reaching a process that changes local state,
so it is gated five ways — configured, not from us, not a group, from the
allowlisted owner, and recent — and then matched against a five-word vocabulary
with plain string comparison. It never reaches the model. Letting an LLM
interpret it would turn the reminder channel into a remote prompt-injection
route into an assistant that runs shell commands, and no amount of prompting
closes that. Anything that fails a gate is dropped in silence: replying, even
with an error, confirms to a stranger that something automated is listening.
See `handle_inbound`.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import socketserver
import threading
import time
from pathlib import Path
from typing import Any

from . import audit, config

SOCKET = config.RUNTIME_DIR / "whatsapp.sock"
SESSION_DIR = config.DATA_DIR / "whatsapp"
SESSION_DB = SESSION_DIR / "session.db"

# How long without a fresh code before the one we hold is called stale.
#
# **Not a rotation interval, and deliberately not one.** Measured against a
# live pairing session, whatsmeow delivers the *first* code with a 60s life and
# every refresh after it at 20s. A single TTL cannot express that: 60 serves a
# refresh that died forty seconds ago, and 25 declares the first code dead at
# 25s and then shows nothing at all for the 35s until the next one arrives.
# Both were built and both were wrong, in opposite directions.
#
# So the server's rotation is treated as the authority instead of guessed at:
# whatever code arrived most recently *is* the current one, because whatsmeow
# always pushes a replacement before the old one lapses. This constant only
# catches the case where the pushes stop altogether — a connection that dropped
# mid-pairing — at which point the panel says the code went stale rather than
# leaving a dead square on screen. Comfortably longer than any observed gap.
QR_STALE_SECONDS = 90

# Reconnection backoff. Capped, because a linked device that hammers the
# servers is a device that gets banned.
RECONNECT_BACKOFF = (5, 15, 30, 60, 120, 300)


class Session:
    """Owns the neonize client and everything that can be asked about it."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state = "unlinked"
        self.detail = ""
        self.jid = ""
        self.since = 0.0
        self.qr = ""
        self.qr_at = 0.0
        self.attempts = 0
        self.client: Any = None
        self.stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._relinking = False
        self._recent_commands: list[float] = []

    # -- state ------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            fresh = self._qr_fresh()
            return {
                "state": self.state,
                "detail": self.detail,
                "linked": bool(self.jid),
                "jid": self.jid,
                "since": self.since,
                "qr": self.qr if fresh else "",
                # Age rather than a countdown. A countdown needs a lifetime,
                # the lifetime is not constant, and a countdown that is wrong
                # is worse than none — it tells someone a live code is dead.
                "qr_age": round(time.time() - self.qr_at, 1) if self.qr else 0.0,
                "qr_stale": bool(self.qr) and not fresh,
                "attempts": self.attempts,
            }

    def _qr_fresh(self) -> bool:
        """Is the code we hold still the one the server wants scanned?

        Only meaningful while pairing: any other state means the flow ended,
        and a code left over from it must not be shown.
        """
        if not self.qr or self.state != "pairing":
            return False
        return (time.time() - self.qr_at) < QR_STALE_SECONDS

    def _set(self, state: str, detail: str = "") -> None:
        with self.lock:
            self.state = state
            self.detail = detail
        audit.record("whatsapp.state", state=state, detail=detail[:200])

    # -- the connection ---------------------------------------------------

    def start(self) -> None:
        """Run the session on its own thread, reconnecting as needed."""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="whatsapp", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self.stopping.is_set():
            try:
                self._connect_once()
            except Exception as exc:
                self._set("error", f"{type(exc).__name__}: {exc}")

            if self.stopping.is_set() or self.state == "unlinked":
                # `unlinked` here means the phone logged us out. That is
                # terminal: retrying cannot fix it and hammering the servers
                # after a logout is exactly the behaviour that gets a number
                # banned. Wait for an explicit `link`.
                return

            delay = RECONNECT_BACKOFF[min(self.attempts, len(RECONNECT_BACKOFF) - 1)]
            with self.lock:
                self.attempts += 1
            self._set("reconnecting", f"retrying in {delay}s")
            self.stopping.wait(delay)

    def _connect_once(self) -> None:
        from neonize.client import NewClient
        from neonize.events import (
            ConnectedEv, DisconnectedEv, LoggedOutEv, MessageEv, PairStatusEv,
        )

        _prepare_session_dir()
        client = NewClient(str(SESSION_DB))
        self.client = client

        @client.qr
        def _on_qr(_client: Any, code: bytes) -> None:
            # Straight into memory and nowhere else. Not logged, not audited,
            # not written to disk — for its rotation window this is a login.
            with self.lock:
                self.qr = code.decode("utf-8", "replace")
                self.qr_at = time.time()
            self._set("pairing", "scan the code")

        @client.event(PairStatusEv)
        def _on_pair(_client: Any, event: Any) -> None:
            with self.lock:
                self.qr = ""
                self.jid = str(getattr(getattr(event, "ID", None), "User", "") or "")
            self._set("connected", "paired")

        @client.event(ConnectedEv)
        def _on_connected(_client: Any, _event: Any) -> None:
            with self.lock:
                self.since = time.time()
                self.attempts = 0
                self.qr = ""
                if not self.jid:
                    me = getattr(client, "me", None)
                    self.jid = str(getattr(getattr(me, "JID", None), "User", "") or "")
            self._set("connected")

        @client.event(MessageEv)
        def _on_message(_client: Any, event: Any) -> None:
            # Everything that decides whether to act lives in `handle_inbound`,
            # which is a pure function and therefore testable without a phone.
            # This only unwraps the protobuf and sends the answer.
            try:
                source = event.Info.MessageSource
                body = (event.Message.conversation
                        or event.Message.extendedTextMessage.text or "")
                command, argument = handle_inbound(
                    sender=str(getattr(source.Sender, "User", "") or ""),
                    text=body,
                    is_from_me=bool(source.IsFromMe),
                    is_group=bool(source.IsGroup),
                    timestamp=float(getattr(event.Info, "Timestamp", 0) or 0),
                )
            except Exception as exc:
                audit.record("whatsapp.inbound_error",
                             error=f"{type(exc).__name__}: {exc}")
                return

            if not command or not self._allow_rate():
                return

            audit.record("whatsapp.inbound", command=command,
                         has_argument=bool(argument))
            reply = run_command(command, argument)
            if reply:
                self.send(reply, "owner", f"reply/{command}")

        @client.event(DisconnectedEv)
        def _on_disconnected(_client: Any, _event: Any) -> None:
            self._set("reconnecting", "the connection dropped")

        @client.event(LoggedOutEv)
        def _on_logged_out(_client: Any, _event: Any) -> None:
            with self.lock:
                self.jid = ""
                self.qr = ""
            self._set("unlinked", "the phone unlinked this device")

        self._set("connecting")
        # Blocks until `stop()` cancels the Go context. This is the whole
        # reason the process exists.
        client.connect()

    def stop(self) -> None:
        self.stopping.set()
        client = self.client
        if client is not None:
            try:
                client.stop()
            except Exception:
                pass

    # -- operations -------------------------------------------------------

    def begin_link(self) -> dict[str, Any]:
        """Start a *fresh* pairing session, tearing down any stale one first.

        The subtlety that made `kravein whatsapp link` hang forever: whatsmeow
        stops pushing QR codes a couple of minutes after a pairing session
        opens, but the thread inside `client.connect()` stays alive and the
        state stays `pairing`. `start()` short-circuits on a live thread, so
        asking to link again did **nothing at all** — the CLI sat on "Waiting
        for a code…" waiting for a channel that had closed twenty minutes
        earlier.

        A live thread is therefore not evidence that pairing is possible.
        Linking always tears the client down and builds a new one.
        """
        if self.state == "connected":
            return {"ok": True, "state": "connected", "detail": "already linked"}

        # Returns immediately and does the work on its own thread. Tearing down
        # a live client means waiting for `connect()` to unwind, which can take
        # longer than the caller's socket timeout — doing it inline made the
        # client report "the service did not answer" while the relink was in
        # fact proceeding, and a socket handler blocked on a thread join stalls
        # every other request behind it.
        with self.lock:
            if self._relinking:
                return {"ok": True, "state": self.state, "detail": "already relinking"}
            self._relinking = True

        threading.Thread(target=self._relink, name="whatsapp-relink", daemon=True).start()
        return {"ok": True, "state": "relinking"}

    def _relink(self) -> None:
        try:
            self._teardown()
            with self.lock:
                self.attempts = 0
                self.qr = ""
                self.qr_at = 0.0
            self._set("connecting", "starting a fresh pairing session")
            self.stopping.clear()
            self.start()
        finally:
            with self.lock:
                self._relinking = False
        self._recent_commands: list[float] = []

    def _allow_rate(self) -> bool:
        """One more command this minute? Cheap guard, owner-only path.

        Not a security boundary — the allowlist is — but a stuck client
        retrying in a loop would otherwise hammer the routine store, and the
        store takes a file lock the minutely tick also wants.
        """
        limit = int(config.get("whatsapp.inbound_rate_limit", 20) or 20)
        now = time.time()
        with self.lock:
            self._recent_commands = [t for t in self._recent_commands if now - t < 60]
            if len(self._recent_commands) >= limit:
                audit.record("whatsapp.inbound_dropped", reason="rate_limited")
                return False
            self._recent_commands.append(now)
        return True

    def _teardown(self) -> None:
        """Stop the client and wait for its thread to actually leave.

        `connect()` only returns once the Go context is cancelled, so starting
        a new client without waiting would leave two of them alive against one
        session database.
        """
        thread = self._thread
        if not (thread and thread.is_alive()):
            return
        self.stopping.set()
        client = self.client
        if client is not None:
            with __import__("contextlib").suppress(Exception):
                client.stop()
        thread.join(timeout=15)
        if thread.is_alive():
            audit.record("whatsapp.teardown_timeout")
        self._thread = None
        self.client = None

    def unlink(self) -> dict[str, Any]:
        client = self.client
        if client is None:
            return {"ok": False, "error": "not connected"}
        try:
            client.logout()
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        with self.lock:
            self.jid = ""
            self.qr = ""
        self._set("unlinked", "logged out on request")
        audit.record("whatsapp.unlinked")
        return {"ok": True}

    def send(self, text: str, to: str, ref: str) -> dict[str, Any]:
        if self.state != "connected" or not self.client:
            return {"ok": False, "error": f"not connected ({self.state})"}

        target = _resolve_target(to, self.jid)
        if not target:
            return {"ok": False, "error": "no recipient configured — set whatsapp.owner"}

        try:
            from neonize.utils import build_jid

            self.client.send_message(build_jid(target), text)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        # The recipient is recorded, the message body is not: reminders can say
        # anything and the audit log is read for "what did it do", not "what
        # did it say".
        audit.record("whatsapp.sent", to=target, ref=ref, chars=len(text))
        return {"ok": True, "to": target}


def _resolve_target(to: str, own_jid: str) -> str:
    """Which number to send to. "owner" is the configured one, or ourselves."""
    if to and to != "owner":
        return "".join(ch for ch in to if ch.isdigit())
    configured = str(config.get("whatsapp.owner", "") or "").strip()
    if configured:
        return "".join(ch for ch in configured if ch.isdigit())
    # Messaging the linked account itself is the safe default: it always works
    # once paired and it cannot reach anybody else by accident.
    return own_jid


# ---------------------------------------------------------------------------
# Inbound: allowlist, vocabulary, dispatch
# ---------------------------------------------------------------------------

# The entire accepted vocabulary. Anything not in here is dropped without a
# reply — see `handle_inbound` for why silence is the only safe answer.
COMMANDS = ("done", "skip", "status", "pause", "resume")

# The only shape an argument may take: a step id as the panel and the reminders
# print it, "s" and a small number. Anything looser turns a typo into a
# command — "done please" was briefly accepted with "please" as the target,
# which is not in the specified vocabulary and widens what a stranger could
# probe with for free.
_STEP_ID = __import__("re").compile(r"s\d{1,3}")


def parse_command(text: str) -> tuple[str, str] | None:
    """Read one message into (command, argument), or None to drop it.

    **Plain string matching, deliberately.** A message from WhatsApp is
    untrusted input arriving over the network at a process that can change
    local state, so it never reaches the model: handing it to an LLM to
    "work out what they meant" turns the reminder channel into a remote prompt
    injection route. Twelve lines of comparison is the whole feature, and its
    narrowness is the security property.

    Accepts exactly: done, done <step_id>, skip, skip <step_id>, status,
    pause, resume — where a step id is "s" plus a small number, as printed in the
    reminders and the panel. Case and surrounding whitespace are forgiven
    because phone keyboards capitalise; nothing else is.
    """
    cleaned = (text or "").strip().lower()
    if not cleaned or len(cleaned) > 64:
        return None

    # One line, no control characters. `str.split()` treats a newline as
    # ordinary whitespace, so "done\nstatus" arrived looking like a two-word
    # command and was accepted. Nothing bad came of that one, but a vocabulary
    # this narrow only stays narrow if the input is a single flat line.
    if any(ch.isspace() and ch != " " for ch in cleaned):
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in cleaned):
        return None

    parts = cleaned.split(" ")
    parts = [p for p in parts if p]
    if not parts or parts[0] not in COMMANDS:
        return None
    command = parts[0]

    # These take no argument at all, so anything trailing them is not a
    # command. `resume` exists because a control that only goes one way is a
    # trap: pausing from a phone and needing the laptop to start again is
    # exactly the situation this channel is meant to avoid.
    if command in ("status", "pause", "resume"):
        return (command, "") if len(parts) == 1 else None

    if len(parts) == 1:
        return command, ""
    if len(parts) == 2 and _STEP_ID.fullmatch(parts[1]):
        return command, parts[1]
    return None


def owner_jid() -> str:
    """The one number allowed to send commands. Empty means inbound is off."""
    raw = str(config.get("whatsapp.owner_jid", "") or "")
    return "".join(ch for ch in raw if ch.isdigit())


def handle_inbound(sender: str, text: str, *, is_from_me: bool, is_group: bool,
                   timestamp: float, now: float | None = None,
                   allow: str | None = None,
                   max_age: float | None = None) -> tuple[str, str]:
    """Decide what an incoming message means. Returns (action, reply).

    `action` is the command to run, or "" to do nothing at all. A reply is only
    ever produced for an accepted command — every rejection returns ("", "").

    **Silence is the security property, not politeness.** Answering an
    unrecognised message, even with an error, confirms to whoever sent it that
    something automated is reading this number. So a stranger gets nothing: no
    reply, no read receipt beyond WhatsApp's own, and no record of what they
    wrote.

    Five gates, and each one is load-bearing:

    * **Not configured** — no `owner_jid` means inbound is off entirely. A
      channel nobody set up should not be open.
    * **From us** — our own confirmation replies come back through the same
      handler. Without this a reply containing "done" would feed itself.
    * **Group** — even the owner's message in a group is refused. Anyone can
      add a number to a group; being in one is not authorisation.
    * **Not the owner** — the allowlist, compared on digits so device suffixes
      and formatting cannot smuggle a near-match through.
    * **Stale** — whatsmeow replays undelivered messages on reconnect, so a
      "done" from this morning could arrive at teatime and silently complete a
      step nobody did. Anything older than the window is ignored.
    """
    if is_from_me or is_group:
        return "", ""

    allowed = allow if allow is not None else owner_jid()
    if not allowed:
        return "", ""

    digits = "".join(ch for ch in (sender or "") if ch.isdigit())
    if not digits or digits != allowed:
        # Deliberately no content, and no sender, in the record. Knowing that
        # something was refused is useful; keeping what a stranger wrote is a
        # log of other people's messages.
        audit.record("whatsapp.inbound_dropped", reason="not_owner")
        return "", ""

    window = max_age if max_age is not None else float(
        config.get("whatsapp.inbound_max_age_seconds", 300) or 300)
    age = (now if now is not None else time.time()) - float(timestamp or 0)
    if age > window:
        audit.record("whatsapp.inbound_dropped", reason="stale",
                     age_seconds=round(age))
        return "", ""

    parsed = parse_command(text)
    if parsed is None:
        audit.record("whatsapp.inbound_dropped", reason="not_a_command")
        return "", ""

    command, argument = parsed
    return command, argument


def run_command(command: str, argument: str, *, moment: Any = None) -> str:
    """Apply an accepted command to the routine store. Returns the reply.

    `moment` overrides the clock, so the whole path is testable against a fixed
    day without waiting for one.

    Writes directly rather than routing through `kravein serve`. The store is
    the source of truth and `routines.lock` already makes concurrent access
    from the tick safe; going via the web server instead would mean a "done"
    sent while the browser was closed did nothing at all, which is the one
    thing a reminder channel must never do.
    """
    from . import routines

    try:
        if command == "status":
            return routines.status_line(moment)

        if command == "pause":
            names = routines.pause_all(True)
            if not names:
                return "Everything is already paused."
            return "Paused: " + ", ".join(names) + ". Send \"resume\" to start again."

        if command == "resume":
            names = routines.pause_all(False)
            if not names:
                return "Nothing was paused."
            return "Resumed: " + ", ".join(names) + "."

        state = "done" if command == "done" else "skipped"
        acted, message = routines.resolve_open(state, step_id=argument,
                                               source="whatsapp", moment=moment)
        if acted:
            _notify_ui(command, argument)
        return message
    except Exception as exc:
        audit.record("whatsapp.inbound_failed", command=command,
                     error=f"{type(exc).__name__}: {exc}")
        return "That did not work. Check the Habits panel."


def _notify_ui(command: str, argument: str) -> None:
    """Best-effort nudge so an open Habits panel refreshes.

    The panel reloads on any `routine_event`, so this carries no detail of its
    own. A closed browser is the normal case and not worth reporting.
    """
    try:
        from . import delivery

        delivery.push("/api/routine-event", {
            "routine": "", "step": argument, "label": "",
            "text": f"{command} via WhatsApp", "nag": 0,
        })
    except Exception:
        pass


def linked_devices() -> list[Any]:
    """Devices registered in the session store. Empty means nothing is paired.

    The store answers this without opening a connection, which is what makes it
    usable as a startup condition.
    """
    if not SESSION_DB.exists():
        return []
    try:
        from neonize.client import ClientFactory

        return list(ClientFactory.get_all_devices_from_db(str(SESSION_DB)))
    except Exception:
        return []


def _prepare_session_dir() -> None:
    """0700 directory, 0600 database, and refuse anything looser.

    A world-readable session file is a credential leak with no symptom — the
    assistant keeps working perfectly while anybody on the machine can lift the
    device login. Cheaper to refuse to start.
    """
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.chmod(0o700)
    if SESSION_DB.exists():
        mode = SESSION_DB.stat().st_mode & 0o777
        if mode & 0o077:
            SESSION_DB.chmod(0o600)
            audit.record("whatsapp.session_mode_tightened", was=oct(mode))


# ---------------------------------------------------------------------------
# The socket
# ---------------------------------------------------------------------------

class _Handler(socketserver.StreamRequestHandler):
    session: Session

    def handle(self) -> None:
        raw = self.rfile.readline(65536)
        try:
            request = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._reply({"ok": False, "error": "malformed request"})
            return

        op = str(request.get("op", ""))
        session = type(self).session

        if op == "state":
            self._reply(session.snapshot())
        elif op == "link":
            self._reply(session.begin_link())
        elif op == "unlink":
            self._reply(session.unlink())
        elif op == "send":
            self._reply(session.send(
                str(request.get("text", "")),
                str(request.get("to", "owner")),
                str(request.get("ref", "")),
            ))
        else:
            self._reply({"ok": False, "error": f"unknown op {op!r}"})

    def _reply(self, payload: dict[str, Any]) -> None:
        try:
            self.wfile.write((json.dumps(payload) + "\n").encode("utf-8"))
        except OSError:
            pass


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def already_serving() -> bool:
    """Is another instance answering on the socket right now?

    A unix socket left on disk says nothing about whether anything is behind
    it — a killed process leaves the path. So this asks: a reply means a live
    owner, a refused connection means a stale file safe to replace.
    """
    if not SOCKET.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(2.0)
            probe.connect(str(SOCKET))
            probe.sendall(b'{"op": "state"}\n')
            return bool(probe.recv(4096))
    except OSError:
        return False


def serve(session: Session) -> _Server:
    """Bind the socket 0600 and start answering.

    The mode is set before the socket is usable, not after: a window in which
    it is world-writable is a window in which anything on the machine can send
    messages as you.

    Refuses to start over a live instance. The first version simply unlinked
    whatever was there, which meant a second copy stole the path from a working
    session — and then, when *it* exited, deleted the socket the survivor was
    still listening on. The symptom is the whole channel silently disappearing
    while a perfectly healthy daemon is still running, and with
    `Restart=always` above it that is not a hypothetical.
    """
    config.ensure_dirs()
    if already_serving():
        raise RuntimeError("another Kravein WhatsApp service is already running")
    if SOCKET.exists():
        SOCKET.unlink()          # stale: nothing answered on it

    previous = os.umask(0o177)          # 0600 on creation, no race
    try:
        handler = type("_Bound", (_Handler,), {"session": session})
        server = _Server(str(SOCKET), handler)
    finally:
        os.umask(previous)

    SOCKET.chmod(0o600)
    # Remember which inode we bound. On the way out we only remove the socket
    # if it is still that one — see `main`.
    server.bound_inode = SOCKET.stat().st_ino
    threading.Thread(target=server.serve_forever, name="whatsapp-socket",
                     daemon=True).start()
    return server


def main(argv: list[str] | None = None) -> int:
    import sys

    args = list(argv if argv is not None else sys.argv[1:])
    session = Session()
    try:
        server = serve(session)
    except RuntimeError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    audit.record("whatsapp.service_started", socket=str(SOCKET))

    # Only auto-connect when a device is actually **registered**, not merely
    # when the database file is present.
    #
    # `SESSION_DB.exists()` was the first condition and it is wrong: neonize
    # creates that file the moment a client is constructed, so a machine that
    # has *attempted* pairing and never finished has one. The service then
    # opened a pairing session at boot that nobody was watching, whatsmeow
    # stopped pushing codes about two minutes later, and it sat in `pairing`
    # for ever with a dead code — which is exactly what a user then runs
    # `whatsapp link` into.
    if linked_devices() or "--link" in args:
        session.start()
    else:
        session._set("unlinked", "no device linked — run `kravein whatsapp link`")

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    try:
        stop.wait()
    finally:
        session.stop()
        server.shutdown()
        # Only remove the socket this process actually bound, identified by
        # inode rather than by path — the path is the same string for every
        # instance, so comparing paths would always match. Deleting it
        # unconditionally is how an exiting duplicate used to take a live
        # instance's channel down with it.
        with __import__("contextlib").suppress(OSError):
            if SOCKET.stat().st_ino == getattr(server, "bound_inode", None):
                SOCKET.unlink()
        audit.record("whatsapp.service_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
