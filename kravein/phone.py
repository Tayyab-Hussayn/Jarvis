"""The phone channel: pairing, device credentials, the device link, and
:func:`call` — the one blocking API Kravein code uses to drive a phone.

Transport-agnostic like :mod:`events`: nothing here imports FastAPI.
``server.py`` owns the HTTP routes and the websocket; this module owns the
parts that have to be right.

**Vocabulary.** ``phone_protocol.json`` next to this file is the only
definition of frames, commands and their arguments. A capability name *is* a
command name. The old gateway advertised ``battery`` and executed
``get_battery``; that split is gone. Every inbound frame and every outbound
``execute`` is validated against the file.

**Credentials.** Pairing redeems a one-time, short-lived offer (shown as a QR)
for a per-device secret. The secret is 256 random bits, returned exactly once,
and stored only as its SHA-256. A fast hash is enough because the input is
random, not a password, so there is nothing to brute-force. Revoking deletes
the record and drops the device's live socket.

**Every call resolves.** :func:`call` parks the caller on a
``threading.Event``. A timeout, a dropped socket, a revoke, a replacement
connection or a full send queue all wake it with an exception. A phone that
goes quiet can never hang a skill.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
import hashlib
import itertools
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import audit, config, webauth

PROTOCOL_FILE = Path(__file__).resolve().parent / "phone_protocol.json"
PHONES_FILE = config.DATA_DIR / "phones.json"

DEFAULT_OFFER_TTL = 120.0
DEFAULT_COMMAND_TIMEOUT = 15.0
# A paired phone keeps working across restarts for this long, then must pair
# again. Bounded so a lost phone's credential does not live forever.
DEFAULT_DEVICE_TTL_DAYS = 14
SWEEP_INTERVAL = 3600.0
DEVICE_AUTH_SCHEME = "device"
MAX_PENDING_FRAMES = 256

# Indirection so tests can move time without sleeping.
_clock = time.monotonic


# ---------------------------------------------------------------------------
# Errors — every way a call can fail has a name and a stable code
# ---------------------------------------------------------------------------

class PhoneError(Exception):
    code = "failed"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class PhoneNotConnected(PhoneError):
    code = "not_connected"


class PhoneAmbiguous(PhoneError):
    """Several phones are connected and the caller did not say which one."""

    code = "ambiguous_device"


class PhoneCommandRejected(PhoneError):
    """Refused before anything reached the phone: unknown command, bad args, missing capability."""

    code = "invalid_args"


class PhoneTimeout(PhoneError):
    code = "timeout"


class PhoneDisconnected(PhoneError):
    code = "disconnected"


class PhoneCommandFailed(PhoneError):
    """The phone ran the command and reported a failure."""


class PhoneBadResult(PhoneError):
    """The phone said ``ok`` but its ``data`` does not match the command's
    ``result`` spec. Callers never see data the protocol does not describe."""

    code = "bad_result"


class ProtocolError(Exception):
    """An inbound frame that cannot be accepted. Answered with an ``error`` frame."""

    def __init__(self, code: str, message: str, request_id: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.request_id = request_id


# ---------------------------------------------------------------------------
# Protocol — loaded once from the JSON file, then used to validate everything
# ---------------------------------------------------------------------------

_protocol: dict[str, Any] | None = None


def protocol() -> dict[str, Any]:
    global _protocol
    if _protocol is None:
        _protocol = json.loads(PROTOCOL_FILE.read_text(encoding="utf-8"))
    return _protocol


def protocol_version() -> int:
    return int(protocol()["protocol"])


def command_names() -> frozenset[str]:
    return frozenset(protocol()["commands"])


def _check_value(name: str, spec: dict[str, Any], value: Any, code: str) -> None:
    """Validate one field against its spec. Raises ``ProtocolError(code, …)``."""
    kind = spec["type"]
    proto = protocol()
    if kind == "string":
        ok = isinstance(value, str)
    elif kind == "integer":
        # bool is an int in Python; `true` is not a volume.
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif kind == "boolean":
        ok = isinstance(value, bool)
    elif kind == "object":
        ok = isinstance(value, dict)
    elif kind == "command":
        if not isinstance(value, str) or value not in proto["commands"]:
            raise ProtocolError("unknown_command", f"unknown command {value!r}")
        ok = True
    elif kind == "list_of_commands":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ProtocolError(code, f"{name} must be a list of command names")
        unknown = sorted(set(value) - set(proto["commands"]))
        if unknown:
            raise ProtocolError(
                "unknown_capability",
                f"unknown capabilities {unknown}; a capability is a command name",
            )
        ok = True
    elif kind == "error_code":
        ok = isinstance(value, str) and value in proto["error_codes"]
    elif kind == "url":
        ok = isinstance(value, str)
        if ok:
            parts = urlsplit(value)
            ok = parts.scheme in ("http", "https") and bool(parts.netloc)
    else:  # a spec typo must fail loudly, not validate everything
        raise ValueError(f"phone_protocol.json: unknown field type {kind!r}")

    if not ok:
        raise ProtocolError(code, f"{name} must be {kind}")
    if isinstance(value, str) and "max_length" in spec and len(value) > spec["max_length"]:
        raise ProtocolError(code, f"{name} is longer than {spec['max_length']}")
    if "values" in spec and value not in spec["values"]:
        raise ProtocolError(code, f"{name} must be one of {spec['values']}")
    if kind == "integer":
        if "min" in spec and value < spec["min"]:
            raise ProtocolError(code, f"{name} is below {spec['min']}")
        if "max" in spec and value > spec["max"]:
            raise ProtocolError(code, f"{name} is above {spec['max']}")


def _check_fields(
    where: str, specs: dict[str, Any], data: dict[str, Any], code: str
) -> None:
    """Strict: missing required fields and unknown fields are both errors."""
    extra = sorted(set(data) - set(specs))
    if extra:
        raise ProtocolError(code, f"{where}: unexpected fields {extra}")
    for name, spec in specs.items():
        if name not in data:
            if spec.get("required"):
                raise ProtocolError(code, f"{where}: missing {name}")
            continue
        _check_value(f"{where}.{name}", spec, data[name], code)


def validate_command(command: str, args: dict[str, Any] | None) -> dict[str, Any]:
    """Check a command and its arguments against the vocabulary. Returns the args."""
    args = {} if args is None else args
    spec = protocol()["commands"].get(command)
    if spec is None:
        raise PhoneCommandRejected(f"unknown phone command {command!r}", code="unknown_command")
    if not isinstance(args, dict):
        raise PhoneCommandRejected("args must be an object", code="invalid_args")
    try:
        _check_fields(command, spec["args"], args, "invalid_args")
    except ProtocolError as exc:
        raise PhoneCommandRejected(str(exc), code=exc.code) from None
    return args


def validate_result(command: str, data: dict[str, Any]) -> dict[str, Any]:
    """Check a phone's ``result.data`` against the command's ``result`` spec.

    Strict like every other frame: a missing field, a wrong type, an out of
    range value or an unexpected field is a :class:`PhoneBadResult`.
    """
    spec = protocol()["commands"][command]["result"]
    try:
        _check_fields(f"{command} result", spec, data, "bad_result")
    except ProtocolError as exc:
        raise PhoneBadResult(f"phone returned a malformed {command} result: {exc}") from None
    return data


def parse_frame(text: str, *, inbound: bool = True) -> tuple[str, str, dict[str, Any]]:
    """Parse and validate one frame. Returns ``(type, request_id, payload)``.

    ``inbound`` frames must be ones a phone is allowed to send.
    """
    proto = protocol()
    if len(text.encode("utf-8")) > proto["max_frame_bytes"]:
        raise ProtocolError("frame_too_large", f"frame exceeds {proto['max_frame_bytes']} bytes")
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        raise ProtocolError("malformed_frame", "frame is not JSON") from None
    if not isinstance(data, dict):
        raise ProtocolError("malformed_frame", "frame must be a JSON object")

    request_id = data.get("request_id") if isinstance(data.get("request_id"), str) else ""
    try:
        _check_fields("frame", proto["envelope"], data, "malformed_frame")
    except ProtocolError as exc:
        exc.request_id = request_id[:64]
        raise

    frame_type = data["type"]
    spec = proto["frames"].get(frame_type)
    wanted = "phone_to_server" if inbound else "server_to_phone"
    if spec is None or spec["direction"] not in (wanted, "both"):
        raise ProtocolError("unknown_frame", f"unknown frame type {frame_type!r}", request_id)
    try:
        _check_fields(frame_type, spec["payload"], data["payload"], "malformed_frame")
    except ProtocolError as exc:
        exc.request_id = request_id
        raise
    return frame_type, request_id, data["payload"]


def make_frame(frame_type: str, request_id: str, **payload: Any) -> dict[str, Any]:
    return {"type": frame_type, "request_id": request_id, "payload": payload}


def error_frame(code: str, message: str, request_id: str = "") -> dict[str, Any]:
    return make_frame("error", request_id, code=code, message=message[:500])


# ---------------------------------------------------------------------------
# Device store — hashed secrets on disk, 0600
# ---------------------------------------------------------------------------

_store_lock = threading.RLock()


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _load_devices() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(PHONES_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        # A corrupt store authenticates nobody. Failing closed is right; the
        # owner re-pairs.
        return {}
    devices = data.get("devices") if isinstance(data, dict) else None
    return devices if isinstance(devices, dict) else {}


def _save_devices(devices: dict[str, dict[str, Any]]) -> None:
    PHONES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PHONES_FILE.with_name(PHONES_FILE.name + ".tmp")
    # Created 0600 from the start, so no other local user ever gets a window to
    # read the hashes, then swapped in atomically.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps({"devices": devices}, indent=2).encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, PHONES_FILE)


def _device_ttl_seconds() -> float:
    try:
        days = float(config.get("phone.device_ttl_days", DEFAULT_DEVICE_TTL_DAYS))
    except (TypeError, ValueError):
        days = DEFAULT_DEVICE_TTL_DAYS
    return max(days, 0.0) * 86400


def _expires_at(record: dict[str, Any]) -> float:
    """Epoch seconds when this device's credential stops working.

    Computed from `paired_at` + the *current* ttl, so lowering the setting
    takes effect on devices paired earlier. A record whose `paired_at` cannot
    be read is treated as already expired: failing closed means the owner
    re-pairs, never that a credential of unknown age lives on.
    """
    try:
        parsed = datetime.strptime(str(record.get("paired_at", "")), "%Y-%m-%dT%H:%M:%S%z")
        paired = parsed.timestamp()
    except (ValueError, OverflowError):
        return 0.0
    return paired + _device_ttl_seconds()


def _wall() -> float:
    return time.time()


def _drop(device_id: str, devices: dict[str, dict[str, Any]], event: str) -> None:
    """Remove a device from `devices` (already loaded, lock held), persist, and
    cut everything that device holds open. Shared by revoke and expiry."""
    devices.pop(device_id, None)
    _save_devices(devices)
    with _links_lock:
        link = _links.pop(device_id, None)
    if link is not None:
        link.close(event.split(".")[-1])
    # Its UI tokens (and the web sockets opened with them) die with it.
    webauth.revoke_ui_tokens(device_id)
    audit.record(event, device_id=device_id)


def sweep_expired() -> list[str]:
    """Remove every device past its lifetime. Returns the removed ids."""
    with _store_lock:
        devices = _load_devices()
        now = _wall()
        gone = [d for d, rec in devices.items() if _expires_at(rec) <= now]
        for device_id in gone:
            _drop(device_id, devices, "phone.expired")
    return gone


def run_expiry_sweeper(stop: threading.Event) -> None:
    """Hourly sweep, so a phone that stays connected past its lifetime is cut
    off too — authentication alone only runs when it (re)connects."""
    while not stop.is_set():
        try:
            sweep_expired()
        except Exception:
            pass
        stop.wait(SWEEP_INTERVAL)


def list_devices() -> list[dict[str, Any]]:
    """Paired devices, with no credential material."""
    with _store_lock:
        devices = _load_devices()
    with _links_lock:
        links = dict(_links)
    out = []
    for device_id, record in sorted(devices.items()):
        link = links.get(device_id)
        live = link is not None and link.ready and not link.closed
        out.append({
            "device_id": device_id,
            "name": record.get("name", ""),
            "paired_at": record.get("paired_at", ""),
            "last_seen": record.get("last_seen", ""),
            "expires_at": int(_expires_at(record)),
            "connected": live,
            "capabilities": sorted(link.capabilities) if live else [],
        })
    return out


def revoke(device_id: str) -> bool:
    """Forget a device and drop its live socket. Returns False if unknown."""
    with _store_lock:
        devices = _load_devices()
        if device_id not in devices:
            return False
        _drop(device_id, devices, "phone.revoked")
    return True


def authenticate(header: str | None) -> dict[str, Any] | None:
    """Check ``Authorization: Device <id>:<secret>``. Returns the device or None.

    The browser's ``Bearer`` token never matches here: the scheme is different
    and the store only holds device secrets.
    """
    if not header:
        return None
    scheme, _, rest = header.strip().partition(" ")
    if scheme.lower() != DEVICE_AUTH_SCHEME:
        return None
    device_id, sep, secret = rest.strip().partition(":")
    if not sep or not device_id or not secret:
        return None
    with _store_lock:
        devices = _load_devices()
        record = devices.get(device_id)
        # Hash anyway when the id is unknown, so a miss takes the same time as
        # a wrong secret.
        expected = record.get("secret_sha256", "") if record else "0" * 64
        if not secrets.compare_digest(_hash(secret), expected) or record is None:
            audit.record("phone.auth_refused", device_id=device_id[:40])
            return None
        if _expires_at(record) <= _wall():
            # The app gets 401 and shows pairing.
            _drop(device_id, devices, "phone.expired")
            return None
        record["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _save_devices(devices)
    return {"device_id": device_id, "name": record.get("name", "")}


# ---------------------------------------------------------------------------
# Pairing offers — one at a time, one use, short-lived, in memory only
# ---------------------------------------------------------------------------

@dataclass
class _Offer:
    code_sha256: str
    expires: float


_offer: _Offer | None = None
_offer_lock = threading.Lock()


def create_offer(
    host: str,
    port: int,
    *,
    ttl: float | None = None,
    scheme: str = "http",
    cert_sha256: str | None = None,
) -> dict[str, Any]:
    """Make a fresh pairing offer, replacing any outstanding one.

    The returned dict is the QR payload. The code is in it and nowhere else.
    It is not stored and not logged. Under TLS it also carries the scheme and
    the certificate fingerprint the app must pin; plain http carries neither
    fingerprint, and `v` is unchanged (the app accepts offers with or without).
    """
    global _offer
    ttl = float(ttl if ttl is not None else config.get("phone.offer_ttl_seconds", DEFAULT_OFFER_TTL))
    code = secrets.token_urlsafe(16)
    with _offer_lock:
        _offer = _Offer(code_sha256=_hash(code), expires=_clock() + ttl)
    # `offer_host`, not `host`: the audit record already has a `host` field
    # (this machine's name) and a plain `host=` would overwrite it.
    audit.record("phone.offer_created", offer_host=host, port=port, ttl=ttl)
    offer = {
        "v": protocol_version(),
        "service": "kravein",
        "host": host,
        "port": int(port),
        "code": code,
        "expires_at": int(time.time() + ttl),
        "scheme": "https" if scheme == "https" else "http",
    }
    if offer["scheme"] == "https":
        if not cert_sha256:
            raise ValueError("an https offer needs the certificate fingerprint")
        offer["cert_sha256"] = cert_sha256.lower()
    return offer


def redeem_offer(code: Any, device_name: Any) -> dict[str, str]:
    """Exchange an offer code for a device credential. Raises PhoneError on refusal.

    The offer is consumed by the first *valid* redemption. A wrong code does
    not burn it, because otherwise anyone on the network could cancel a
    pairing in progress, but an expired one is discarded.
    """
    global _offer
    if not isinstance(code, str) or not code:
        audit.record("phone.pair_refused", reason="missing code")
        raise PhoneError("pairing code required", code="permission_denied")
    with _offer_lock:
        offer = _offer
        if offer is None:
            reason = "no offer"
        elif _clock() >= offer.expires:
            _offer = None
            reason = "expired"
        elif not secrets.compare_digest(_hash(code), offer.code_sha256):
            reason = "wrong code"
        else:
            _offer = None
            reason = ""
    if reason:
        audit.record("phone.pair_refused", reason=reason)
        raise PhoneError("pairing offer is invalid, used or expired", code="permission_denied")

    name = device_name.strip()[:80] if isinstance(device_name, str) else ""
    name = name or "Android phone"
    device_id = "ph_" + secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with _store_lock:
        devices = _load_devices()
        devices[device_id] = {
            "name": name,
            "secret_sha256": _hash(secret),
            "paired_at": now,
            "last_seen": "",
        }
        _save_devices(devices)
    audit.record("phone.paired", device_id=device_id, name=name)
    return {"device_id": device_id, "device_secret": secret, "name": name}


# ---------------------------------------------------------------------------
# Live links
# ---------------------------------------------------------------------------

@dataclass
class _Call:
    command: str
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    error: PhoneError | None = None


class PhoneLink:
    """One authenticated phone socket.

    Created on the event loop by the server. Inbound text goes through
    :meth:`handle_text` (loop thread). Outbound frames come out of
    :meth:`next_frame`; a ``None`` frame means "close the socket". Callers on
    worker threads use :meth:`request`.
    """

    def __init__(self, device: dict[str, Any], loop: asyncio.AbstractEventLoop) -> None:
        self.device_id: str = device["device_id"]
        self.name: str = device.get("name", "")
        self.capabilities: frozenset[str] = frozenset()
        self.ready = False
        self._loop = loop
        self._loop_thread = threading.get_ident()
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=MAX_PENDING_FRAMES)
        self._pending: dict[str, _Call] = {}
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._closed = threading.Event()
        self.close_reason = ""

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    # -- outbound ---------------------------------------------------------

    def _push(self, payload: dict[str, Any] | None, request_id: str = "") -> None:
        """Enqueue on the loop thread. A full queue fails the request it carried."""
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            if payload is None:
                # Make room for the close sentinel: closing must always win.
                with contextlib.suppress(Exception):
                    self._queue.get_nowait()
                with contextlib.suppress(Exception):
                    self._queue.put_nowait(None)
                return
            self._fail(request_id, PhoneDisconnected("send queue to the phone is full"))

    def _send(self, payload: dict[str, Any] | None, request_id: str = "") -> None:
        """Queue a frame from any thread."""
        if threading.get_ident() == self._loop_thread:
            self._push(payload, request_id)
            return
        try:
            self._loop.call_soon_threadsafe(self._push, payload, request_id)
        except RuntimeError:
            # The loop is gone, so the socket is gone.
            self._closed.set()
            self._fail(request_id, PhoneDisconnected("phone connection is gone"))

    async def next_frame(self) -> dict[str, Any] | None:
        return await self._queue.get()

    # -- requests from worker threads --------------------------------------

    def request(self, command: str, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        """Send ``execute`` and block until its result. Always returns or raises."""
        if threading.get_ident() == self._loop_thread:
            # Blocking the loop would stop the very socket that has to deliver
            # the answer, so this would deadlock until the timeout every time.
            raise PhoneError("phone.call() must not run on the server's event loop")
        if self.closed:
            raise PhoneDisconnected(f"phone {self.name!r} is not connected")

        request_id = f"r{next(self._ids)}"
        pending = _Call(command=command)
        with self._lock:
            self._pending[request_id] = pending
        self._send(make_frame("execute", request_id, command=command, args=args), request_id)

        answered = pending.event.wait(timeout=timeout)
        with self._lock:
            self._pending.pop(request_id, None)
        if not answered:
            raise PhoneTimeout(f"phone {self.name!r} did not answer {command} within {timeout:g}s")
        if pending.error is not None:
            raise pending.error
        return pending.result or {}

    def _fail(self, request_id: str, error: PhoneError) -> None:
        with self._lock:
            pending = self._pending.get(request_id)
        if pending is not None and not pending.event.is_set():
            pending.error = error
            pending.event.set()

    # -- inbound (loop thread) ---------------------------------------------

    def handle_text(self, text: str) -> None:
        """Validate one inbound frame and act on it. Every frame gets an answer
        or resolves a pending call. A malformed one gets an ``error`` frame."""
        try:
            frame_type, request_id, payload = parse_frame(text)
        except ProtocolError as exc:
            audit.record("phone.frame_rejected", device_id=self.device_id, code=exc.code)
            self._send(error_frame(exc.code, str(exc), exc.request_id))
            if exc.code == "frame_too_large":
                # Something is very wrong with the peer; do not keep reading.
                self.close("frame too large")
            return

        if frame_type == "hello":
            if payload["protocol"] != protocol_version():
                self._send(error_frame(
                    "protocol_mismatch",
                    f"server speaks protocol {protocol_version()}", request_id,
                ))
                self.close("protocol mismatch")
                return
            self.capabilities = frozenset(payload["capabilities"])
            self.ready = True
            audit.record(
                "phone.ready", device_id=self.device_id,
                capabilities=sorted(self.capabilities),
            )
            self._send(make_frame(
                "welcome", request_id,
                protocol=protocol_version(), device_id=self.device_id,
                server_name=str(config.get("assistant_name", "Kravein") or "Kravein"),
            ))
            # A phone that connects while a confirmation is open still gets it.
            if _notifications_on():
                from . import events  # lazy: events imports this module
                for confirm_id, info in events.open_confirms():
                    with _links_lock:
                        _relayed.add(confirm_id)
                    self._send(_confirm_request_frame(confirm_id, info))
        elif frame_type == "confirm_reply":
            if not self.ready:
                self._send(error_frame("not_ready", "send hello first", request_id))
                return
            from . import events
            approve = payload["approve"]
            accepted = events.resolve_open(request_id, approve)
            audit.record(
                "phone.confirm_reply", device_id=self.device_id,
                confirm_id=request_id, approve=approve, accepted=accepted,
            )
            if not accepted:
                # Unknown, late or already answered: say so, approve nothing.
                self._send(error_frame(
                    "unknown_request", f"no confirmation is waiting on {request_id!r}", request_id,
                ))
        elif frame_type == "ping":
            self._send(make_frame("pong", request_id))
        elif frame_type == "pong":
            pass
        elif frame_type in ("result", "error"):
            with self._lock:
                pending = self._pending.get(request_id)
            if pending is None or pending.event.is_set():
                if frame_type == "result":
                    # Late (after a timeout) or invented. Say so; never guess
                    # which call it belonged to.
                    self._send(error_frame(
                        "unknown_request", f"no command is waiting on {request_id!r}", request_id,
                    ))
                return
            if frame_type == "error":
                pending.error = PhoneCommandFailed(payload["message"], code=payload["code"])
            elif payload["ok"]:
                data = payload.get("data") or {}
                try:
                    validate_result(pending.command, data)
                except PhoneBadResult as exc:
                    # Fail the caller, not the socket: one bad answer does not
                    # make the phone untrustworthy for other commands, but its
                    # data must never reach a skill unchecked.
                    audit.record(
                        "phone.bad_result", device_id=self.device_id,
                        command=pending.command, reason=str(exc)[:200],
                    )
                    pending.error = exc
                else:
                    pending.result = data
            else:
                pending.error = PhoneCommandFailed(
                    payload.get("error") or f"{pending.command} failed on the phone",
                    code=payload.get("error_code") or "failed",
                )
            pending.event.set()

    # -- teardown ----------------------------------------------------------

    def close(self, reason: str) -> None:
        """Fail every pending call and ask the server to drop the socket. Any thread, idempotent."""
        if self._closed.is_set():
            return
        self._closed.set()
        self.close_reason = reason
        self.ready = False
        with self._lock:
            pending = list(self._pending.values())
        for p in pending:
            if not p.event.is_set():
                p.error = PhoneDisconnected(f"phone {self.name!r} disconnected ({reason})")
                p.event.set()
        with contextlib.suppress(Exception):
            if threading.get_ident() == self._loop_thread:
                self._push(None)
            else:
                self._loop.call_soon_threadsafe(self._push, None)


_links: dict[str, PhoneLink] = {}
_links_lock = threading.Lock()


def attach(link: PhoneLink) -> None:
    """Register a freshly authenticated link. A second socket for the same
    device replaces the first; the old one's pending calls fail."""
    with _links_lock:
        old = _links.get(link.device_id)
        _links[link.device_id] = link
    if old is not None and old is not link:
        old.close("replaced by a new connection")
    audit.record("phone.connected", device_id=link.device_id)


