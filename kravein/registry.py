"""Skill registry.

Replaces the original's 1,000-line ``TOOL_DECLARATIONS`` list plus the
``if name == ...`` dispatch chain in ``main.py``. A skill is a plain function
decorated with ``@skill``; the decorator derives the LLM tool schema from the
signature and type hints, and registers the callable for dispatch.

Adding a capability now means dropping a module into ``kravein/skills/`` — no
core file needs editing.
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import pkgutil
import threading
import types
import typing
from typing import Any, Callable, get_args, get_origin

from . import audit, safety
from .safety import ConfirmationRequired, Danger, SafetyError

_JSON_TYPES = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


@dataclasses.dataclass
class Skill:
    """One callable capability exposed to the LLM and the CLI."""

    name: str
    func: Callable[..., Any]
    description: str
    danger: Danger
    parameters: dict[str, Any]
    category: str
    requires: tuple[str, ...]
    aliases: tuple[str, ...]

    def available(self) -> tuple[bool, str]:
        """Check the skill's declared external requirements are present."""
        from .platform import shell

        missing = [b for b in self.requires if not shell.have(b)]
        if missing:
            return False, "missing command(s): " + ", ".join(missing)
        return True, ""

    def __call__(self, **kwargs: Any) -> Any:
        return self.func(**kwargs)


_registry: dict[str, Skill] = {}
_aliases: dict[str, str] = {}
_lock = threading.RLock()
_loaded = False


# ---------------------------------------------------------------------------
# Schema derivation
# ---------------------------------------------------------------------------

def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """Turn ``X | None`` into ``(X, True)``."""
    origin = get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
        return args[0] if args else str, True
    return annotation, False


def _json_schema_for(annotation: Any) -> dict[str, Any]:
    annotation, _ = _unwrap_optional(annotation)
    origin = get_origin(annotation)

    if origin in (list, typing.List):
        args = get_args(annotation)
        item = _json_schema_for(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item}

    if origin in (dict, typing.Dict):
        return {"type": "object"}

    if annotation in _JSON_TYPES:
        return {"type": _JSON_TYPES[annotation]}

    if isinstance(annotation, type) and issubclass(annotation, str):
        return {"type": "string"}

    # typing.Literal["a", "b"] becomes an enum.
    if origin is typing.Literal:
        values = [str(v) for v in get_args(annotation)]
        return {"type": "string", "enum": values}

    return {"type": "string"}


def _parse_param_docs(doc: str) -> dict[str, str]:
    """Pull ``name: description`` lines out of an Args:/Parameters: block."""
    if not doc:
        return {}
    out: dict[str, str] = {}
    in_block = False
    # A wrapped description is indented deeper than the parameter name it
    # belongs to. Without tracking that, every continuation line was dropped
    # and the model saw the argument doc cut off mid-sentence — "Omit for the"
    # instead of "Omit for the focused window." Indentation is the only signal
    # available, so the first parameter line sets the base.
    base_indent: int | None = None
    last = ""
    for raw in doc.splitlines():
        line = raw.strip()
        low = line.lower().rstrip(":")
        if low in ("args", "arguments", "parameters", "params"):
            in_block = True
            continue
        if in_block:
            if not line:
                continue
            if low in ("returns", "return", "raises", "example", "examples", "notes"):
                break
            indent = len(raw) - len(raw.lstrip())
            if last and base_indent is not None and indent > base_indent:
                out[last] = f"{out[last]} {line}".strip()
                continue
            if ":" in line:
                name, _, desc = line.partition(":")
                name = name.strip().lstrip("*").split(" ")[0]
                if name:
                    out[name] = desc.strip()
                    last = name
                    if base_indent is None:
                        base_indent = indent
    return out


def _short_description(func: Callable, override: str) -> str:
    if override:
        return override.strip()
    doc = inspect.getdoc(func) or ""
    lines: list[str] = []
    for line in doc.splitlines():
        low = line.strip().lower().rstrip(":")
        if low in ("args", "arguments", "parameters", "params", "returns"):
            break
        lines.append(line)
    return " ".join(" ".join(lines).split()).strip() or func.__name__


