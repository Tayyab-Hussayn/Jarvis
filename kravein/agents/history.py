"""Persistent per-agent conversation history (SQLite).

Keyed by (profile, thread). A thread's messages and rolling summary are
replaced together in one transaction, so a crash can never leave a thread with
a summary from one save and messages from another, or a tool call without its
result half-written.

One connection per call, with WAL and a busy timeout: several threads (web
sessions, jobs) and processes (the server and a CLI) may touch the file.
``_write_lock`` additionally serialises writers inside this process so
contention shows up as waiting, not as "database is locked".
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

from .. import audit, config

_write_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    profile TEXT NOT NULL,
    thread  TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    updated REAL NOT NULL,
    PRIMARY KEY (profile, thread)
);
CREATE TABLE IF NOT EXISTS messages (
    profile TEXT NOT NULL,
    thread  TEXT NOT NULL,
    seq     INTEGER NOT NULL,
    body    TEXT NOT NULL,
    PRIMARY KEY (profile, thread, seq)
);
"""


_BYTES_TAG = "__b64__"


def _encode(value: Any) -> Any:
    """json ``default=``: bytes round-trip as a tagged base64 object.

    ``default=str`` used to store ``ToolCall.signature`` (Gemini's opaque
    ``thought_signature`` bytes) as its repr, which came back as a str and made
    Gemini answer 400 "Base64 decoding failed" on every later turn (#154).
    """
    if isinstance(value, (bytes, bytearray)):
        return {_BYTES_TAG: base64.b64encode(bytes(value)).decode("ascii")}
    return str(value)


def _restore_signatures(message: dict[str, Any]) -> None:
    """Decode tagged signatures back to bytes; drop any that are not.

    Rows written before the fix hold a repr string like ``"b'\\x12`...'"``.
    Sent to Gemini it fails the whole request, and the repr cannot be turned
    back into the original bytes, so the signature is dropped and audited.
    A call without a signature may be refused by Gemini 3.x, but then the
    failover chain answers; garbage would poison every provider attempt.
    """
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict) or call.get("signature") is None:
            continue
        sig = call["signature"]
        if isinstance(sig, dict) and isinstance(sig.get(_BYTES_TAG), str):
            try:
                call["signature"] = base64.b64decode(sig[_BYTES_TAG], validate=True)
                continue
            except ValueError:
                pass
        call["signature"] = None
        audit.record("agent.history_signature_dropped", tool=call.get("name", ""))


def principal_thread_id(channel: str, principal: str) -> str:
    """A stable thread id for who is talking, never containing the principal.

    The principal is a device id or "browser"; hashing keeps it (and anything
    token-derived) out of the database while staying the same across restarts.
    """
    digest = hashlib.sha256(f"{channel}\0{principal}".encode()).hexdigest()[:20]
    return f"{channel}:{digest}"


def db_path() -> Path:
    return config.STATE_DIR / "agents.db"


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        # Whole conversations and raw tool output live here, unredacted:
        # owner-only from the first byte (SQLite gives -wal/-shm the same mode).
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
    conn = sqlite3.connect(str(path), timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(_SCHEMA)
    return conn


def load(profile: str, thread: str) -> tuple[list[dict[str, Any]], str]:
    """Return ``(messages, summary)``; empty when the thread is new."""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT summary FROM threads WHERE profile=? AND thread=?",
            (profile, thread),
        ).fetchone()
        rows = conn.execute(
            "SELECT body FROM messages WHERE profile=? AND thread=? ORDER BY seq",
            (profile, thread),
        ).fetchall()
    messages: list[dict[str, Any]] = []
    for (body,) in rows:
        try:
            message = json.loads(body)
            if isinstance(message, dict):
                _restore_signatures(message)
            messages.append(message)
        except ValueError:
            # A row we cannot read is dropped, not fatal: the rest of the
            # thread is still better than none. The loop fixes pairing below.
            continue
    return _close_dangling_calls(_drop_orphans(messages)), (row[0] if row else "")


