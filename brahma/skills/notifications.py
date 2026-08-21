"""Notifications, reminders and attention monitoring.

Ports two Windows-bound modules:

  * ``actions/reminder.py`` — used ``winsound.Beep`` and ``win10toast``, and
    scheduled via a detached ``pythonw.exe``. Now uses libnotify plus a
    ``systemd-run --user`` timer, which survives the assistant exiting.
  * ``actions/attention_monitor.py`` — read Windows' toast SQLite database.
    Now watches ``org.freedesktop.Notifications`` on the session bus.
"""

from __future__ import annotations

import re
import shlex
import time
from datetime import datetime, timedelta

from .. import audit
from ..platform import notify, shell
from ..registry import skill
from ..safety import Danger

_DURATION_RE = re.compile(
    r"(?:(?P<hours>\d+)\s*h(?:ours?|rs?)?)?\s*"
    r"(?:(?P<minutes>\d+)\s*m(?:in(?:ute)?s?)?)?\s*"
    r"(?:(?P<seconds>\d+)\s*s(?:ec(?:ond)?s?)?)?",
    re.IGNORECASE,
)


def _parse_delay(text: str) -> int | None:
    """Turn '10m', '1h30m' or '90' into seconds."""
    raw = (text or "").strip().lower()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw) * 60  # a bare number means minutes

    match = _DURATION_RE.fullmatch(raw.replace(" ", ""))
    if match and any(match.groupdict().values()):
        hours = int(match.group("hours") or 0)
        minutes = int(match.group("minutes") or 0)
        seconds = int(match.group("seconds") or 0)
        total = hours * 3600 + minutes * 60 + seconds
        return total or None
    return None


@skill(category="notify", danger=Danger.MODERATE)
def send_notification(
    message: str,
    title: str = "Brahma",
    urgency: str = "normal",
) -> str:
    """Show a desktop notification.

    Args:
        message: Body text.
        title: Notification heading.
        urgency: 'low', 'normal' or 'critical'.
    """
    return notify.send(message, title, urgency=urgency)


@skill(category="notify", danger=Danger.MODERATE)
def set_reminder(message: str, delay: str = "10m", title: str = "Reminder") -> str:
    """Schedule a desktop notification for later.

    Uses a transient systemd user timer, so the reminder still fires if the
    assistant is closed.

    Args:
        message: What to remind you about.
        delay: How long to wait, e.g. '10m', '1h30m', '45s'.
        title: Notification heading.
    """
    seconds = _parse_delay(delay)
    if seconds is None or seconds <= 0:
        return f"Could not understand the delay '{delay}'. Try '10m' or '1h30m'."
    if seconds > 30 * 24 * 3600:
        return "That reminder is more than 30 days out; use a calendar instead."

    fire_at = datetime.now() + timedelta(seconds=seconds)
    notifier = shell.first_available("notify-send", "dunstify")
    if not notifier:
        return "No notification client is installed (need libnotify)."

    if shell.have("systemd-run"):
        result = shell.run([
            "systemd-run", "--user", "--quiet",
            f"--on-active={seconds}",
            f"--unit=brahma-reminder-{int(time.time())}",
            "--description=Brahma reminder",
            notifier, "-u", "critical", "--", title, message,
        ], timeout=15)

        if result.ok:
            audit.record("reminder.scheduled", message=message, fire_at=fire_at.isoformat())
            return f"Reminder set for {fire_at:%H:%M:%S} ({delay}): {message}"

    # No systemd-run: fall back to an in-process timer that dies with us.
    import threading

    def _fire() -> None:
        notify.send(message, title, urgency="critical", timeout_ms=0)

    timer = threading.Timer(seconds, _fire)
    timer.daemon = True
    timer.start()
    audit.record("reminder.scheduled_local", message=message, fire_at=fire_at.isoformat())
    return (
        f"Reminder set for {fire_at:%H:%M:%S} ({delay}): {message}\n"
        "Note: systemd-run was unavailable, so this only fires while the "
        "assistant keeps running."
    )


