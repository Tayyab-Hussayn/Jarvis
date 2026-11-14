"""``~/.config/kravein/plugins.json``: which plugins the owner enabled (#245).

Written only by owner actions (``kravein plugin ...``); a test bans the
writers from skills, agents, platform and mcp code, the file sits in the
config tree the file tools refuse, and the shell rule in ``safety`` refuses
any non-read command naming it.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .. import audit, config


def path() -> Path:
    return Path(config.CONFIG_DIR) / "plugins.json"


def load() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    plugins = data.get("plugins") if isinstance(data, dict) else None
    return {k: v for k, v in (plugins or {}).items() if isinstance(v, dict)}


@contextmanager
def _locked() -> Iterator[dict[str, dict[str, Any]]]:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target.with_name("plugins.json.lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        plugins = load()
        yield plugins
        tmp = target.with_name("plugins.json.tmp")
        tmp.write_text(json.dumps({"plugins": plugins}, indent=2, sort_keys=True), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)


# -- owner actions (CLI only) -------------------------------------------------

def pin(name: str, *, file_hashes: dict[str, str], digest: str, version: str,
        mcp_added: list[str]) -> None:
    with _locked() as plugins:
        plugins[name] = {"enabled": True, "files": dict(file_hashes), "hash": digest,
                         "version": version, "enabled_at": time.time(),
                         "mcp_added": sorted(set(mcp_added))}
    audit.record("plugin.enabled", plugin=name, hash=digest, files=len(file_hashes), version=version)


def set_enabled(name: str, enabled: bool) -> None:
    with _locked() as plugins:
        if name not in plugins:
            raise ValueError(f"plugin '{name}' was never enabled")
        plugins[name]["enabled"] = bool(enabled)
    audit.record("plugin.disabled" if not enabled else "plugin.reenabled", plugin=name)


def forget(name: str) -> None:
    with _locked() as plugins:
        if plugins.pop(name, None) is None:
            raise ValueError(f"plugin '{name}' is not known")
    audit.record("plugin.removed", plugin=name)