def _drop_orphans(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A thread must start at a user message so no tool result is orphaned."""
    for i, m in enumerate(messages):
        if m.get("role") == "user":
            return messages[i:]
    return []


def _close_dangling_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every tool call without a result one, so the transcript is valid.

    A turn killed between the assistant's tool-call message and its results
    (crash, SIGTERM) is saved as it stood; replayed as-is, every provider
    answers 400 on every later turn.
    """
    if not messages or messages[-1].get("role") != "assistant":
        return messages
    calls = messages[-1].get("tool_calls") or []
    if not calls:
        return messages
    return [*messages, *(
        {"role": "tool", "name": c.get("name", ""),
         "tool_call_id": c.get("id") or c.get("name", ""),
         "content": "Not run — the request was interrupted."}
        for c in calls
    )]


def stamp(profile: str, thread: str) -> float:
    """When the thread was last written (0.0 if it never was).

    An agent compares this with the value it saw at its last load/save to
    notice that another writer (a voice session) appended to its thread.
    """
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT updated FROM threads WHERE profile=? AND thread=?", (profile, thread)
        ).fetchone()
    return float(row[0]) if row else 0.0


def append(profile: str, thread: str, messages: list[dict[str, Any]]) -> float:
    """Add whole messages to the end of a thread; returns the new stamp.

    Voice writes spoken turns here instead of holding the text agent's list:
    one INSERT transaction, so it cannot interleave with a ``save``.
    """
    import time

    now = time.time()
    with _write_lock, closing(_connect()) as conn:
        with conn:
            append_in(conn, profile, thread, messages, now)
    return now


def append_in(conn: sqlite3.Connection, profile: str, thread: str,
              messages: list[dict[str, Any]], now: float | None = None) -> None:
    """``append`` inside the caller's transaction (caller holds ``_write_lock``).

    Lets a task report be marked announced and written into the thread in ONE
    transaction: a crash between the two can no longer drop the report.
    """
    import secrets
    import time

    now = time.time() if now is None else now
    # `xid` lets a running agent recognise a message it did not write and keep
    # it when it next saves the thread (see `Agent._persist`).
    messages = [{**m, "xid": m.get("xid") or secrets.token_hex(6)} for m in messages]
    (top,) = conn.execute(
        "SELECT COALESCE(MAX(seq), -1) FROM messages WHERE profile=? AND thread=?",
        (profile, thread),
    ).fetchone()
    conn.executemany(
        "INSERT INTO messages (profile, thread, seq, body) VALUES (?,?,?,?)",
        [(profile, thread, top + 1 + i, json.dumps(m, ensure_ascii=False, default=_encode))
         for i, m in enumerate(messages)],
    )
    conn.execute(
        "INSERT INTO threads (profile, thread, summary, updated) VALUES (?,?,?,?) "
        "ON CONFLICT(profile, thread) DO UPDATE SET updated=excluded.updated",
        (profile, thread, "", now),
    )
    return now


def save(profile: str, thread: str, messages: list[dict[str, Any]], summary: str) -> float:
    import time

    now = time.time()
    rows = [
        (profile, thread, i, json.dumps(m, ensure_ascii=False, default=_encode))
        for i, m in enumerate(messages)
    ]
    with _write_lock, closing(_connect()) as conn:
        with conn:  # one transaction
            conn.execute(
                "DELETE FROM messages WHERE profile=? AND thread=?", (profile, thread)
            )
            conn.executemany(
                "INSERT INTO messages (profile, thread, seq, body) VALUES (?,?,?,?)", rows
            )
            conn.execute(
                "INSERT INTO threads (profile, thread, summary, updated) VALUES (?,?,?,?) "
                "ON CONFLICT(profile, thread) DO UPDATE SET "
                "summary=excluded.summary, updated=excluded.updated",
                (profile, thread, summary, now),
            )
    return now


def delete(profile: str, thread: str) -> None:
    with _write_lock, closing(_connect()) as conn:
        with conn:
            conn.execute("DELETE FROM messages WHERE profile=? AND thread=?", (profile, thread))
            conn.execute("DELETE FROM threads WHERE profile=? AND thread=?", (profile, thread))
