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
import json
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
    # alias -> kwargs the alias name implies (e.g. pause_job -> action=pause).
    alias_presets: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)
    # GUARDED grants and taint (#237). `scope` = the arguments a grant is
    # bound to (empty = all of them, the narrowest); `outward` = acts outside
    # this machine (a grant is suspended in a tainted run); `untrusted_output`
    # = its result carries outside content (taints the run); `origin` = the
    # MCP server / plugin it belongs to ("" = core), used for the
    # same-source exception to the taint rule.
    scope: tuple[str, ...] = ()
    outward: bool = False
    untrusted_output: bool = False
    origin: str = ""

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
_alias_presets: dict[str, dict[str, Any]] = {}
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

    # A TypedDict becomes a real object schema (properties, required, enums).
    # A bare `dict` has none, and providers such as Gemini reject an array of
    # property-less objects, so a list of records must be declared as one.
    if typing.is_typeddict(annotation):
        hints = typing.get_type_hints(annotation)
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {k: _json_schema_for(v) for k, v in hints.items()},
        }
        required = sorted(getattr(annotation, "__required_keys__", ()))
        if required:
            schema["required"] = required
        return schema

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

# Categories whose results carry outside content by default (#237): the run
# that reads them is tainted. A skill can say otherwise with untrusted_output=.
UNTRUSTED_CATEGORIES = frozenset({"web", "browser"})


