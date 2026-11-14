"""Plugin folders: ``plugin.json`` and the file hashes (#245)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")        # no "_": keeps <plugin>-<server> MCP names valid
TOOL_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
DANGERS = ("SAFE", "MODERATE", "GUARDED", "DESTRUCTIVE", "CRITICAL")
MAX_FILES = 200
MAX_FILE_BYTES = 2 * 1024 * 1024


class ManifestError(ValueError):
    """plugin.json is missing or wrong. The message says what to fix."""


def plugins_dir() -> Path:
    return Path(config.DATA_DIR) / "plugins"


@dataclass
class Manifest:
    name: str
    version: str
    description: str
    author: str
    secrets: list[str] = field(default_factory=list)
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)


def read_manifest(folder: Path) -> Manifest:
    try:
        data = json.loads((folder / "plugin.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ManifestError("plugin.json is missing") from None
    except (OSError, ValueError) as exc:
        raise ManifestError(f"plugin.json is not valid JSON ({exc})") from None
    if not isinstance(data, dict):
        raise ManifestError("plugin.json must be an object")
    name = str(data.get("name") or "")
    if name != folder.name:
        raise ManifestError(f"plugin.json name '{name}' must equal the folder name '{folder.name}'")
    if not NAME_RE.match(name):
        raise ManifestError("name: lowercase letters, digits and '-', up to 32, starting with a letter")
    tools = data.get("tools") or {}
    if not isinstance(tools, dict):
        raise ManifestError("tools must be an object {tool_name: {danger, outward, ...}}")
    clean: dict[str, dict[str, Any]] = {}
    for tool, spec in tools.items():
        spec = {"danger": spec} if isinstance(spec, str) else spec
        if not TOOL_RE.match(str(tool)) or not isinstance(spec, dict):
            raise ManifestError(f"tools.{tool}: bad tool name or entry")
        danger = str(spec.get("danger") or "").upper()
        if danger not in DANGERS:
            raise ManifestError(f"tools.{tool}.danger must be one of {', '.join(DANGERS)}")
        scope = spec.get("scope") or []
        if not isinstance(scope, list) or not all(isinstance(s, str) for s in scope):
            raise ManifestError(f"tools.{tool}.scope must be a list of argument names")
        clean[str(tool)] = {
            "danger": danger, "outward": bool(spec.get("outward", False)),
            "untrusted_output": spec.get("untrusted_output"), "scope": scope,
        }
    secrets = data.get("secrets") or []
    if not isinstance(secrets, list) or not all(isinstance(s, str) for s in secrets):
        raise ManifestError("secrets must be a list of api_keys.json names")
    return Manifest(name=name, version=str(data.get("version") or "0"),
                    description=str(data.get("description") or "")[:500],
                    author=str(data.get("author") or "")[:200], secrets=list(secrets), tools=clean)


def walk(folder: Path) -> list[Path]:
    """Every regular file in the plugin, sorted. A symlink anywhere refuses
    the plugin: what the owner approved must be the bytes that are in it."""
    files: list[Path] = []
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if any(part == "__pycache__" for part in rel.parts):
            continue                                  # Kravein never imports from disk
        if path.is_symlink():
            raise ManifestError(f"{rel} is a symlink; plugins may not contain links")
        if path.is_file():
            files.append(path)
            if len(files) > MAX_FILES:
                raise ManifestError(f"more than {MAX_FILES} files")
    return files


def read_files(folder: Path) -> dict[str, bytes]:
    """``{relative path: bytes}``, read once: the loader executes these bytes."""
    out: dict[str, bytes] = {}
    for path in walk(folder):
        data = path.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise ManifestError(f"{path.relative_to(folder)} is larger than {MAX_FILE_BYTES} bytes")
        out[path.relative_to(folder).as_posix()] = data
    return out


def hashes(files: dict[str, bytes]) -> dict[str, str]:
    return {rel: hashlib.sha256(data).hexdigest() for rel, data in sorted(files.items())}


def total(file_hashes: dict[str, str]) -> str:
    blob = json.dumps(file_hashes, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def compare(pinned: dict[str, str], current: dict[str, str]) -> dict[str, list[str]]:
    return {
        "changed": sorted(k for k in pinned if k in current and pinned[k] != current[k]),
        "added": sorted(set(current) - set(pinned)),
        "missing": sorted(set(pinned) - set(current)),
    }
