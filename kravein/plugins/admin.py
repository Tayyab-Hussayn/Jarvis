"""Owner-side plugin actions behind ``kravein plugin`` (#245). CLI only.

``enable`` shows the owner everything the plugin brings (file list with a hash
per file, declared tools with their class, profiles with their tool lists, MCP
servers, procedures, required secrets), says plainly that a code plugin runs
with Kravein's privileges, asks once, and pins the hashes. Its MCP servers are
added as pending: each still needs ``kravein mcp approve``.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .. import config
from . import loader, manifest, state


def folders() -> list[str]:
    base = manifest.plugins_dir()
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))


def _profile_tools(text: str) -> str:
    try:
        from ..agents.profile import parse_profile

        return ", ".join(parse_profile(text).tools) or "(none)"
    except Exception as exc:
        return f"(unreadable: {exc})"


def describe(name: str, files: dict[str, bytes], man: manifest.Manifest) -> list[str]:
    file_hashes = manifest.hashes(files)
    lines = [f"Plugin '{man.name}' {man.version} by {man.author or 'unknown'}",
             f"  {man.description}" if man.description else "",
             f"  hash: {manifest.total(file_hashes)}", "  files:"]
    lines += [f"    {digest[:16]}  {rel}" for rel, digest in file_hashes.items()]
    code = [f for f in files if f.startswith("tools/") and f.endswith(".py")]
    if code:
        lines.append("  CODE PLUGIN: these files run inside Kravein with all of its privileges.")
    if man.tools:
        lines.append("  tools (class is the floor; the tool can only ask more):")
        for tool, spec in sorted(man.tools.items()):
            extra = ", outward" if spec["outward"] else ""
            scope = f", Always-allow scope: {', '.join(spec['scope'])}" if spec["scope"] else ""
            lines.append(f"    {tool}: {spec['danger']}{extra}{scope}")
    for rel in sorted(files):
        parts = rel.split("/")
        if len(parts) == 3 and parts[0] == "agents" and parts[2] == "SKILL.md":
            text = files[rel].decode("utf-8", "replace")
            lines.append(f"  worker profile '{parts[1]}' with tools: {_profile_tools(text)}")
        if len(parts) == 3 and parts[0] == "skills" and parts[2] == "SKILL.md":
            lines.append(f"  procedure '{parts[1]}' (listed only; not loaded yet)")
    servers = _mcp_servers(files)
    for server, spec in sorted(servers.items()):
        where = spec.get("url") or " ".join(spec.get("command") or [])
        lines.append(f"  MCP server '{name}-{server}': {where} (added as pending; approve it separately)")
    if man.secrets:
        missing = [k for k in man.secrets if not config.api_key(k)]
        lines.append(f"  needs secrets: {', '.join(man.secrets)}"
                     + (f"  (MISSING: {', '.join(missing)})" if missing else ""))
    return [ln for ln in lines if ln]


def _mcp_servers(files: dict[str, bytes]) -> dict[str, dict[str, Any]]:
    raw = files.get("mcp.json")
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise manifest.ManifestError(f"mcp.json is not valid JSON ({exc})") from None
    servers = data.get("servers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise manifest.ManifestError('mcp.json must be {"servers": {name: {...}}}')
    return {k: v for k, v in servers.items() if isinstance(v, dict)}


def enable(name: str, *, yes: bool = False, ask: Callable[[str], str] = input,
           out: Callable[[str], None] = print) -> bool:
    """Show the plugin, ask once, pin its hashes, add its MCP servers as pending."""
    folder = manifest.plugins_dir() / name
    if not folder.is_dir():
        raise ValueError(f"no plugin folder {folder}")
    files = manifest.read_files(folder)
    man = manifest.read_manifest(folder)
    servers = _mcp_servers(files)
    for line in describe(name, files, man):
        out(line)
    if not yes and ask("Enable exactly this? [y/N] ").strip().lower() not in ("y", "yes"):
        out("Not enabled.")
        return False
    # Pin what was SHOWN: re-read and refuse if anything moved meanwhile.
    again = manifest.read_files(folder)
    if manifest.hashes(again) != manifest.hashes(files):
        raise ValueError("the plugin's files changed while you were reviewing them; run enable again")
    added = _add_mcp(name, servers)
    file_hashes = manifest.hashes(files)
    state.pin(name, file_hashes=file_hashes, digest=manifest.total(file_hashes),
              version=man.version, mcp_added=added)
    out(f"Enabled '{name}'. Restart Kravein (or the server) for its tools and profiles to load.")
    if added:
        out("Its MCP servers are pending; approve each with: "
            + "; ".join(f"kravein mcp approve {s}" for s in added))
    return True


def _add_mcp(plugin: str, servers: dict[str, dict[str, Any]]) -> list[str]:
    """Add each server as a PENDING MCP server (no tool approved)."""
    if not servers:
        return []
    from ..mcp import config as mcp_config

    existing = mcp_config.servers()
    added = []
    for server, spec in sorted(servers.items()):
        full = f"{plugin}-{server}"
        if full in existing:
            added.append(full)
            continue
        mcp_config.add_server(
            full, command=spec.get("command") or None, url=spec.get("url") or "",
            env=spec.get("env") or {}, auth_key=spec.get("auth_key") or "",
            sandbox=bool(spec.get("sandbox")), timeout=float(spec.get("timeout") or 60))
        added.append(full)
    return added


def disable(name: str) -> None:
    state.set_enabled(name, False)


def remove(name: str) -> None:
    """Forget the pins. The owner's folder is left alone."""
    state.forget(name)