@skill(category="notify", danger=Danger.SAFE)
def list_reminders() -> str:
    """List reminders that are still pending."""
    if not shell.have("systemctl"):
        return "systemd is unavailable, so scheduled reminders cannot be listed."

    result = shell.run(
        ["systemctl", "--user", "list-timers", "--all", "--no-pager", "--no-legend"],
        timeout=12,
    )
    if not result.ok:
        return "Could not list timers."

    rows = [l for l in result.stdout.splitlines() if "brahma-reminder" in l]
    if not rows:
        return "No pending reminders."
    return f"{len(rows)} pending reminder(s):\n" + "\n".join(f"  {r}" for r in rows)


@skill(category="notify", danger=Danger.DESTRUCTIVE)
def cancel_reminders(_source: str = "local", _confirm: bool = False) -> str:
    """Cancel every pending reminder."""
    from ..safety import Verdict, enforce

    result = shell.run(
        ["systemctl", "--user", "list-units", "--all", "--no-pager", "--no-legend",
         "brahma-reminder-*.timer"],
        timeout=12,
    )
    units = [
        line.split()[0].lstrip("*").strip()
        for line in result.stdout.splitlines() if "brahma-reminder" in line
    ]
    if not units:
        return "No pending reminders to cancel."

    enforce(
        Verdict(danger=Danger.DESTRUCTIVE, reasons=[f"cancels {len(units)} reminder(s)"]),
        f"cancel {len(units)} pending reminder(s)",
        skill="cancel_reminders", source=_source, pre_confirmed=_confirm,
    )

    cancelled = 0
    for unit in units:
        if shell.run(["systemctl", "--user", "stop", unit], timeout=10).ok:
            cancelled += 1
    return f"Cancelled {cancelled} reminder(s)."


# ---------------------------------------------------------------------------
# Attention monitoring
# ---------------------------------------------------------------------------

def _on_notification(note: notify.Notification) -> None:
    """Speak high-priority notifications, matching the original's behaviour."""
    from .. import config

    kind = notify.classify(note)
    if kind == "other":
        return
    if not config.get("voice.enabled", False):
        return

    from ..platform import tts

    if kind == "call":
        tts.speak(f"Incoming call on {note.app}.")
    else:
        summary = note.summary or note.app
        tts.speak(f"New message from {summary}.")


@skill(category="notify", danger=Danger.MODERATE)
def watch_notifications(enabled: bool = True) -> str:
    """Start or stop watching desktop notifications for calls and messages.

    While active, incoming notifications are recorded and (if voice is on)
    announced aloud.

    Args:
        enabled: True to start watching, False to stop.
    """
    watcher = notify.watcher(_on_notification)
    if enabled:
        return watcher.start()
    return watcher.stop()


@skill(category="notify", danger=Danger.SAFE)
def recent_notifications(limit: int = 15, kind: str = "") -> str:
    """Show notifications seen since watching started.

    Args:
        limit: How many to show.
        kind: Filter to 'call', 'message' or 'other'.
    """
    watcher = notify.watcher()
    if not watcher.running and not watcher.history:
        return (
            "Not watching notifications. Start with: watch_notifications enabled=true"
        )

    notes = watcher.recent(limit=max(1, min(int(limit), 100)), kind=kind)
    if not notes:
        scope = f" of kind '{kind}'" if kind else ""
        return f"No notifications recorded{scope} yet."

    lines = [f"{len(notes)} recent notification(s):"]
    for note in notes:
        lines.append(f"  [{notify.classify(note)}] {note.describe()}")
    return "\n".join(lines)


@skill(category="notify", danger=Danger.SAFE)
def notification_status() -> str:
    """Report whether notification watching is active and available."""
    watcher = notify.watcher()
    ok, why = notify.NotificationWatcher.available()
    lines = [
        f"Watching : {'yes' if watcher.running else 'no'}",
        f"Available: {'yes' if ok else f'no — {why}'}",
        f"Recorded : {len(watcher.history)} notification(s) this session",
    ]
    sender = shell.first_available("notify-send", "dunstify")
    lines.append(f"Sender   : {sender or 'none installed'}")
    return "\n".join(lines)


@skill(category="notify", danger=Danger.MODERATE)
def speak(text: str, blocking: bool = False) -> str:
    """Say something out loud through the speakers.

    Args:
        text: What to say.
        blocking: Wait for it to finish before returning.
    """
    from ..platform import tts

    return tts.speak(text, blocking=blocking)


@skill(category="notify", danger=Danger.SAFE)
def stop_speaking() -> str:
    """Stop any speech that is currently playing."""
    from ..platform import tts

    return tts.stop()