def build_schema(func: Callable, description: str = "") -> dict[str, Any]:
    """Derive an LLM tool schema from a function signature."""
    sig = inspect.signature(func)
    try:
        hints = typing.get_type_hints(func)
    except Exception:
        hints = {}

    param_docs = _parse_param_docs(inspect.getdoc(func) or "")

    properties: dict[str, Any] = {}
    required: list[str] = []

    for pname, param in sig.parameters.items():
        if pname in ("self", "cls") or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        # Injected by the dispatcher, never exposed to the model.
        if pname in _INJECTED_PARAMS:
            continue

        schema = _json_schema_for(hints.get(pname, param.annotation))
        doc = param_docs.get(pname, "")
        if doc:
            schema["description"] = doc
        if param.default is not inspect.Parameter.empty and param.default is not None:
            schema.setdefault("description", "")
            default_note = f"Default: {param.default!r}."
            schema["description"] = (schema["description"] + " " + default_note).strip()

        properties[pname] = schema
        if param.default is inspect.Parameter.empty:
            required.append(pname)

    return {
        "name": func.__name__,
        "description": _short_description(func, description),
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


# Parameters the dispatcher fills in; hidden from the model's schema.
_INJECTED_PARAMS = {"_source", "_confirm", "_context"}


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

def skill(
    _func: Callable | None = None,
    *,
    name: str = "",
    description: str = "",
    danger: Danger = Danger.SAFE,
    category: str = "general",
    requires: tuple[str, ...] | list[str] = (),
    aliases: tuple[str, ...] | list[str] = (),
) -> Callable:
    """Register a function as a skill.

    Args:
        name: Override the tool name (defaults to the function name).
        description: Override the description (defaults to the docstring).
        danger: Baseline risk level; skills may escalate at runtime.
        category: Grouping used by ``kravein skills`` and the help output.
        requires: External binaries that must exist for the skill to work.
        aliases: Extra names the CLI accepts for this skill.
    """

    def decorator(func: Callable) -> Callable:
        schema = build_schema(func, description)
        skill_name = name or func.__name__
        schema["name"] = skill_name

        entry = Skill(
            name=skill_name,
            func=func,
            description=schema["description"],
            danger=danger,
            parameters=schema["parameters"],
            category=category,
            requires=tuple(requires),
            aliases=tuple(aliases),
        )

        with _lock:
            if skill_name in _registry:
                raise ValueError(f"duplicate skill name: {skill_name}")
            _registry[skill_name] = entry
            for alias in entry.aliases:
                _aliases[alias] = skill_name

        func.__skill__ = entry  # type: ignore[attr-defined]
        return func

    if _func is not None:
        return decorator(_func)
    return decorator


# ---------------------------------------------------------------------------
# Loading and lookup
# ---------------------------------------------------------------------------

def load_skills(force: bool = False) -> None:
    """Import every module under ``kravein.skills`` so decorators run."""
    global _loaded
    with _lock:
        if _loaded and not force:
            return
        from . import skills as skills_pkg

        for mod in pkgutil.iter_modules(skills_pkg.__path__):
            if mod.name.startswith("_"):
                continue
            full = f"{skills_pkg.__name__}.{mod.name}"
            try:
                importlib.import_module(full)
            except Exception as exc:
                print(f"[registry] skill module '{mod.name}' failed to load: {exc}")
                audit.record("registry.load_error", module=full, error=str(exc))
        _loaded = True


def all_skills() -> dict[str, Skill]:
    load_skills()
    return dict(_registry)


def get_skill(name: str) -> Skill | None:
    load_skills()
    key = (name or "").strip()
    if key in _registry:
        return _registry[key]
    resolved = _aliases.get(key)
    return _registry.get(resolved) if resolved else None


def categories() -> dict[str, list[Skill]]:
    out: dict[str, list[Skill]] = {}
    for entry in all_skills().values():
        out.setdefault(entry.category, []).append(entry)
    for group in out.values():
        group.sort(key=lambda s: s.name)
    return dict(sorted(out.items()))


def tool_declarations(
    include_unavailable: bool = False,
    categories: set[str] | frozenset[str] | None = None,
    routing: bool = False,
) -> list[dict[str, Any]]:
    """Build the function-declaration list handed to the LLM.

    ``categories`` narrows the list (``None`` means every skill). ``routing``
    says the caller is using tool routing, which is the only time the
    routing-only skills (``more_tools``) mean anything — everywhere else they
    stay out, so turning routing off reproduces the old list exactly.
    """
    from .toolselect import ROUTING_ONLY_SKILLS

    decls = []
    for entry in sorted(all_skills().values(), key=lambda s: s.name):
        if entry.name in ROUTING_ONLY_SKILLS:
            if not routing:
                continue
        elif categories is not None and entry.category not in categories:
            continue
        ok, _ = entry.available()
        if not ok and not include_unavailable:
            continue
        decls.append({
            "name": entry.name,
            "description": entry.description,
            "parameters": entry.parameters,
        })
    return decls


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _coerce(value: Any, schema: dict[str, Any]) -> Any:
    """Best-effort coercion; models often send numbers and bools as strings."""
    kind = schema.get("type")
    try:
        if kind == "integer" and not isinstance(value, bool):
            return int(float(value)) if isinstance(value, str) else int(value)
        if kind == "number":
            return float(value)
        if kind == "boolean":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "y", "on")
            return bool(value)
        if kind == "array" and isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        if kind == "string" and not isinstance(value, str):
            return str(value)
    except (TypeError, ValueError):
        return value
    return value