def list_text() -> str:
    from .. import registry

    registry.load_skills()
    pins = state.load()
    live = loader.status()
    names = sorted(set(folders()) | set(pins))
    if not names:
        return f"No plugins. Put one in {manifest.plugins_dir()}/<name>/ and run `kravein plugin enable <name>`."
    lines = []
    for name in names:
        info = live.get(name) or {}
        if name not in pins:
            lines.append(f"{name}: not enabled (run `kravein plugin enable {name}`)")
            continue
        st = info.get("state") or ("enabled" if pins[name].get("enabled") else "disabled")
        lines.append(f"{name}: {st}" + (f" - {info['error']}" if info.get("error") else ""))
        for kind in ("changed", "added", "missing"):
            if (info.get("diff") or {}).get(kind):
                lines.append(f"    {kind}: {', '.join(info['diff'][kind])}")
        if info.get("tools"):
            lines.append(f"    tools: {', '.join(info['tools'])}")
        if info.get("profiles"):
            lines.append(f"    profiles: {', '.join(sorted(info['profiles']))}")
        if pins[name].get("mcp_added"):
            lines.append(f"    MCP servers: {', '.join(pins[name]['mcp_added'])}")
    return "\n".join(lines)


def doctor_lines() -> list[str]:
    """For ``kravein doctor``: pins and a fresh hash check. Never executes
    plugin code; a load error from this process is shown if there was one."""
    pins = state.load()
    live = loader.status()
    names = sorted(set(folders()) | set(pins))
    if not names:
        return ["no plugins"]
    out = []
    for name in names:
        if name not in pins:
            out.append(f"{name}: not enabled")
            continue
        if not pins[name].get("enabled"):
            out.append(f"{name}: disabled")
            continue
        folder = manifest.plugins_dir() / name
        try:
            files = manifest.read_files(folder)
            diff = manifest.compare(pins[name].get("files") or {}, manifest.hashes(files))
            man = manifest.read_manifest(folder)
        except (OSError, manifest.ManifestError) as exc:
            out.append(f"{name}: ERROR {exc}")
            continue
        if any(diff.values()):
            moved = ", ".join(sorted({*diff["changed"], *diff["added"], *diff["missing"]}))
            out.append(f"{name}: CHANGED ({moved}) - will not load until `kravein plugin enable {name}`")
            continue
        err = (live.get(name) or {}).get("error")
        line = f"{name}: enabled, {len(man.tools)} tool(s) declared" + (f", ERROR {err}" if err else "")
        missing = [k for k in man.secrets if not config.api_key(k)]
        if missing:
            line += f", MISSING SECRET {', '.join(missing)}"
        out.append(line)
    return out
