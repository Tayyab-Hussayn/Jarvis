"""Load the owner's enabled plugins (#245).

Called from ``registry.load_skills``, so the CLI, the server and the jobs tick
all see the same tools. For every enabled plugin:

1. read every file ONCE and hash it; any difference from the pins (a changed,
   added or missing file) and the plugin does not load: status ``changed``;
2. execute each ``tools/*.py`` from those same bytes (never an import from
   disk after hashing, so a file swapped in between cannot run), inside
   ``registry.plugin_context``: category/origin ``plugin:<name>``, the
   manifest's danger floor, no shadowing, undeclared tools refused;
3. on any exception, unregister everything that plugin added and record why.
   Kravein keeps starting; the other plugins still load.
"""

from __future__ import annotations

import sys
import threading
import types
from typing import Any

from .. import audit, registry
from . import manifest, state

_status: dict[str, dict[str, Any]] = {}
_lock = threading.RLock()
_loaded = False


def status() -> dict[str, dict[str, Any]]:
    """``{name: {state, error, tools, profiles, ...}}`` from the last load."""
    with _lock:
        return {k: dict(v) for k, v in _status.items()}


def intact(name: str) -> dict[str, bytes] | None:
    """The files of an enabled plugin whose hashes all match its pins, else None."""
    pins = state.load().get(name)
    if not pins or not pins.get("enabled"):
        return None
    folder = manifest.plugins_dir() / name
    try:
        files = manifest.read_files(folder)
    except (OSError, manifest.ManifestError):
        return None
    if manifest.compare(pins.get("files") or {}, manifest.hashes(files)) != \
            {"changed": [], "added": [], "missing": []}:
        return None
    return files


def load_enabled(force: bool = False) -> None:
    global _loaded
    with _lock:
        if _loaded and not force:
            return
        _loaded = True
        for name in list(_status):
            registry.unregister_plugin(name)
        _status.clear()
        for name, pins in sorted(state.load().items()):
            if not pins.get("enabled"):
                _status[name] = {"state": "disabled"}
                continue
            _status[name] = _load_one(name, pins)


def _load_one(name: str, pins: dict[str, Any]) -> dict[str, Any]:
    folder = manifest.plugins_dir() / name
    if not folder.is_dir():
        return {"state": "missing", "error": f"{folder} does not exist"}
    try:
        files = manifest.read_files(folder)
        diff = manifest.compare(pins.get("files") or {}, manifest.hashes(files))
        if any(diff.values()):
            audit.record("plugin.changed", plugin=name, **diff)
            return {"state": "changed", "diff": diff,
                    "error": "files changed since you enabled it; run `kravein plugin enable "
                             f"{name}` to review and enable it again"}
        # Parsed from the hashed bytes, never re-read: the floors, outward
        # flags and declared tools are exactly what the owner pinned.
        man = manifest.parse_manifest(name, files.get("plugin.json"))
    except (OSError, manifest.ManifestError) as exc:
        return {"state": "error", "error": str(exc)}

    registered: list[str] = []
    try:
        with registry.plugin_context(name, man.tools) as registered:
            for rel in sorted(f for f in files if f.startswith("tools/") and f.endswith(".py")
                              and f.count("/") == 1):
                _exec(name, rel, files[rel], folder)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:                 # incl. sys.exit / argparse: only this plugin goes
        registry.unregister_plugin(name)
        prefix = f"kravein_plugins.{name.replace('-', '_')}."
        for mod in [m for m in sys.modules if m.startswith(prefix)]:
            sys.modules.pop(mod, None)
        reason = f"{type(exc).__name__}: {exc}"[:300]
        audit.record("plugin.load_failed", plugin=name, error=reason)
        return {"state": "error", "error": reason}
    # Worker profiles, kept as the hashed text (never re-read from disk later).
    profiles = {}
    for rel, data in files.items():
        parts = rel.split("/")
        if len(parts) == 3 and parts[0] == "agents" and parts[2] == "SKILL.md" \
                and not parts[1].startswith("_"):
            profiles[parts[1]] = data.decode("utf-8", "replace")
    audit.record("plugin.loaded", plugin=name, tools=registered, profiles=sorted(profiles))
    return {"state": "enabled", "tools": sorted(registered), "version": man.version,
            "profiles": profiles}


def _exec(name: str, rel: str, source: bytes, folder: Any) -> None:
    """Run one tool module from the bytes that were hashed."""
    mod_name = f"kravein_plugins.{name.replace('-', '_')}.{rel[len('tools/'):-3]}"
    module = types.ModuleType(mod_name)
    module.__file__ = str(folder / rel)
    code = compile(source, str(folder / rel), "exec")
    sys.modules[mod_name] = module
    try:
        exec(code, module.__dict__)               # noqa: S102 - the owner enabled this code
    except BaseException:
        sys.modules.pop(mod_name, None)
        raise


def profile_texts() -> dict[str, tuple[str, str]]:
    """``{profile name: (plugin, SKILL.md text)}`` of loaded plugins. The first
    plugin (by name) wins a clash between plugins; a core profile always wins
    (``agents.profile`` checks)."""
    out: dict[str, tuple[str, str]] = {}
    for name, info in sorted(status().items()):
        if info.get("state") != "enabled":
            continue
        for pname, text in sorted((info.get("profiles") or {}).items()):
            out.setdefault(pname, (name, text))
    return out
