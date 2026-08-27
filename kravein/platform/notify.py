"""Desktop notifications — sending and watching.

Two halves, both replacing Windows mechanisms:

  * Sending replaces ``win10toast``.
  * Watching replaces ``attention_monitor.py``, which read Windows' toast
    SQLite database (``wpndatabase.db``). The Linux equivalent is the
    ``org.freedesktop.Notifications`` D-Bus interface: we snoop the session
    bus for ``Notify`` method calls, which is how every notification on the
    desktop is delivered.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .. import audit, config
from . import shell

URGENCY = {"low": "low", "normal": "normal", "critical": "critical"}


def send(
    message: str,
    title: str = "Kravein",
    *,
    urgency: str = "normal",
    timeout_ms: int = 6000,
    icon: str = "",
) -> str:
    """Post a desktop notification."""
    tool = shell.first_available("notify-send", "dunstify")
    if not tool:
        return "No notification daemon client found (install libnotify)."

    args = [tool, "-u", URGENCY.get(urgency, "normal"), "-t", str(timeout_ms)]
    if icon:
        args += ["-i", icon]
    args += ["--", title, message]

    result = shell.run(args, timeout=8)
    if not result.ok:
        return f"Could not send notification: {result.output}"
    return "Notification sent."


# ---------------------------------------------------------------------------
# Watching the bus
# ---------------------------------------------------------------------------

@dataclass
class Notification:
    """A notification observed on the session bus."""

    app: str
    summary: str
    body: str
    timestamp: float = field(default_factory=time.time)

    @property
    def text(self) -> str:
        return f"{self.summary} {self.body}".strip()

    def describe(self) -> str:
        when = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        parts = [f"[{when}] {self.app}"]
        if self.summary:
            parts.append(self.summary)
        if self.body:
            parts.append(self.body)
        return " · ".join(parts)


# Messaging apps worth surfacing, mirroring the original's _APP_ALIASES.
MESSAGING_APPS = {
    "discord", "whatsapp", "telegram", "signal", "slack", "element",
    "thunderbird", "evolution", "geary", "mail", "gmail", "teams",
    "skype", "zoom", "messenger", "instagram", "fractal", "nheko",
}

CALL_HINTS = (
    "incoming call", "calling", "is calling", "video call", "voice call",
    "ringing", "wants to talk", "incoming video", "incoming voice",
)

MESSAGE_HINTS = (
    "new message", "sent a message", "mentioned you", "replied",
    "unread", "dm from", "message from",
)


def classify(note: Notification) -> str:
    """Label a notification: 'call', 'message' or 'other'."""
    haystack = f"{note.app} {note.text}".lower()
    if any(hint in haystack for hint in CALL_HINTS):
        return "call"
    app_lower = note.app.lower()
    if any(app in app_lower for app in MESSAGING_APPS):
        return "message"
    if any(hint in haystack for hint in MESSAGE_HINTS):
        return "message"
    return "other"


# dbus-monitor emits a multi-line block per method call; this pulls out the
# positional string args of org.freedesktop.Notifications.Notify.
_STRING_RE = re.compile(r'^\s*string\s+"(.*)"\s*$')


class NotificationWatcher:
    """Background thread that reports notifications as they appear.

    Uses ``dbus-monitor`` rather than a Python D-Bus binding so the watcher
    has no hard dependency beyond ``dbus`` itself.
    """

    def __init__(self, on_notification: Callable[[Notification], None]):
        self._callback = on_notification
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self.history: list[Notification] = []
        self._history_lock = threading.Lock()

    @staticmethod
    def available() -> tuple[bool, str]:
        if not shutil.which("dbus-monitor"):
            return False, "dbus-monitor is not installed (package: dbus)"
        import os

        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            return False, "no session D-Bus is available"
        return True, ""

    def start(self) -> str:
        ok, why = self.available()
        if not ok:
            return f"Notification watching unavailable: {why}."
        if self._thread and self._thread.is_alive():
            return "Notification watcher is already running."

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="kravein-notify-watch", daemon=True
        )
        self._thread.start()
        audit.record("notify.watch_start")
        return "Watching desktop notifications."

    def stop(self) -> str:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    self._proc.kill()
                except OSError:
                    pass
        audit.record("notify.watch_stop")
        return "Stopped watching notifications."

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def recent(self, limit: int = 20, kind: str = "") -> list[Notification]:
        with self._history_lock:
            items = list(self.history)
        if kind:
            items = [n for n in items if classify(n) == kind]
        return items[-limit:]

    def _run(self) -> None:
        rule = (
            "interface='org.freedesktop.Notifications',"
            "member='Notify',eavesdrop=true"
        )
        try:
            self._proc = subprocess.Popen(
                ["dbus-monitor", "--session", rule],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            audit.record("notify.watch_error", error=str(exc))
            return

        strings: list[str] = []
        in_call = False

        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            if self._stop.is_set():
                break

            if "member=Notify" in line:
                in_call = True
                strings = []
                continue

            if not in_call:
                continue

            match = _STRING_RE.match(line)
            if match:
                strings.append(match.group(1))
                continue

            # A blank line or the next header ends the argument block.
            if line.strip() == "" or line.startswith("method call"):
                if len(strings) >= 3:
                    self._emit(strings)
                in_call = "method call" in line
                strings = []

        self._proc = None

    def _emit(self, strings: list[str]) -> None:
        # Notify signature: app_name, app_icon, summary, body, ...
        # (replaces_id is a uint32 so it never lands in our string list)
        app = strings[0] if strings else ""
        summary = strings[2] if len(strings) > 2 else ""
        body = strings[3] if len(strings) > 3 else ""

        # Ignore the assistant's own notifications to avoid a feedback loop.
        if app.lower().startswith("kravein"):
            return

        note = Notification(
            app=app,
            summary=_unescape(summary),
            body=_unescape(body),
        )

        with self._history_lock:
            self.history.append(note)
            if len(self.history) > 500:
                del self.history[:-500]

        audit.record(
            "notify.received",
            app=note.app, summary=note.summary,
            kind=classify(note),
        )

        try:
            self._callback(note)
        except Exception as exc:
            audit.record("notify.callback_error", error=str(exc))


def _unescape(text: str) -> str:
    """dbus-monitor escapes quotes and backslashes in string literals."""
    return text.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n")


_watcher: NotificationWatcher | None = None


def watcher(on_notification: Callable[[Notification], None] | None = None) -> NotificationWatcher:
    """Get (or lazily create) the process-wide watcher."""
    global _watcher
    if _watcher is None:
        _watcher = NotificationWatcher(on_notification or (lambda n: None))
    elif on_notification is not None:
        _watcher._callback = on_notification
    return _watcher
