"""Append-only audit trail.

Every skill invocation, every shell command and every confirmation decision
is written here as one JSON object per line. The file is opened with O_APPEND
so concurrent writers (REPL + daemon) interleave safely, and each record is
written in a single write() call to stay atomic under PIPE_BUF.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import config

_lock = threading.Lock()
_fd: int | None = None
_session_id = f"{int(time.time())}-{os.getpid()}"

MAX_VALUE_CHARS = 2000


def session_id() -> str:
    return _session_id


def _open() -> int | None:
    global _fd
    if _fd is not None:
        return _fd
    try:
        config.ensure_dirs()
        _fd = os.open(
            config.AUDIT_LOG,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
    except OSError as exc:
        print(f"[audit] could not open audit log: {exc}")
        _fd = None
    return _fd


def _truncate(value: Any) -> Any:
    """Keep the log readable and bounded without losing the shape of data."""
    if isinstance(value, str):
        if len(value) > MAX_VALUE_CHARS:
            return value[:MAX_VALUE_CHARS] + f"…[+{len(value) - MAX_VALUE_CHARS} chars]"
        return value
    if isinstance(value, dict):
        return {k: _truncate(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_truncate(v) for v in value][:50]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _truncate(repr(value))


# Keys whose values must never reach the log.
_REDACT_KEYS = {
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "gemini_api_key", "openrouter_api_key", "authorization", "cookie",
    "private_key", "passphrase", "credential", "credentials",
}


def _redact(data: Any) -> Any:
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            if str(key).lower() in _REDACT_KEYS:
                out[key] = "***redacted***"
            else:
                out[key] = _redact(value)
        return out
    if isinstance(data, list):
        return [_redact(v) for v in data]
    return data


def record(event: str, **fields: Any) -> None:
    """Write one audit record. Never raises."""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "epoch": round(time.time(), 3),
        "session": _session_id,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "event": event,
    }
    entry.update(_truncate(_redact(fields)))

    try:
        line = (json.dumps(entry, ensure_ascii=False, default=str) + "\n").encode("utf-8")
    except (TypeError, ValueError):
        line = (json.dumps({**entry, "_serialize_error": True}) + "\n").encode("utf-8")

    with _lock:
        fd = _open()
        if fd is None:
            return
        try:
            os.write(fd, line)
        except OSError as exc:
            print(f"[audit] write failed: {exc}")


@contextmanager
def action(skill: str, params: dict[str, Any], source: str = "local") -> Iterator[dict]:
    """Audit a skill invocation, capturing outcome and duration.

    Yields a mutable dict; set ``ctx['result']`` to have it logged.
    """
    started = time.monotonic()
    record("skill.start", skill=skill, params=params, source=source)
    ctx: dict[str, Any] = {"result": None}
    try:
        yield ctx
    except BaseException as exc:
        record(
            "skill.error",
            skill=skill,
            source=source,
            error_type=type(exc).__name__,
            error=str(exc),
            duration_ms=round((time.monotonic() - started) * 1000, 1),
        )
        raise
    else:
        record(
            "skill.ok",
            skill=skill,
            source=source,
            result=ctx.get("result"),
            duration_ms=round((time.monotonic() - started) * 1000, 1),
        )


def tail(limit: int = 50, event_prefix: str = "") -> list[dict]:
    """Read back the most recent audit records."""
    path = Path(config.AUDIT_LOG)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    out: list[dict] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event_prefix and not str(entry.get("event", "")).startswith(event_prefix):
            continue
        out.append(entry)
        if len(out) >= limit:
            break
    out.reverse()
    return out