def detach(link: PhoneLink, reason: str = "disconnected") -> None:
    with _links_lock:
        if _links.get(link.device_id) is link:
            _links.pop(link.device_id)
    link.close(reason)
    audit.record("phone.disconnected", device_id=link.device_id, reason=link.close_reason or reason)


_relayed: set[str] = set()


def _notifications_on() -> bool:
    return bool(config.get("phone.confirm_notifications", True))


def _confirm_request_frame(confirm_id: str, info: dict[str, Any]) -> dict[str, Any]:
    spec = protocol()["frames"]["confirm_request"]["payload"]
    danger = str(info["danger"]).lower()
    return make_frame(
        "confirm_request", confirm_id,
        action=str(info["action"])[: spec["action"]["max_length"]],
        detail=str(info["detail"])[: spec["detail"]["max_length"]],
        # Only DESTRUCTIVE and CRITICAL ever reach a confirmer.
        danger="critical" if danger == "critical" else "destructive",
        expires_at=int(info["expires_at"]),
    )


def _ready_links() -> list[PhoneLink]:
    with _links_lock:
        return [l for l in _links.values() if l.ready and not l.closed]


def relay_confirm_request(confirm_id: str, info: dict[str, Any]) -> None:
    """Show a pending confirmation on every connected phone. Any thread."""
    if not _notifications_on():
        return
    payload = _confirm_request_frame(confirm_id, info)
    with _links_lock:
        _relayed.add(confirm_id)
    for link in _ready_links():
        link._send(payload)


