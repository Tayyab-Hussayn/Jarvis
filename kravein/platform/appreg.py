"""Learned app registry: what the owner calls things, remembered per machine.

``~/.local/share/kravein/apps.json`` maps normalised spoken names and aliases
("anti gravity", "agy") to the thing that was actually launched and verified.
It is user data, not repo data: names differ per machine and the file has to
survive updates.

Token cost is zero by design. Lookups are an O(1) dict hit inside
``open_app`` / ``resolve_command``; the model never reads or receives the
list. An entry is only written *after* the launch was verified, so a wrong
guess is never remembered, and a stale entry (desktop file or executable gone)
is dropped on lookup so the normal search runs again.

Concurrency: several Kravein processes (REPL, serve, jobs) may learn at once.
Writers take an exclusive ``flock`` on a sidecar file and replace the data
file atomically, so a reader never sees half a file and two writers never lose
each other's entry.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .. import audit, config

# Keys kept. A registry that grows without bound is a slow memory leak in a
# file read on every launch; least recently used keys go first.
MAX_KEYS = 400


def norm(text: str) -> str:
    """Same normaliser as ``apps._norm`` (#181): "Anti Gravity" == "antigravity"."""
    return re.sub(r"[^0-9a-z]+", "", (text or "").casefold())


def path() -> Path:
    # Resolved per call so tests (and a changed XDG_DATA_HOME) take effect.
    return config.DATA_DIR / "apps.json"


@contextmanager
def _locked() -> Iterator[None]:
    lock = path().with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _load() -> dict[str, dict[str, Any]]:
    """Read the registry. A corrupt file is treated as empty, never fatal."""
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    apps = data.get("apps") if isinstance(data, dict) else None
    if not isinstance(apps, dict):
        return {}
    return {k: v for k, v in apps.items() if isinstance(v, dict)}


def _save(apps: dict[str, dict[str, Any]]) -> None:
    if len(apps) > MAX_KEYS:
        keep = sorted(apps, key=lambda k: apps[k].get("last_used", 0), reverse=True)
        apps = {k: apps[k] for k in keep[:MAX_KEYS]}
    target = path()
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"version": 1, "apps": apps}, indent=1, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, target)  # atomic: readers see the old or the new file


def is_alive(record: dict[str, Any]) -> bool:
    """Does the thing a record points at still exist on this machine?"""
    ident = str(record.get("id") or "")
    if record.get("kind") == "desktop":
        # A desktop id resolves through the XDG data dirs, so ask the index
        # rather than guessing a path.
        from . import apps
        return any(e.id == ident for e in apps.index())
    return bool(ident) and os.path.isfile(ident) and os.access(ident, os.X_OK)


def lookup(name: str) -> dict[str, Any] | None:
    """O(1) hit for a spoken name, or ``None``. Drops a stale entry."""
    key = norm(name)
    if not key:
        return None
    record = _load().get(key)
    if record is None:
        return None
    if not is_alive(record):
        audit.record("apps.registry_stale", name=name, id=record.get("id"))
        _drop([k for k, rec in _load().items() if rec.get("id") == record.get("id")])
        return None
    return record


def touch(name: str) -> None:
    """Count a use of an entry (every alias of it shares the same id)."""
    key = norm(name)
    with _locked():
        apps = _load()
        record = apps.get(key)
        if record is None:
            return
        now = time.time()
        for rec in apps.values():
            if rec.get("id") == record.get("id"):
                rec["uses"] = int(rec.get("uses", 0)) + 1
                rec["last_used"] = now
        _save(apps)


def learn(names: list[str], *, kind: str, ident: str, display: str) -> None:
    """Remember a verified launch under every name it is known by.

    ``kind`` is ``desktop`` (``ident`` = desktop file id) or ``cli``
    (``ident`` = absolute executable path). Call only after the effect was
    verified.
    """
    if kind not in ("desktop", "cli") or not ident:
        return
    now = time.time()
    with _locked():
        apps = _load()
        for name in names:
            key = norm(name)
            if not key:
                continue
            old = apps.get(key, {})
            apps[key] = {
                "kind": kind, "id": ident, "name": display,
                "last_used": now, "uses": int(old.get("uses", 0)) + 1
                if old.get("id") == ident else 1,
            }
        _save(apps)
    audit.record("apps.registry_learn", names=names, kind=kind, id=ident)


def _drop(keys: list[str]) -> int:
    with _locked():
        apps = _load()
        gone = [k for k in keys if k in apps]
        for k in gone:
            del apps[k]
        if gone:
            _save(apps)
    return len(gone)


def forget(name: str) -> str:
    """Remove a name and every other name for the same app. Returns a message."""
    key = norm(name)
    with _locked():
        apps = _load()
        record = apps.get(key)
        if record is None:
            return f"'{name}' is not in the learned app registry."
        ident = record.get("id")
        gone = [k for k, rec in apps.items() if rec.get("id") == ident]
        for k in gone:
            del apps[k]
        _save(apps)
    audit.record("apps.registry_forget", name=name, id=ident, keys=gone)
    return f"Forgot '{name}' ({record.get('name', ident)}); it will be searched for again next time."