def dispatch(
    name: str,
    params: dict[str, Any] | None = None,
    *,
    source: str = "local",
    confirm: bool = False,
    context: Any = None,
) -> str:
    """Run a skill by name. Always returns a string for the model to read."""
    params = dict(params or {})
    entry = get_skill(name)

    if entry is None:
        known = ", ".join(sorted(all_skills())[:20])
        return f"Unknown skill '{name}'. Try one of: {known}…"

    ok, why = entry.available()
    if not ok:
        return f"Skill '{entry.name}' is unavailable on this system — {why}."

    # A confirmation flag inside `params` came from the *model*, and the model
    # does not get to approve its own destructive actions.
    #
    # This used to read `confirm = confirm or <model's flag>`. Caught on
    # 2026-09-06 by a scheduled job: refused on `delete_path`, the model
    # retried the same delete as `run_command "rm -f …"` with confirm=true and
    # the file was gone — from an unattended session with no confirmer at all.
    # The system prompt says never to do that; as the rest of this codebase
    # already learned about the verb list, a sentence in a prompt is not
    # enforcement.
    #
    # `confirm=` as a *keyword argument* still works, because that comes from a
    # trusted caller — the daemon relaying an explicit client request, or the
    # CLI's `--confirm`. Only the model's own arguments are ignored.
    for flag in ("confirm", "confirmed", "_confirm"):
        if flag in params:
            raw = params.pop(flag)
            if _coerce(raw, {"type": "boolean"}) and not confirm:
                audit.record(
                    "skill.self_confirm_refused",
                    skill=entry.name, source=source, flag=flag,
                )

    props = entry.parameters.get("properties", {})
    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        if key in props:
            cleaned[key] = _coerce(value, props[key])
        elif key in _INJECTED_PARAMS:
            continue
        else:
            # Keep unknown keys out of the call rather than raising TypeError.
            audit.record("skill.unknown_param", skill=entry.name, param=key)

    missing = [r for r in entry.parameters.get("required", []) if r not in cleaned]
    if missing:
        return f"Skill '{entry.name}' needs: {', '.join(missing)}."

    sig = inspect.signature(entry.func)
    if "_source" in sig.parameters:
        cleaned["_source"] = source
    if "_confirm" in sig.parameters:
        cleaned["_confirm"] = confirm
    if "_context" in sig.parameters:
        cleaned["_context"] = context

    enforced_before = safety.enforce_calls()

    with audit.action(entry.name, params, source=source) as ctx:
        try:
            result = entry.func(**cleaned)
        except (SafetyError, ConfirmationRequired) as exc:
            ctx["result"] = str(exc)
            return str(exc)
        except TypeError as exc:
            ctx["result"] = f"bad arguments: {exc}"
            return f"Skill '{entry.name}' received bad arguments: {exc}"
        except Exception as exc:
            ctx["result"] = f"{type(exc).__name__}: {exc}"
            return f"Skill '{entry.name}' failed: {type(exc).__name__}: {exc}"

        # Tripwire, not enforcement: a skill declared DESTRUCTIVE or worse that
        # returned without calling safety.enforce() at all (directly or through
        # a skill it delegates to) is a skill someone forgot to guard. Only the
        # skill knows what its arguments mean, so the registry cannot enforce
        # for it — but it can make the omission visible in the audit log.
        if (
            entry.danger >= Danger.DESTRUCTIVE
            and safety.enforce_calls() == enforced_before
        ):
            audit.record(
                "skill.unenforced", skill=entry.name, source=source,
                danger=entry.danger.name,
            )

        text = result if isinstance(result, str) else str(result)
        ctx["result"] = text
        return text
