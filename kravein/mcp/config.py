"""``~/.config/kravein/mcp.json``: the owner's MCP servers and approvals (#243).

Everything that changes this file is an OWNER action, reached only from the
``kravein mcp`` CLI. No skill, agent, profile or MCP server may call the
writers below (a test scans for imports), and the file sits in Kravein's
config tree, which the file tools and shell writes already refuse. An agent
that wants a server may only ``propose`` it.

Secrets never live here: an env value ``key:<name>`` and ``auth_key`` name an
entry of ``api_keys.json`` (read at connect time through ``config.api_key``).

Shape::

    {"servers": {"outreach": {
        "transport": "stdio", "command": ["/usr/bin/node", "server.js"],
        "env": {"PATH": "inherit", "OUTREACH_TOKEN": "key:outreach_token"},
        "sandbox": false, "timeout": 60, "enabled": true,
        "tools": {"send_message": {"pin": "...", "class": "GUARDED",
                                   "outward": true, "definition": {...},
                                   "approved_at": 1700000000.0}}}}}
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

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")       # no "_": "__" splits mcp__s__t
CLASSES = ("SAFE", "GUARDED", "DESTRUCTIVE")
DEFAULT_TIMEOUT = 60.0


def path() -> Path:
    return Path(config.CONFIG_DIR) / "mcp.json"


def load() -> dict[str, Any]:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"servers": {}}
    servers = data.get("servers") if isinstance(data, dict) else None
    return {"servers": {k: v for k, v in (servers or {}).items()
                        if isinstance(v, dict) and NAME_RE.match(str(k))}}


def servers() -> dict[str, dict[str, Any]]:
    return load()["servers"]


def mtime() -> float:
    try:
        return path().stat().st_mtime
    except OSError:
        return 0.0


@contextmanager
def _locked() -> Iterator[dict[str, Any]]:
    """Load, let the caller change, save atomically (0600), under a file lock."""
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target.with_name("mcp.json.lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load()
        yield data
        tmp = target.with_name("mcp.json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)


def _check_env(env: dict[str, str]) -> dict[str, str]:
    out = {}
    for name, value in (env or {}).items():
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(name)):
            raise ValueError(f"bad environment variable name '{name}'")
        out[str(name)] = str(value)
    return out


# -- owner actions (CLI only) -------------------------------------------------

def add_server(name: str, *, command: list[str] | None = None, url: str = "",
               env: dict[str, str] | None = None, auth_key: str = "",
               sandbox: bool = False, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Add a server, disabled-by-approval: no tool runs until ``approve``."""
    if not NAME_RE.match(name or ""):
        raise ValueError("server name: lowercase letters, digits and '-', up to 32, starting with a letter")
    if bool(command) == bool(url):
        raise ValueError("give exactly one of a command (stdio) or a URL (Streamable HTTP)")
    if url and not re.match(r"^https?://", url):
        raise ValueError("the URL must start with http:// or https://")
    if url and url.startswith("http://") and not re.match(r"^http://(127\.0\.0\.1|localhost|\[::1\])[:/]", url):
        raise ValueError("plain http:// is only allowed for a server on this machine; use https://")
    spec: dict[str, Any] = {
        "transport": "stdio" if command else "http",
        "command": list(command or []), "url": url, "env": _check_env(env or {}),
        "auth_key": auth_key, "sandbox": bool(sandbox),
        "timeout": max(1.0, min(float(timeout), 600.0)), "enabled": True, "tools": {},
        "added_at": time.time(),
    }
    with _locked() as data:
        if name in data["servers"]:
            raise ValueError(f"server '{name}' already exists (remove it first)")
        data["servers"][name] = spec
    audit.record("mcp.server_added", server=name, transport=spec["transport"],
                 env_names=sorted(spec["env"]), sandbox=spec["sandbox"])
    return spec


def set_enabled(name: str, enabled: bool) -> None:
    with _locked() as data:
        if name not in data["servers"]:
            raise ValueError(f"no server '{name}'")
        data["servers"][name]["enabled"] = bool(enabled)
    audit.record("mcp.server_enabled" if enabled else "mcp.server_disabled", server=name)


def remove_server(name: str) -> None:
    with _locked() as data:
        if data["servers"].pop(name, None) is None:
            raise ValueError(f"no server '{name}'")
    audit.record("mcp.server_removed", server=name)


def record_approval(name: str, approvals: dict[str, dict[str, Any]],
                    drop: list[str] | tuple[str, ...] = ()) -> None:
    """Store the owner's decisions: ``{tool: {pin, class, outward, definition}}``.

    ``drop`` removes approvals of tools the server no longer lists.
    """
    for tool, entry in approvals.items():
        if entry.get("class") not in CLASSES:
            raise ValueError(f"{tool}: class must be one of {', '.join(CLASSES)}")
    with _locked() as data:
        spec = data["servers"].get(name)
        if spec is None:
            raise ValueError(f"no server '{name}'")
        tools = spec.setdefault("tools", {})
        for tool in drop:
            tools.pop(tool, None)
        for tool, entry in approvals.items():
            tools[tool] = {**entry, "approved_at": time.time()}
    audit.record("mcp.tools_approved", server=name,
                 tools={t: {"class": e["class"], "outward": bool(e.get("outward"))}
                        for t, e in approvals.items()}, dropped=list(drop))
