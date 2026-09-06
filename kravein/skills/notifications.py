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
    title: str = "Kravein",
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
    """Remind you about something later with a desktop notification.

    Fires even if Kravein is closed. For a specific clock time, a repeating
    reminder, or anything that should actually *do* something rather than just
    show a message, use `schedule_job`.

    Args:
        message: What to remind you about.
        delay: When, e.g. '10m', '1h30m', '45s', or a clock time like '5pm'.
        title: Notification heading.
    """
    # Migrated off `systemd-run --on-active=` on 2026-09-06. That created a
    # *transient* unit, and transient units do not survive a reboot — a
    # reminder set for tomorrow morning vanished silently if the machine was
    # restarted overnight. The job store plus the minutely tick has no such
    # hole, and it is one mechanism instead of two.
    from .schedule import schedule_job

    return schedule_job(
        prompt=message, when=delay, kind="notify", title=title or "Reminder"
    )


@skill(category="notify", danger=Danger.SAFE)
def list_reminders() -> str:
    """List reminders that are still pending."""
    from .schedule import list_jobs

    return list_jobs()


@skill(category="notify", danger=Danger.DESTRUCTIVE)
def cancel_reminders(_source: str = "local", _confirm: bool = False) -> str:
    """Cancel every pending reminder."""
    from .schedule import cancel_job

    return cancel_job(job_id="", _source=_source, _confirm=_confirm)


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
