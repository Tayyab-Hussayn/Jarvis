"""Talking to the resident WhatsApp service over a unix socket.

**This module never imports neonize.** That library ships an 8 MB Go shared
library and `connect()` blocks its thread for the entire session — it is not
"open a connection and return". So the session lives in one always-on process
(`kravein.whatsapp_service`, started by `kravein-whatsapp.service`) and
everything else talks to it through here. Keeping the import on the far side of
the socket is what lets `delivery.py` call `send()` from the minutely tick
without loading a Go runtime 1,440 times a day.

**The socket is the authentication.** `$XDG_RUNTIME_DIR/kravein/whatsapp.sock`,
mode 0600, in a directory only this user can enter. That is process-to-process
on one machine, so filesystem permissions do the whole job: no token to leak,
no port to bind, nothing reachable from the network, and it vanishes on reboot
with the runtime directory. The web push uses HTTP with a bearer token because
a browser is on the other end; this does not, and should not.

**Everything degrades to "unavailable".** A missing socket means the service is
not running, which is the normal case on a machine that never linked a phone.
Nothing here raises; callers get a state or a refusal and carry on. WhatsApp is
an *additional* channel — the desktop notification has already gone out.
"""

from __future__ import annotations

import json
import socket
from typing import Any

from .. import config

SOCKET = config.RUNTIME_DIR / "whatsapp.sock"

# Short. Every caller is either the minutely tick or a web request, and both
# would rather report "not reachable" than hold a thread. Pairing is the one
# slow operation and it is polled, not waited on.
TIMEOUT = 5.0

UNAVAILABLE = {
    "state": "unavailable",
    "detail": "the WhatsApp service is not running",
    "linked": False,
}


def _ask(payload: dict[str, Any], *, timeout: float = TIMEOUT) -> dict[str, Any] | None:
    """One request, one reply. None when the service cannot be reached."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(SOCKET))
            sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))

            # Read to the newline rather than a fixed buffer: a QR payload is
            # a few hundred bytes and a state reply is short, but a partial
            # read that happens to split a JSON object would be an
            # intermittent parse failure, which is the worst kind.
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
            raw = b"".join(chunks).split(b"\n", 1)[0]
        return json.loads(raw.decode("utf-8")) if raw else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def available() -> bool:
    """Is the service reachable at all? Cheap — no round trip."""
    return SOCKET.exists()


def state() -> dict[str, Any]:
    """Connection state, for the panel and `kravein whatsapp status`.

    Includes the current pairing QR when one is live. That code is a login
    credential for its rotation window, so it is returned only to an already
    authenticated caller and is never written to the audit log.
    """
    if not available():
        return dict(UNAVAILABLE)
    reply = _ask({"op": "state"})
    return reply if reply else dict(UNAVAILABLE)


def qr_modules(code: str) -> list[str]:
    """The pairing code as rows of "0"/"1", ready to draw as squares.

    Encoded here rather than in the browser so the frontend needs no QR
    library: `qrcode` is already a Python dependency, used by the CLI for the
    same job. Rows are strings because a list of 1,300 booleans is a lot of
    JSON for something that is one bit per entry.
    """
    if not code:
        return []
    try:
        import qrcode
    except ImportError:
        return []
    try:
        qr = qrcode.QRCode(border=0)   # the quiet zone is added by the renderer
        qr.add_data(code)
        qr.make(fit=True)
        return ["".join("1" if cell else "0" for cell in row) for row in qr.get_matrix()]
    except Exception:
        return []


def begin_link() -> dict[str, Any]:
    """Start pairing. The QR arrives through `state()`, not in this reply."""
    if not available():
        return {"ok": False, "error": UNAVAILABLE["detail"]}
    reply = _ask({"op": "link"}, timeout=10.0)
    return reply if reply else {"ok": False, "error": "the service did not answer"}


def unlink() -> dict[str, Any]:
    """Log the device out and discard the session."""
    if not available():
        return {"ok": False, "error": UNAVAILABLE["detail"]}
    reply = _ask({"op": "unlink"}, timeout=15.0)
    return reply if reply else {"ok": False, "error": "the service did not answer"}


def send(text: str, *, to: str = "owner", ref: str = "") -> tuple[bool, str]:
    """Send one message. Returns (sent, error).

    Never raises, and the caller must treat failure as "this channel is not
    available right now" rather than as the reminder failing — by the time this
    is called the desktop notification has already been shown.
    """
    if not available():
        return False, str(UNAVAILABLE["detail"])
    reply = _ask({"op": "send", "to": to, "text": text, "ref": ref}, timeout=15.0)
    if not reply:
        return False, "the service did not answer"
    if reply.get("ok"):
        return True, ""
    return False, str(reply.get("error") or "unknown error")
