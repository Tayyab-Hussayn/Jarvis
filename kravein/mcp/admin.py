"""Owner-side MCP actions behind ``kravein mcp`` (#243). CLI only.

``approve`` connects to the server, shows every tool that is new or changed
(the capped description the model would see, the schema size, a diff for a
changed one, and the server's own hints labelled as a suggestion), and stores
the owner's class and outward flag with the pin. The owner's class defaults
to GUARDED; outward defaults to yes, the safer reading.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from . import config, pins
from .manager import Manager


def server_access(spec: dict[str, Any]) -> list[str]:
    """What a server is given: every env var and where its value comes from,
    the bearer key, the sandbox. A `key:` value is named, never printed."""
    out = []
    for var, value in sorted((spec.get("env") or {}).items()):
        value = str(value)
        if value == "inherit":
            out.append(f"env {var}: copied from Kravein's own environment")
        elif value.startswith("key:"):
            out.append(f"env {var}: the secret '{value[4:]}' from api_keys.json")
        else:
            out.append(f"env {var}: the literal value {value[:60]!r}")
    if spec.get("auth_key"):
        out.append(f"sends the secret '{spec['auth_key']}' as its bearer token")
    out.append("sandboxed (no network)" if spec.get("sandbox") else "not sandboxed")
    return out


def describe_tool(defn: dict[str, Any], old: dict[str, Any] | None = None) -> str:
    clean = pins.sanitized(defn)
    hints = defn.get("_hints") or {}
    lines = [f"  tool: {defn['name']}"]
    if clean is None:
        lines.append("    (schema too large or not an object: will not be registered)")
    else:
        # Everything the model will read on every turn, not just the names:
        # nested descriptions, enums, defaults and titles are server text too.
        desc, params = clean
        lines.append(f"    description: {desc}")
        lines.append("    arguments (exactly as the model will see them):")
        lines += ["      " + ln for ln in json.dumps(params, indent=1, sort_keys=True,
                                                     ensure_ascii=False).splitlines()]
    if hints:
        lines.append("    server says (a suggestion, not trusted): "
                     + ", ".join(f"{k}={v}" for k, v in sorted(hints.items())))
    if old is not None:
        lines.append("    CHANGED since approval:")
        lines += ["      " + ln for ln in pins.diff_text(old, pins.definition(defn)).splitlines()]
    return "\n".join(lines)


def approve(name: str, *, classes: dict[str, str] | None = None, inward: set[str] | None = None,
            scopes: dict[str, list[str]] | None = None,
            yes: bool = False, ask: Callable[[str], str] = input,
            out: Callable[[str], None] = print, manager: Manager | None = None) -> list[str]:
    """Connect ``name``, show what is pending, and approve it. Returns the
    approved tool names (empty when the owner says no).

    ``scopes`` = the arguments an "Always allow" grant for that tool is bound
    to (``{"send_message": ["campaign"]}``: one grant per campaign, any text).
    None = every argument, the narrowest grant (§13.6)."""
    classes = {k: v.upper() for k, v in (classes or {}).items()}
    inward = set(inward or ())
    scopes = {k: [s for s in v if s] for k, v in (scopes or {}).items()}
    for tool, cls in classes.items():
        if cls not in config.CLASSES:
            raise ValueError(f"{tool}: class must be one of {', '.join(config.CLASSES)}")
    own = manager is None
    manager = manager or Manager()
    try:
        conn = manager.connect_once(name)
        if conn.status != "ready":
            raise RuntimeError(f"could not connect to '{name}': {conn.error or conn.status}")
        spec = config.servers()[name]
        approved = spec.get("tools") or {}
        pending = conn.report.get("new", []) + conn.report.get("changed", [])
        removed = conn.report.get("removed", [])
        if not pending and not removed:
            out(f"'{name}': every listed tool is already approved and unchanged.")
            return []
        out(f"MCP server '{name}' gets:")
        for line in server_access(spec):
            out(f"  {line}")
        out(f"MCP server '{name}' wants these tools approved:")
        for tool, wanted in scopes.items():
            props = ((conn.listed.get(tool) or {}).get("input_schema") or {}).get("properties", {}) or {}
            unknown = set(wanted) - set(props)
            if tool not in conn.listed or unknown:
                raise ValueError(f"--scope {tool}: unknown tool or argument(s) {', '.join(sorted(unknown))}")
        for tool in pending:
            old = (approved.get(tool) or {}).get("definition")
            out(describe_tool(conn.listed[tool], old))
            scope = ", ".join(scopes.get(tool) or []) or "every argument"
            out(f"    class: {classes.get(tool, 'GUARDED')}   outward: "
                f"{'no' if tool in inward else 'yes'}   Always-allow scope: {scope}")
        if removed:
            out(f"  no longer listed (approval will be dropped): {', '.join(removed)}")
        if not yes and ask("Approve exactly this? [y/N] ").strip().lower() not in ("y", "yes"):
            out("Nothing approved.")
            return []
        decisions = {}
        for tool in pending:
            defn = {k: v for k, v in conn.listed[tool].items() if not k.startswith("_")}
            decisions[tool] = {
                "pin": pins.pin(defn), "class": classes.get(tool, "GUARDED"),
                "outward": tool not in inward, "definition": defn,
                "scope": list(scopes.get(tool) or []),
            }
        config.record_approval(name, decisions, drop=removed)
        return sorted(decisions)
    finally:
        if own:
            manager.shutdown()


def list_text(manager: Manager | None = None) -> str:
    """Config-level view; with ``manager`` also the live connection state and diffs."""
    servers = config.servers()
    if not servers:
        return "No MCP servers configured. Add one with `kravein mcp add`."
    live = {s["name"]: s for s in manager.status()} if manager is not None else {}
    lines = []
    for name, spec in sorted(servers.items()):
        where = spec.get("url") or " ".join(spec.get("command") or [])
        state = "enabled" if spec.get("enabled") else "disabled"
        lines.append(f"{name}  [{spec.get('transport')}, {state}{', sandboxed' if spec.get('sandbox') else ''}]  {where}")
        tools = spec.get("tools") or {}
        for tool, entry in sorted(tools.items()):
            lines.append(f"    {tool}: {entry.get('class')}{', outward' if entry.get('outward') else ''}")
        if not tools:
            lines.append("    (no tools approved: run `kravein mcp approve " + name + "`)")
        info = live.get(name)
        if info:
            lines.append(f"    connection: {info['status']}{(' (' + info['error'] + ')') if info['error'] else ''}")
            report = info.get("report") or {}
            for kind in ("new", "changed", "removed"):
                if report.get(kind):
                    lines.append(f"    {kind}: {', '.join(report[kind])}")
            conn = manager.connection(name) if manager is not None else None
            for tool in report.get("changed", []):
                old = (tools.get(tool) or {}).get("definition")
                if conn is not None and tool in conn.listed:
                    lines += ["      " + ln for ln in pins.diff_text(old, pins.definition(conn.listed[tool])).splitlines()]
    return "\n".join(lines)


def parse_env(items: list[str]) -> dict[str, str]:
    env = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"--env needs NAME=inherit|key:<api_keys name>|value, got '{item}'")
        env[name] = value
    return env


def doctor_lines() -> list[str]:
    """Config-level status for ``kravein doctor``; never spawns a server."""
    servers = config.servers()
    if not servers:
        return ["no MCP servers configured"]
    out = []
    for name, spec in sorted(servers.items()):
        tools = spec.get("tools") or {}
        state = "enabled" if spec.get("enabled") else "disabled"
        missing = [v[4:] for v in (spec.get("env") or {}).values() if str(v).startswith("key:")]
        if spec.get("auth_key"):
            missing.append(spec["auth_key"])
        from .. import config as kconfig

        missing = [k for k in missing if not kconfig.api_key(k)]
        extra = f", MISSING KEY: {', '.join(missing)}" if missing else ""
        out.append(f"{name}: {spec.get('transport')}, {state}, {len(tools)} tool(s) approved{extra}"
                   + ("" if tools else " - NOT APPROVED yet"))
    return out


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, default=str)