def _scope_values(entry: Skill, cleaned: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """The grant scope of THIS call, from the arguments actually passed plus the
    signature's defaults, so an omitted argument cannot match a grant made
    with a different explicit value."""
    sig = inspect.signature(entry.func)
    values: dict[str, Any] = {}
    for pname, param in sig.parameters.items():
        if pname in _INJECTED_PARAMS or param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        if pname in cleaned:
            values[pname] = cleaned[pname]
        elif param.default is not inspect.Parameter.empty:
            values[pname] = param.default
    # A `**kwargs` bridge (an MCP/plugin wrapper) has its real arguments only
    # in the call: they all count, or one grant would cover every argument.
    for pname, value in cleaned.items():
        if pname not in _INJECTED_PARAMS:
            values.setdefault(pname, value)
    names = entry.scope or tuple(sorted(values))
    return tuple(sorted(
        (n, json.dumps(values.get(n), sort_keys=True, default=str)) for n in names
    ))


def skill(
    _func: Callable | None = None,
    *,
    name: str = "",
    description: str = "",
    danger: Danger = Danger.SAFE,
    category: str = "general",
    requires: tuple[str, ...] | list[str] = (),
    aliases: "tuple[str, ...] | list[str] | typing.Mapping[str, dict[str, Any]]" = (),
    scope: "tuple[str, ...] | list[str]" = (),
    outward: bool = False,
    untrusted_output: bool | None = None,
    origin: str = "",
) -> Callable:
    """Register a function as a skill.

    Args:
        name: Override the tool name (defaults to the function name).
        description: Override the description (defaults to the docstring).
        danger: Baseline risk level; skills may escalate at runtime.
        category: Grouping used by ``kravein skills`` and the help output.
        requires: External binaries that must exist for the skill to work.
        aliases: Extra names the CLI accepts for this skill. A plain list is
            just names. A mapping ``{alias: {kwarg: value}}`` also fixes
            arguments: ``{"pause_job": {"action": "pause"}}``. Preset keys
            always win over the caller's arguments, because the alias name
            decides the action and a caller must not turn pause into cancel.
            Aliases are never shown to the model.
        scope: For a GUARDED skill, the arguments an "Always allow" grant is
            bound to (``("channel", "campaign")``: not the message text). None
            declared = every argument, the narrowest grant. A name that is
            not a parameter raises: a typo must never widen a grant.
        outward: Acts outside this machine (sends, posts, pays). Its grants
            are suspended while the run is tainted.
        untrusted_output: Its result carries outside content (a web page, an
            email body) and taints the run. ``None`` = the category default
            (``web``, ``browser``: untrusted).
        origin: The MCP server or plugin it belongs to (``"mcp:leads"``);
            ``""`` for core skills.
    """

    def decorator(func: Callable) -> Callable:
        schema = build_schema(func, description)
        skill_name = name or func.__name__
        schema["name"] = skill_name

        scope_names = tuple(scope)
        unknown_scope = set(scope_names) - set(schema["parameters"]["properties"])
        if unknown_scope:
            raise ValueError(
                f"{skill_name} declares scope argument(s) it does not take: "
                f"{', '.join(sorted(unknown_scope))}"
            )

        presets: dict[str, dict[str, Any]] = {}
        if isinstance(aliases, typing.Mapping):
            alias_names = tuple(aliases)
            for alias, preset in aliases.items():
                preset = dict(preset or {})
                unknown = set(preset) - set(schema["parameters"]["properties"])
                if unknown:
                    raise ValueError(
                        f"alias '{alias}' of {skill_name} presets unknown "
                        f"parameter(s): {', '.join(sorted(unknown))}"
                    )
                if preset:
                    presets[alias] = preset
        else:
            alias_names = tuple(aliases)

        entry = Skill(
            name=skill_name,
            func=func,
            description=schema["description"],
            danger=danger,
            parameters=schema["parameters"],
            category=category,
            requires=tuple(requires),
            aliases=alias_names,
            alias_presets=presets,
            scope=scope_names,
            outward=bool(outward),
            untrusted_output=(
                category in UNTRUSTED_CATEGORIES if untrusted_output is None
                else bool(untrusted_output)
            ),
            origin=str(origin or ""),
        )

        with _lock:
            if skill_name in _registry:
                raise ValueError(f"duplicate skill name: {skill_name}")
            _registry[skill_name] = entry
            for alias in entry.aliases:
                _aliases[alias] = skill_name
                # Always reset: a plain alias registered over an old preset
                # must not inherit the previous skill's forced arguments.
                if alias in presets:
                    _alias_presets[alias] = presets[alias]
                else:
                    _alias_presets.pop(alias, None)

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
    """Skill groups the model and `more_tools` can name. The "agents" group
    (``delegate``) is not one of them: it is reached by profile, by name."""
    out: dict[str, list[Skill]] = {}
    for entry in all_skills().values():
        if entry.category == "agents":
            continue
        out.setdefault(entry.category, []).append(entry)
    for group in out.values():
        group.sort(key=lambda s: s.name)
    return dict(sorted(out.items()))


def tool_declarations(
    include_unavailable: bool = False,
    categories: set[str] | frozenset[str] | None = None,
    routing: bool = False,
    names: set[str] | frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Build the function-declaration list handed to the LLM.

    ``categories`` narrows the list (``None`` means every skill). ``routing``
    says the caller is using tool routing, which is the only time the
    routing-only skills (``more_tools``) mean anything — everywhere else they
    stay out, so turning routing off reproduces the old list exactly.

    ``names`` is an agent profile's exact tool set (see ``resolve_tools``); it
    overrides ``categories`` and routing. Aliases are never listed: only real
    skills have an entry here.
    """
    from .toolselect import ROUTING_ONLY_SKILLS

    decls = []
    for entry in sorted(all_skills().values(), key=lambda s: s.name):
        if names is not None:
            if entry.name not in names:
                continue
        elif entry.category == "agents" or is_external(entry):
            # `delegate` is an Orchestrator-only tool: an agent with no
            # profile never sees it (and `delegate` refuses such a caller too).
            # An MCP tool (#243) is given only to a profile that lists it.
            continue
        elif entry.name in ROUTING_ONLY_SKILLS:
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


def resolve_tools(spec: "typing.Iterable[str]") -> frozenset[str]:
    """Turn a profile's ``tools`` (categories and/or skill names) into skill names.

    An entry that is neither raises ``ValueError``: a typo must not quietly
    give an agent a smaller tool set than its author wrote.
    """
    by_cat = categories()
    out: set[str] = set()
    for item in spec:
        if item in by_cat:
            out.update(s.name for s in by_cat[item])
            continue
        if item.startswith(EXTERNAL_CATEGORY_PREFIX) or item.startswith(EXTERNAL_NAME_PREFIX):
            # An MCP server or tool (#243) that is not connected (or not
            # approved) right now: valid to name, contributes nothing until
            # it is registered. The agent re-resolves its profile every turn.
            entry = get_skill(item)
            if entry is not None:
                out.add(entry.name)
            continue
        entry = get_skill(item)
        if entry is None:
            raise ValueError(f"unknown tool or category '{item}'")
        out.add(entry.name)
    return frozenset(out)


# -- external tools (MCP, #243) ------------------------------------------------

EXTERNAL_CATEGORY_PREFIX = "mcp:"
EXTERNAL_NAME_PREFIX = "mcp__"


def is_external(entry: Skill) -> bool:
    return entry.category.startswith(EXTERNAL_CATEGORY_PREFIX)


def register_external(
    *,
    name: str,
    func: Callable[..., Any],
    description: str,
    parameters: dict[str, Any],
    danger: Danger,
    category: str,
    outward: bool,
    origin: str,
    scope: "tuple[str, ...] | list[str]" = (),
) -> Skill:
    """Register a tool that has no Python signature to read (an MCP tool).

    Only ``kravein.mcp.bridge`` calls this, for a tool the owner approved and
    whose pin matches. Its output is always untrusted; an existing core skill
    is never replaced, and an external name must carry the external prefix.
    ``scope`` (the owner's choice at approval) must name schema properties.
    """
    if not name.startswith(EXTERNAL_NAME_PREFIX) or not category.startswith(EXTERNAL_CATEGORY_PREFIX):
        raise ValueError(f"external tool '{name}' must be named mcp__* in an mcp:* category")
    scope = tuple(scope)
    unknown = set(scope) - set((parameters or {}).get("properties", {}))
    if unknown:
        raise ValueError(f"{name}: scope names unknown argument(s) {', '.join(sorted(unknown))}")
    entry = Skill(
        name=name, func=func, description=description, danger=danger,
        parameters=parameters, category=category, requires=(), aliases=(),
        scope=scope, outward=bool(outward), untrusted_output=True, origin=origin,
    )
    with _lock:
        existing = _registry.get(name)
        if existing is not None and not is_external(existing):
            raise ValueError(f"'{name}' would replace a core skill")
        _registry[name] = entry
    return entry


def unregister_external(name: str) -> None:
    with _lock:
        existing = _registry.get(name)
        if existing is not None and is_external(existing):
            del _registry[name]


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
            # A list of records sent as one JSON string must not be split on
            # commas (that would shred the objects).
            if value.lstrip().startswith("["):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return parsed
                except ValueError:
                    pass
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
    allowed: "frozenset[str] | set[str] | None" = None,
) -> str:
    """Run a skill by name. Always returns a string for the model to read.

    ``allowed`` is the calling agent profile's tool set (``None`` = every
    skill, the behaviour of an agent with no profile). A call outside it is
    refused and audited before anything runs: the restriction lives here, not
    in the prompt, so a model that names a tool it was never shown gets
    nothing.
    """
    params = dict(params or {})
    entry = get_skill(name)

    if entry is None:
        known = ", ".join(sorted(all_skills())[:20])
        return f"Unknown skill '{name}'. Try one of: {known}…"

    # Checked on the resolved skill, so an alias cannot dodge it. An external
    # (MCP) tool needs a profile that names it: no allow-list = not allowed.
    if (allowed is not None and entry.name not in allowed) or (allowed is None and is_external(entry)):
        audit.record(
            "skill.profile_refused", skill=entry.name, called_as=name, source=source,
        )
        return f"Skill '{entry.name}' is not available to this agent."

    alias_key = (name or "").strip()
    preset = _alias_presets.get(alias_key) if alias_key not in _registry else None

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

    if preset:
        # The alias name decides these arguments; whatever the caller sent for
        # the same keys is overwritten, never merged the other way round.
        overridden = sorted(k for k in preset if k in params and params[k] != preset[k])
        params.update(preset)
        audit.record(
            "skill.alias", alias=alias_key, skill=entry.name, source=source,
            preset=preset, overridden=overridden,
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
    # What enforce() binds a grant to (#237): computed here, from the call that
    # is really running, so no caller can name a scope or a signature.
    call = safety.CallContext(
        tool=entry.name, scope=_scope_values(entry, cleaned),
        outward=entry.outward, origin=entry.origin,
    )

    with audit.action(entry.name, params, source=source) as ctx:
        try:
            with safety.call_context(call):
                result = entry.func(**cleaned)
            if entry.untrusted_output:
                # The result enters the model's context: the run is tainted.
                safety.mark_tainted(entry.origin or f"tool:{entry.name}")
        except (SafetyError, ConfirmationRequired) as exc:
            ctx["result"] = str(exc)
            return str(exc)
        except TypeError as exc:
            ctx["result"] = f"bad arguments: {exc}"
            return f"Skill '{entry.name}' received bad arguments: {exc}"
        except Exception as exc:
            ctx["result"] = f"{type(exc).__name__}: {exc}"
            return f"Skill '{entry.name}' failed: {type(exc).__name__}: {exc}"

        # Tripwire, not enforcement: a skill declared GUARDED or worse that
        # returned without calling safety.enforce() at all (directly or through
        # a skill it delegates to) is a skill someone forgot to guard. Only the
        # skill knows what its arguments mean, so the registry cannot enforce
        # for it — but it can make the omission visible in the audit log.
        if (
            entry.danger >= Danger.GUARDED
            and safety.enforce_calls() == enforced_before
        ):
            audit.record(
                "skill.unenforced", skill=entry.name, source=source,
                danger=entry.danger.name,
            )

        text = result if isinstance(result, str) else str(result)
        ctx["result"] = text
        return text
