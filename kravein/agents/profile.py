"""Agent profiles: ``kravein/agents/<name>/SKILL.md``.

An agent is not a class, it is a folder. One generic :class:`~kravein.agent.Agent`
runs any profile, so adding an agent means adding a folder and nothing in the
core changes (the same principle as ``@skill``).

File shape::

    ---
    name: research
    purpose: One line on what this agent is for.
    tools: [web, files, read_file]      # categories and/or skill names
    model: zen:deepseek-v4.1-flash      # optional, "provider:model"
    report_max_chars: 1500              # optional
    ---
    Free-form instructions, appended to the shared base system prompt.

PyYAML is deliberately not used: it is only a transitive dependency here, and
the shape above is all a profile needs. The parser is strict instead — an
unknown key or a malformed line refuses the whole profile, because a profile
that half-loads (say, with its ``tools`` line silently dropped) would run with
the wrong tool set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PROFILES_DIR = Path(__file__).resolve().parent

_KEYS = {"name", "purpose", "tools", "model", "report_max_chars"}
_REQUIRED = ("name", "purpose", "tools")
_NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,39}$")


class ProfileError(ValueError):
    """A profile is missing, malformed, or inconsistent. Never half-loaded."""


@dataclass(frozen=True)
class AgentProfile:
    name: str
    purpose: str
    tools: tuple[str, ...]
    instructions: str = ""
    model: str = ""
    report_max_chars: int = 0  # 0 = use the caller's default


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def _strip_comment(raw: str) -> str:
    # A ` #` starts a comment unless inside quotes.
    quote = ""
    for i, ch in enumerate(raw):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or raw[i - 1] in " \t"):
            return raw[:i]
    return raw


def _parse_frontmatter(text: str, where: str) -> dict[str, object]:
    out: dict[str, object] = {}
    lines = text.splitlines()
    current_list: str | None = None
    for number, original in enumerate(lines, 1):
        line = _strip_comment(original).rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("- "):
            if current_list is None:
                raise ProfileError(f"{where}: line {number}: list item without a key")
            item = _unquote(line.lstrip()[2:])
            if not item:
                raise ProfileError(f"{where}: line {number}: empty list item")
            out[current_list].append(item)  # type: ignore[union-attr]
            continue
        if line[0] in " \t":
            raise ProfileError(f"{where}: line {number}: unexpected indentation")
        key, sep, value = line.partition(":")
        key = key.strip()
        if not sep or not key:
            raise ProfileError(f"{where}: line {number}: expected 'key: value'")
        if key not in _KEYS:
            raise ProfileError(f"{where}: line {number}: unknown key '{key}'")
        if key in out:
            raise ProfileError(f"{where}: line {number}: duplicate key '{key}'")
        value = value.strip()
        current_list = None
        if key == "tools":
            if not value:
                out[key] = []
                current_list = key
            elif value.startswith("[") and value.endswith("]"):
                inner = value[1:-1]
                out[key] = [_unquote(p) for p in inner.split(",") if p.strip()]
            else:
                raise ProfileError(
                    f"{where}: line {number}: 'tools' must be a list "
                    "([a, b] or '- item' lines)"
                )
        else:
            out[key] = _unquote(value)
    return out


def parse_profile(text: str, *, expected_name: str = "", where: str = "SKILL.md") -> AgentProfile:
    """Parse one SKILL.md. Raises :class:`ProfileError`; never returns partial."""
    text = text.lstrip("﻿")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ProfileError(f"{where}: must start with a '---' frontmatter line")
    for end in range(1, len(lines)):
        if lines[end].strip() == "---":
            break
    else:
        raise ProfileError(f"{where}: frontmatter is not closed with '---'")

    meta = _parse_frontmatter("\n".join(lines[1:end]), where)
    body = "\n".join(lines[end + 1:]).strip()

    for key in _REQUIRED:
        if not meta.get(key):
            raise ProfileError(f"{where}: missing required key '{key}'")
    name = str(meta["name"])
    if not _NAME_RE.match(name):
        raise ProfileError(f"{where}: invalid name '{name}' (lowercase letters, digits, - _)")
    if expected_name and name != expected_name:
        raise ProfileError(f"{where}: name '{name}' does not match its folder '{expected_name}'")

    tools = tuple(dict.fromkeys(str(t) for t in meta["tools"]))  # type: ignore[union-attr]
    if not tools:
        raise ProfileError(f"{where}: 'tools' is empty")

    limit = 0
    if "report_max_chars" in meta:
        try:
            limit = int(str(meta["report_max_chars"]))
        except ValueError:
            raise ProfileError(f"{where}: report_max_chars must be an integer") from None
        if limit <= 0:
            raise ProfileError(f"{where}: report_max_chars must be positive")

    model = str(meta.get("model", ""))
    if model and not re.match(r"^[a-z0-9_-]+:\S+$", model):
        raise ProfileError(
            f"{where}: model must look like 'provider:model' (got '{model}')"
        )

    return AgentProfile(
        name=name,
        purpose=str(meta["purpose"]),
        tools=tools,
        instructions=body,
        model=model,
        report_max_chars=limit,
    )


def load_profile(name: str, *, root: Path | None = None) -> AgentProfile:
    """Load ``<root>/<name>/SKILL.md`` (root defaults to ``kravein/agents``)."""
    if not _NAME_RE.match(name or ""):
        raise ProfileError(f"invalid profile name '{name}'")
    path = (root or PROFILES_DIR) / name / "SKILL.md"
    if root is None and not path.is_file():
        # A profile from an enabled plugin (#245): the text the loader hashed,
        # never a fresh read from the plugin folder. A core profile always
        # wins a name clash (it is checked first).
        found = _plugin_profiles().get(name)
        if found is not None:
            plugin, text = found
            return parse_profile(text, expected_name=name, where=f"plugin:{plugin}/agents/{name}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileError(f"profile '{name}' not found ({path}): {exc}") from None
    return parse_profile(text, expected_name=name, where=str(path))


def _plugin_profiles() -> dict[str, tuple[str, str]]:
    try:
        from .. import registry
        from ..plugins import loader

        registry.load_skills()                    # loads the enabled plugins
        return loader.profile_texts()
    except Exception:
        return {}


def list_profiles(*, root: Path | None = None) -> list[str]:
    """Names of folders that hold a SKILL.md; folders starting with ``_`` are
    fixtures and not listed. Without ``root``: plus enabled plugins' profiles
    whose names do not clash with a core one."""
    base = root or PROFILES_DIR
    core = {
        p.name for p in base.iterdir()
        if p.is_dir() and not p.name.startswith("_") and (p / "SKILL.md").is_file()
    }
    if root is None:
        core |= {n for n in _plugin_profiles() if _NAME_RE.match(n)}
    return sorted(core)
