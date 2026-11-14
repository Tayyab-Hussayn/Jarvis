"""Tool pinning (#243): what the owner approved is exactly what may run.

At approval each tool's name, description and input schema are hashed and
stored with the approved definition. On every connect the server's listing is
compared: a tool whose definition changed (a "rug pull"), a tool that is new,
or one that vanished is not registered until the owner approves again, and
``kravein mcp list`` shows the difference.

The hash covers the RAW definition the server sent, so any change, even in a
part Kravein would have trimmed, counts. What reaches the model is the
sanitised copy: descriptions are capped (a description is model-facing text
the server controls), and an oversized schema is refused.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
from typing import Any

MAX_DESCRIPTION = 1000          # the tool's description shown to the model
MAX_PARAM_DESCRIPTION = 300     # any "description" nested in the schema
MAX_SCHEMA_BYTES = 16384        # a schema this large is not registered at all


def definition(tool: Any) -> dict[str, Any]:
    """The pinned parts of an ``mcp_types.Tool`` (or an equivalent dict)."""
    if isinstance(tool, dict):
        name, desc, schema = tool.get("name"), tool.get("description"), tool.get("input_schema")
    else:
        name, desc, schema = tool.name, tool.description, tool.input_schema
    return {"name": str(name or ""), "description": str(desc or ""),
            "input_schema": schema if isinstance(schema, dict) else {}}


def pin(defn: dict[str, Any]) -> str:
    # `_hints` and other `_` keys are Kravein's display annotations, not the
    # server's definition.
    defn = {k: v for k, v in defn.items() if not k.startswith("_")}
    blob = json.dumps(defn, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compare(approved: dict[str, dict[str, Any]],
            listed: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """``{"ok", "new", "changed", "removed"}`` tool names."""
    out: dict[str, list[str]] = {"ok": [], "new": [], "changed": [], "removed": []}
    for name, defn in sorted(listed.items()):
        entry = approved.get(name)
        if entry is None:
            out["new"].append(name)
        elif entry.get("pin") != pin(defn):
            out["changed"].append(name)
        else:
            out["ok"].append(name)
    out["removed"] = sorted(set(approved) - set(listed))
    return out


def _cap_descriptions(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: (v[:MAX_PARAM_DESCRIPTION] if k == "description" and isinstance(v, str)
                    else _cap_descriptions(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [_cap_descriptions(v) for v in node]
    return node


def sanitized(defn: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """``(description, parameters)`` safe to show the model, or ``None`` when
    the schema is too large or not an object schema."""
    schema = defn.get("input_schema") or {}
    try:
        size = len(json.dumps(schema, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return None
    if size > MAX_SCHEMA_BYTES or schema.get("type", "object") != "object":
        return None
    params = _cap_descriptions(copy.deepcopy(schema))
    params.setdefault("type", "object")
    params.setdefault("properties", {})
    return defn.get("description", "")[:MAX_DESCRIPTION], params


def diff_text(old: dict[str, Any] | None, new: dict[str, Any] | None, limit: int = 2000) -> str:
    """A short unified diff between an approved and a listed definition."""
    def lines(d: dict[str, Any] | None) -> list[str]:
        return json.dumps(d or {}, indent=1, sort_keys=True, ensure_ascii=False,
                          default=str).splitlines()

    text = "\n".join(difflib.unified_diff(lines(old), lines(new), "approved", "server now",
                                          lineterm="", n=1))
    return text if len(text) <= limit else text[:limit] + "\n… (diff cut)"