def relay_confirm_closed(confirm_id: str, outcome: str) -> None:
    """Tell every phone a confirmation was resolved (browser, phone or timeout)."""
    # Only for requests we relayed; and even if notifications were switched
    # off meanwhile, a delivered request must still be dismissed.
    with _links_lock:
        if confirm_id not in _relayed:
            return
        _relayed.discard(confirm_id)
    payload = make_frame("confirm_closed", confirm_id, outcome=outcome)
    for link in _ready_links():
        link._send(payload)


def connected() -> list[dict[str, Any]]:
    """Phones that have said hello and can take commands."""
    with _links_lock:
        links = list(_links.values())
    return [
        {"device_id": l.device_id, "name": l.name, "capabilities": sorted(l.capabilities)}
        for l in links if l.ready and not l.closed
    ]


def _pick(device: str | None) -> PhoneLink:
    with _links_lock:
        links = [l for l in _links.values() if l.ready and not l.closed]
    if not links:
        raise PhoneNotConnected("no phone is connected")
    if device is None:
        if len(links) == 1:
            return links[0]
        names = ", ".join(sorted(f"{l.name} ({l.device_id})" for l in links))
        raise PhoneAmbiguous(f"several phones are connected; name one: {names}")
    matches = [l for l in links if l.device_id == device]
    if not matches:
        wanted = device.strip().lower()
        matches = [l for l in links if l.name.lower() == wanted]
    if not matches:
        raise PhoneNotConnected(f"no connected phone named {device!r}")
    if len(matches) > 1:
        ids = ", ".join(sorted(l.device_id for l in matches))
        raise PhoneAmbiguous(f"several phones are named {device!r}: {ids}")
    return matches[0]


