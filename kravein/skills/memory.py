"""Long-term memory.

Ports ``memory/memory_manager.py``. Storage moves from the repo directory to
``$XDG_DATA_HOME``, and writes are atomic so a crash mid-save cannot corrupt
the file.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from .. import config
from ..registry import skill
from ..safety import Danger, Verdict, enforce

_lock = threading.RLock()

# The original's categories, preserved so existing memory files still load.
CATEGORIES = ("identity", "preferences", "projects", "facts", "routines")


def _empty() -> dict[str, dict[str, Any]]:
    return {category: {} for category in CATEGORIES}


def _load() -> dict[str, dict[str, Any]]:
    with _lock:
        path = config.MEMORY_FILE
        if not path.exists():
            # Fall back to the original in-repo location on first run.
            legacy = config.BASE_DIR / "memory" / "long_term.json"
            if legacy.exists():
                path = legacy
            else:
                return _empty()

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _empty()

        if not isinstance(data, dict):
            return _empty()

        merged = _empty()
        for category, entries in data.items():
            if isinstance(entries, dict):
                merged.setdefault(category, {}).update(entries)
        return merged


def _save(data: dict[str, dict[str, Any]]) -> None:
    with _lock:
        config.ensure_dirs()
        path = config.MEMORY_FILE
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


def context_for_prompt(max_items: int = 40) -> str:
    """Render memory as a block to prepend to the system prompt."""
    data = _load()
    lines: list[str] = []
    count = 0
    for category in CATEGORIES:
        entries = data.get(category) or {}
        if not entries:
            continue
        lines.append(f"{category}:")
        for key, entry in entries.items():
            value = entry.get("value") if isinstance(entry, dict) else entry
            lines.append(f"  - {key}: {value}")
            count += 1
            if count >= max_items:
                break
        if count >= max_items:
            break
    if not lines:
        return ""
    return "What you already know about this user:\n" + "\n".join(lines)


@skill(category="memory", danger=Danger.MODERATE)
def remember(key: str, value: str, category: str = "facts") -> str:
    """Store a fact about the user for future conversations.

    Args:
        key: A short label, e.g. 'preferred_editor'.
        value: The fact itself.
        category: One of identity, preferences, projects, facts, routines.
    """
    key = (key or "").strip()
    if not key:
        return "Give a key to store this under."

    category = (category or "facts").strip().lower()
    if category not in CATEGORIES:
        category = "facts"

    data = _load()
    data.setdefault(category, {})[key] = {
        "value": value,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _save(data)
    return f"Remembered [{category}] {key} = {value}"


@skill(category="memory", danger=Danger.SAFE)
def recall(query: str = "", category: str = "") -> str:
    """Look up what has been remembered.

    Args:
        query: Text to search keys and values for. Omit to list everything.
        category: Limit to one category.
    """
    data = _load()
    needle = (query or "").strip().lower()

    categories = [category] if category in CATEGORIES else list(CATEGORIES)
    lines: list[str] = []

    for cat in categories:
        entries = data.get(cat) or {}
        matched = []
        for key, entry in entries.items():
            value = entry.get("value") if isinstance(entry, dict) else entry
            if needle and needle not in key.lower() and needle not in str(value).lower():
                continue
            updated = entry.get("updated", "") if isinstance(entry, dict) else ""
            matched.append(f"  {key}: {value}" + (f"  ({updated[:10]})" if updated else ""))
        if matched:
            lines.append(f"{cat}:")
            lines.extend(matched)

    if not lines:
        return f"Nothing remembered matching '{query}'." if needle else "Memory is empty."
    return "\n".join(lines)


@skill(category="memory", danger=Danger.DESTRUCTIVE)
def forget(key: str, category: str = "", _source: str = "local", _confirm: bool = False) -> str:
    """Delete a remembered fact.

    Args:
        key: The key to remove, or '*' to clear a whole category.
        category: Which category to remove it from. Required for '*'.
    """
    data = _load()

    if key == "*":
        if category not in CATEGORIES:
            return "To clear a whole category, name it explicitly."
        count = len(data.get(category) or {})
        if not count:
            return f"Category '{category}' is already empty."
        enforce(
            Verdict(danger=Danger.DESTRUCTIVE,
                    reasons=[f"erases all {count} memories in '{category}'"]),
            f"clear the '{category}' memory category",
            skill="forget", source=_source, pre_confirmed=_confirm,
        )
        data[category] = {}
        _save(data)
        return f"Cleared {count} memory item(s) from '{category}'."

    targets = [category] if category in CATEGORIES else list(CATEGORIES)
    for cat in targets:
        if key in (data.get(cat) or {}):
            enforce(
                Verdict(danger=Danger.DESTRUCTIVE, reasons=[f"forgets '{key}'"]),
                f"forget '{key}' from {cat}",
                skill="forget", source=_source, pre_confirmed=_confirm,
            )
            del data[cat][key]
            _save(data)
            return f"Forgot [{cat}] {key}."

    return f"Nothing remembered under '{key}'."
