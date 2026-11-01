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

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

from .. import config

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
            messages.append(json.loads(body))
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


def save(profile: str, thread: str, messages: list[dict[str, Any]], summary: str) -> None:
    import time

    rows = [
        (profile, thread, i, json.dumps(m, ensure_ascii=False, default=str))
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
                (profile, thread, summary, time.time()),
            )


def delete(profile: str, thread: str) -> None:
    with _write_lock, closing(_connect()) as conn:
        with conn:
            conn.execute("DELETE FROM messages WHERE profile=? AND thread=?", (profile, thread))
            conn.execute("DELETE FROM threads WHERE profile=? AND thread=?", (profile, thread))