def call(
    command: str,
    args: dict[str, Any] | None = None,
    *,
    device: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Run one command on a phone and return its ``data``. **Blocks.**

    ``device`` is a device id or name. It may be left out only when exactly
    one phone is connected. Raises a :class:`PhoneError` subclass on every
    failure: not connected, ambiguous, rejected (unknown command, bad args,
    missing capability), timeout, disconnected mid-call, or a failure the
    phone reported. Never hangs past ``timeout``.

    Callers that change the phone's state still owe ``safety.enforce()``;
    this is transport, not policy.
    """
    args = validate_command(command, args)
    link = _pick(device)
    if command not in link.capabilities:
        raise PhoneCommandRejected(
            f"phone {link.name!r} does not support {command}", code="unknown_capability",
        )
    wait = float(timeout if timeout is not None
                 else config.get("phone.command_timeout_seconds", DEFAULT_COMMAND_TIMEOUT))
    started = _clock()
    outcome = "ok"
    try:
        return link.request(command, args, wait)
    except PhoneError as exc:
        outcome = exc.code
        raise
    finally:
        audit.record(
            "phone.command", device_id=link.device_id, command=command, args=args,
            outcome=outcome, seconds=round(_clock() - started, 2),
        )


# -- notify outbox (#62) -------------------------------------------------
#
# `kravein.jobs tick` is a separate systemd process: it cannot reach this
# process's phone sockets. It appends to a small file instead and the server
# polls it. The tick side must stay cheap and must never fail the job, so
# `outbox_append` swallows every error.

OUTBOX_FILE = config.STATE_DIR / "phone_outbox.jsonl"
OUTBOX_MAX_AGE = 600  # seconds; older entries are dropped, not delivered
OUTBOX_MAX_ENTRIES = 100  # trim threshold so the file never grows unbounded
NOTIFY_POLL_SECONDS = 2.0


def _forward_on() -> bool:
    return bool(config.get("phone.forward_notifications", True))


def outbox_append(title: str, body: str, kind: str, *, now: float | None = None) -> bool:
    """Queue one notification for paired phones. Called from the tick process.

    Returns False instead of raising: a full disk or unwritable state dir must
    never turn a delivered reminder into a failed job.
    """
    if not _forward_on():
        return False
    try:
        import fcntl

        at = int(now if now is not None else time.time())
        entry = {"id": secrets.token_hex(8), "title": str(title), "body": str(body),
                 "kind": kind if kind in ("reminder", "job") else "job", "at": at}
        path = OUTBOX_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        # Lock a file we never replace: the trim below swaps the outbox inode.
        lock = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as fh:
                fh.write(json.dumps(entry) + "\n")
            _outbox_trim(path, at)
        finally:
            os.close(lock)
        return True
    except Exception:
        return False


def _outbox_read(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text()
    except OSError:
        return []
    entries = []
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue  # a torn line is skipped, never fatal
        if isinstance(e, dict) and isinstance(e.get("id"), str):
            entries.append(e)
    return entries


def _outbox_trim(path: Path, now: float) -> None:
    """Drop stale entries once the file passes the cap. Caller holds the lock."""
    entries = _outbox_read(path)
    if len(entries) <= OUTBOX_MAX_ENTRIES:
        return
    keep = [e for e in entries if now - float(e.get("at", 0)) <= OUTBOX_MAX_AGE]
    keep = keep[-OUTBOX_MAX_ENTRIES:]
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.writelines(json.dumps(e) + "\n" for e in keep)
    os.replace(tmp, path)


def _notify_frame(entry: dict[str, Any]) -> dict[str, Any]:
    spec = protocol()["frames"]["notify"]["payload"]
    return make_frame(
        "notify", str(entry["id"]),
        title=str(entry.get("title", ""))[: spec["title"]["max_length"]],
        body=str(entry.get("body", ""))[: spec["body"]["max_length"]],
        kind="reminder" if entry.get("kind") == "reminder" else "job",
        at=int(entry.get("at", 0)),
    )


class OutboxRelay:
    """Server side: deliver new outbox entries to every ready phone."""

    def __init__(self) -> None:
        self._stamp: tuple[int, int, int] | None = None
        # Entries that already exist fired before this server started;
        # re-delivering them on every restart would buzz the phone twice.
        self._seen: set[str] = {e["id"] for e in _outbox_read(OUTBOX_FILE)}

    def poll(self, now: float | None = None) -> int:
        """Send what is new. Returns how many entries were relayed."""
        path = OUTBOX_FILE
        try:
            st = path.stat()
        except OSError:
            return 0
        stamp = (st.st_ino, st.st_size, st.st_mtime_ns)
        if stamp == self._stamp:
            return 0  # cheap path: nothing changed since the last poll
        self._stamp = stamp
        now = time.time() if now is None else now
        sent = 0
        entries = _outbox_read(path)
        for e in entries:
            if e["id"] in self._seen:
                continue
            self._seen.add(e["id"])
            if not _forward_on() or now - float(e.get("at", 0)) > OUTBOX_MAX_AGE:
                continue
            frame = _notify_frame(e)
            for link in _ready_links():
                link._send(frame)
            sent += 1
        if len(self._seen) > 1000:
            self._seen = {e["id"] for e in entries}
        return sent

    def run(self, stop: threading.Event) -> None:
        while not stop.wait(NOTIFY_POLL_SECONDS):
            try:
                self.poll()
            except Exception as exc:  # the poller must not die
                audit.record("phone.notify_relay_error", error=str(exc)[:200])
