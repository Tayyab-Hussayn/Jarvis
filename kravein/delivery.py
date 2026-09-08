"""Telling the user something happened, from a process nobody is watching.

Lifted out of `jobs.py` so the routine engine can reach it without importing
the scheduler. Both run inside the same minutely tick and need the same
escalation, and the alternative — `routines` importing `jobs` for one function
— is how two subsystems that share a timer quietly grow into one.

**Every import here is lazy.** A tick that finds nothing due must not load the
notification backend, the TTS engine or urllib. This module is imported on the
cheap path; its contents are not.

**Delivery is layered, and only the first layer is guaranteed.** A desktop
notification works with nothing else running, so it is the floor. Speech needs
an audio stack; the web push needs `kravein serve` to be up; WhatsApp (later)
needs a linked session. Each is additive and each failure is swallowed and
recorded rather than raised — a reminder that does not arrive because the
optional half of its delivery failed is the worst outcome available.
"""

from __future__ import annotations

import json
from typing import Any

from . import config

# Channels, cheapest first. `escalate` walks this list.
NOTIFY = "notify"
SPEAK = "speak"
WHATSAPP = "whatsapp"
CHANNELS = (NOTIFY, SPEAK, WHATSAPP)


def send_notification(text: str, title: str, *, urgency: str = "normal",
                      timeout_ms: int = 0) -> str:
    """Desktop notification. Returns "" on success, or the error."""
    try:
        from .platform import notify

        notify.send(text or "(nothing to say)", title,
                    urgency=urgency, timeout_ms=timeout_ms)
        return ""
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def say(text: str) -> str:
    """Speak it. Never blocking — a tick must not wait on the audio device."""
    try:
        from .platform import tts

        tts.speak(text, blocking=False)
        return ""
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def send_whatsapp(text: str, *, ref: str = "") -> str:
    """Hand off to the resident WhatsApp service. "" on success, else why not.

    Never loads `neonize` — that lives on the far side of a unix socket, in a
    process that stays connected, because `connect()` blocks for the whole
    session and a 50ms tick cannot host one.
    """
    try:
        from .platform import whatsapp

        sent, error = whatsapp.send(text, ref=ref)
        return "" if sent else error
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def push(path: str, payload: dict[str, Any], *, timeout: float = 3.0) -> bool:
    """Best-effort POST to a running `kravein serve`.

    The tick is its own short-lived process, so it cannot reach a browser
    directly — it posts to the local server, which fans the event out over the
    websockets it holds. A refused connection means the UI simply is not
    running, which is the normal case and not worth reporting anywhere.
    """
    try:
        import urllib.request

        from . import webauth

        token = webauth.read_token()
        if not token:
            return False
        port = int(config.get("web.port", 8787) or 8787)
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                # Explicit, because webauth checks Host as part of the
                # DNS-rebinding defence and urllib would otherwise omit it.
                "Host": f"127.0.0.1:{port}",
            },
            method="POST",
        )
        urllib.request.urlopen(request, timeout=timeout).close()
        return True
    except Exception:
        return False


def deliver(
    text: str,
    *,
    title: str = "Kravein",
    failed: bool = False,
    channels: tuple[str, ...] | list[str] = (NOTIFY,),
    urgency: str = "",
    timeout_ms: int = 0,
    push_path: str = "",
    push_payload: dict[str, Any] | None = None,
    ref: str = "",
) -> dict[str, str]:
    """Say one thing through the channels asked for.

    Returns a channel → error map, empty when everything worked. Callers record
    it; nothing here raises, because the caller is a timer with no user
    attached and a traceback would land in the journal and nowhere else.
    """
    problems: dict[str, str] = {}
    wanted = tuple(channels) or (NOTIFY,)

    if NOTIFY in wanted:
        error = send_notification(
            text,
            f"{title} — failed" if failed else title,
            urgency=urgency or ("critical" if failed else "normal"),
            timeout_ms=timeout_ms,
        )
        if error:
            problems[NOTIFY] = error

    # A failure notice is not read aloud and not sent on. It is already
    # critical-urgency and sticky; escalating an error report through every
    # channel turns one problem into three interruptions.
    if not failed:
        if SPEAK in wanted:
            error = say(text)
            if error:
                problems[SPEAK] = error
        if WHATSAPP in wanted:
            error = send_whatsapp(text, ref=ref)
            if error:
                problems[WHATSAPP] = error

    if push_path:
        push(push_path, push_payload or {})

    return problems


def escalation(step: int, *, escalate: bool, insist: bool = False) -> tuple[str, ...]:
    """Which channels the nth reminder uses. `step` is 1-based.

    Without `escalate` every reminder is a notification: repetition alone, no
    creeping intrusiveness.

    With it, the ladder climbs — notification, then speech, then WhatsApp — so
    a routine never opens with the loudest thing it has. `insist` starts one
    rung up, because that flag exists precisely for the steps that must not be
    missed quietly.
    """
    if not escalate:
        return (NOTIFY,)
    rung = step + (1 if insist else 0)
    if rung <= 1:
        return (NOTIFY,)
    if rung == 2:
        return (NOTIFY, SPEAK)
    return (NOTIFY, SPEAK, WHATSAPP)
