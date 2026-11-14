"""Per-call usage ledger: one row per provider attempt (v3 phase 0a, #228).

Every model call the facade makes lands here, failed failover attempts
included, so "what did that turn cost and which model really answered" has a
record that does not depend on the audit log's free-form events.

Token numbers are only ever the provider's own report. A call whose provider
reported nothing stores NULL tokens: an estimate presented as real would make
the cost view lie, which is worse than a gap.

Writing must never break a turn: every failure is audited and swallowed.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from . import audit, config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    principal TEXT NOT NULL DEFAULT '',
    thread TEXT NOT NULL DEFAULT '',
    agent TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    input INTEGER,
    cached INTEGER,
    output INTEGER,
    latency_ms INTEGER,
    ok INTEGER NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    cost REAL
);
CREATE INDEX IF NOT EXISTS calls_ts ON calls (ts);
"""

_lock = threading.Lock()


def db_path() -> Path:
    # Read at call time: tests monkeypatch config.STATE_DIR.
    return Path(config.STATE_DIR) / "usage.db"


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5)
    conn.executescript(_SCHEMA)
    return conn


def price_source(provider: str, model: str) -> tuple[dict[str, float] | None, str]:
    """``(price, "catalog" | "prices" | "")`` for ``provider:model``.

    One source of truth (#234): the catalog entry's ``price``. The older
    ``models.prices`` (#230) is still read when the catalog has none, so a
    price someone already set does not silently turn their costs to NULL.
    Read as whole dicts, never via a dotted ``config.get``: model ids hold
    dots (``gemini-3.5-flash-lite``) and would split into nonsense paths.
    """
    key = f"{provider}:{model}"
    entry = catalog().get(key) or {}
    found = _valid_price(entry.get("price"))
    if found is not None:
        return found, "catalog"
    prices = config.get("models.prices") or {}
    if isinstance(prices, dict):
        found = _valid_price(prices.get(key))
        if found is not None:
            return found, "prices"
    return None, ""


def price_of(provider: str, model: str) -> dict[str, float] | None:
    """USD per 1M tokens for ``provider:model``, or ``None`` (see price_source)."""
    return price_source(provider, model)[0]


def _valid_price(entry: Any) -> dict[str, float] | None:
    if not isinstance(entry, dict):
        return None
    try:
        out = {k: float(v) for k, v in entry.items() if k in ("input", "cached", "output")}
    except (TypeError, ValueError):
        return None
    if "input" not in out or "output" not in out:
        return None
    return out


def cost_of(provider: str, model: str, usage: Any) -> float | None:
    """USD for one call, or ``None`` when tokens or the price are unknown.

    ``usage.input`` includes the cached tokens, so the cached part is billed at
    the ``cached`` price (the input price when no cached price is configured)
    and only the rest at the input price.
    """
    if usage is None:
        return None
    price = price_of(provider, model)
    if price is None:
        return None
    cached = max(0, min(int(usage.cached or 0), int(usage.input or 0)))
    fresh = int(usage.input or 0) - cached
    cached_price = price.get("cached", price["input"])
    total = (
        fresh * price["input"] + cached * cached_price
        + int(usage.output or 0) * price["output"]
    )
    return round(total / 1_000_000, 8)


def record(
    *,
    role: str,
    provider: str,
    model: str,
    usage: Any = None,
    latency_ms: float = 0.0,
    ok: bool,
    error: str = "",
    principal: str = "",
    thread: str = "",
    agent: str = "",
) -> None:
    """Append one row. Never raises."""
    try:
        cost = cost_of(provider, model, usage) if ok else None
        row = (
            time.time(), principal or "", thread or "", agent or "", role or "",
            provider or "", model or "",
            None if usage is None else int(usage.input),
            None if usage is None else int(usage.cached),
            None if usage is None else int(usage.output),
            int(round(latency_ms)), 1 if ok else 0, error or "", cost,
        )
        with _lock:
            conn = _connect()
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO calls (ts, principal, thread, agent, role, provider,"
                        " model, input, cached, output, latency_ms, ok, error, cost)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        row,
                    )
            finally:
                conn.close()
    except Exception as exc:  # the ledger is bookkeeping, never a failure path
        try:
            audit.record("usage.write_failed", error=f"{type(exc).__name__}: {exc}"[:200])
        except Exception:
            pass


def summary(days: float = 7) -> list[dict[str, Any]]:
    """Totals per (role, provider, model) over the last ``days`` days.

    Token sums cover only calls that reported usage (``reported`` counts them);
    ``cost`` is ``None`` when no call in the group had a known cost.
    """
    if not db_path().exists():
        return []
    since = time.time() - float(days) * 86400
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT role, provider, model, COUNT(*), SUM(1 - ok),"
            " SUM(input), SUM(cached), SUM(output), COUNT(input), SUM(cost)"
            " FROM calls WHERE ts >= ? GROUP BY role, provider, model"
            " ORDER BY role, provider, model",
            (since,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "role": r[0], "provider": r[1], "model": r[2], "calls": r[3],
            "failures": r[4] or 0, "input": r[5] or 0, "cached": r[6] or 0,
            "output": r[7] or 0, "reported": r[8], "cost": r[9],
        }
        for r in rows
    ]


def report(days: float = 7) -> str:
    """``kravein usage`` text."""
    rows = summary(days)
    if not rows:
        return f"No model calls recorded in the last {days:g} day(s)."
    header = ("role", "provider:model", "calls", "fail", "input", "cached", "output", "cost")
    table = [header]
    for r in rows:
        unreported = r["calls"] - r["reported"]
        tokens = [str(r["input"]), str(r["cached"]), str(r["output"])]
        if unreported:
            tokens[2] += f" (+{unreported} unreported)"
        table.append((
            r["role"] or "default",
            f"{r['provider']}:{r['model']}" if r["model"] else r["provider"],
            str(r["calls"]), str(r["failures"]), *tokens,
            "-" if r["cost"] is None else f"${r['cost']:.4f}",
        ))
    widths = [max(len(row[i]) for row in table) for i in range(len(header))]
    lines = [f"Model usage, last {days:g} day(s):"]
    for row in table:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Model catalog (#234, v3 phase 0c)
# ---------------------------------------------------------------------------
#
# `models.catalog["provider:model"]` holds what is known about a model ON THIS
# ACCOUNT: price, context, tool cap, and whether tool calling was verified by a
# real probe. "Model ids are not the catalogue" (hard-won-llm-agent): an id a
# provider lists may not be callable here, so only a probe marks it verified.

TOOL_STATES = ("verified", "failed", "untested")
CATALOG_MAX_AGE_DAYS = 60
# Roles whose first choice runs an agent loop: only a verified model may lead.
_UNLIMITED_TOOLS = 10**6


def catalog() -> dict[str, dict[str, Any]]:
    """The whole catalog (a copy), keyed ``provider:model``."""
    raw = config.get("models.catalog") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


def tool_status(key: str) -> str:
    """``verified`` / ``failed`` / ``untested`` for ``provider:model``."""
    state = (catalog().get(key) or {}).get("tool_calling")
    return state if state in TOOL_STATES else "untested"


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _age_days(as_of: Any) -> float | None:
    try:
        then = time.mktime(time.strptime(str(as_of), "%Y-%m-%d"))
    except (TypeError, ValueError):
        return None
    return (time.time() - then) / 86400


def update_entry(key: str, **fields: Any) -> dict[str, Any]:
    """Merge ``fields`` into ``models.catalog[key]`` and save; returns the entry.

    Goes through ``config.update_settings`` (one locked read-modify-write), so
    a concurrent settings save cannot lose it.
    """
    result: dict[str, Any] = {}

    def mutate(settings: dict[str, Any]) -> None:
        models = settings.setdefault("models", {})
        if not isinstance(models, dict):
            models = settings["models"] = {}
        cat = models.setdefault("catalog", {})
        if not isinstance(cat, dict):
            cat = models["catalog"] = {}
        entry = cat.get(key) if isinstance(cat.get(key), dict) else {}
        entry.update(fields)
        cat[key] = entry
        result.update(entry)

    config.update_settings(mutate)
    return result


def _resolve(facade: Any, entry: str) -> tuple[str, str, Any]:
    """``(provider, model, provider object | None)`` for a chain entry;
    a bare ``provider`` resolves to that provider's configured model."""
    name, _, model = str(entry).partition(":")
    name = name.strip().lower()
    provider = facade._named().get(name)
    model = model.strip()
    if not model and provider is not None:
        try:
            model = facade.model_of(provider)
        except Exception:
            model = ""
    return name, model, provider


def _key(name: str, model: str) -> str:
    return f"{name}:{model}" if model else name


def models_in_use(facade: Any) -> dict[str, list[str]]:
    """``provider:model`` → the roles naming it (``(global)`` = the primary
    of the chain used when no role is set)."""
    used: dict[str, list[str]] = {}
    roles = config.get("models.roles") or {}
    for role, chain in (roles.items() if isinstance(roles, dict) else []):
        for entry in (chain if isinstance(chain, list) else []):
            name, model, _ = _resolve(facade, entry)
            used.setdefault(_key(name, model), []).append(str(role))
    try:
        name, model = facade.effective()
    except Exception:
        name, model = "", ""
    if name:
        used.setdefault(_key(name, model), []).append("(global)")
    return used


def is_agent_role(role: str) -> bool:
    return role == "orchestrator" or role.startswith("worker:")


def validate_role_setting(role: str, chain: Any, facade: Any = None) -> list[str]:
    """Problems with setting ``models.roles[role] = chain``; empty = allowed.

    The path the web model picker uses (0d). An agent role's (``orchestrator``,
    ``worker:*``) first choice must be ``verified`` by ``kravein models
    probe``: an unprobed or failed model leading an agent loop is exactly the
    "id exists but cannot call tools here" trap. A hand edit of settings.json
    is not blocked; ``doctor`` warns about it instead.
    """
    from . import llm

    facade = facade or llm.client()
    if not isinstance(role, str) or not llm.valid_role(role):
        return [f"unknown role '{role}' (known: {', '.join(llm.ROLES)}, worker:<profile>)"]
    if not isinstance(chain, list) or not chain or not all(isinstance(e, str) and e.strip() for e in chain):
        return ["the chain must be a non-empty list of \"provider:model\" strings"]
    problems = []
    for entry in chain:
        name, _, provider = _resolve(facade, entry)
        if provider is None:
            problems.append(f"unknown provider '{name}' in '{entry}'")
    if problems:
        return problems
    if is_agent_role(role):
        name, model, _ = _resolve(facade, chain[0])
        key = _key(name, model)
        state = tool_status(key)
        if state != "verified":
            problems.append(
                f"{key} is {state} for tool calling; run `kravein models probe {key}` "
                f"before making it the first choice for '{role}'"
            )
    return problems


def set_role_chain(role: str, chain: list[str] | None, facade: Any = None) -> None:
    """Validate, then save ``models.roles[role]``; ``None``/``[]`` removes it.

    Raises ``ValueError`` listing the problems; nothing is written then.
    """
    if chain:
        problems = validate_role_setting(role, chain, facade)
        if problems:
            raise ValueError("; ".join(problems))

    def mutate(settings: dict[str, Any]) -> None:
        models = settings.setdefault("models", {})
        roles = models.setdefault("roles", {})
        if chain:
            roles[role] = [str(e).strip() for e in chain]
        else:
            roles.pop(role, None)

    config.update_settings(mutate)


def doctor_warnings(facade: Any = None) -> list[str]:
    """Role-chain and catalog checks for ``kravein doctor``."""
    from . import llm

    facade = facade or llm.client()
    warnings: list[str] = []
    roles = config.get("models.roles") or {}
    for role, chain in (roles.items() if isinstance(roles, dict) else []):
        if not llm.valid_role(str(role)):
            warnings.append(f"role '{role}' is not a known role and is never used")
            continue
        entries = chain if isinstance(chain, list) else []
        live = 0
        for entry in entries:
            _, _, provider = _resolve(facade, entry)
            if provider is not None and provider.available():
                live += 1
        if live < 2:
            warnings.append(
                f"role '{role}' has {live} live chain entr{'y' if live == 1 else 'ies'}: "
                "add a second available provider so a failure can fail over"
            )
    for key, entry in sorted(catalog().items()):
        age = _age_days(entry.get("as_of"))
        if age is None:
            warnings.append(f"catalog entry {key} has no valid as_of date (YYYY-MM-DD)")
        elif age > CATALOG_MAX_AGE_DAYS:
            warnings.append(f"catalog entry {key} is {int(age)} days old: re-probe it "
                            "and check its price")
    for key, used_by in sorted(models_in_use(facade).items()):
        state = tool_status(key)
        if state != "verified":
            warnings.append(f"{key} ({', '.join(used_by)}) is {state} for tool calling: "
                            f"run `kravein models probe {key}`")
    return warnings


def list_report(facade: Any = None) -> str:
    """``kravein models list``: the catalog plus which roles use what."""
    from . import llm

    facade = facade or llm.client()
    used = models_in_use(facade)
    cat = catalog()
    keys = sorted(set(cat) | set(used))
    if not keys:
        return "No models in the catalog and no roles configured."
    header = ("model", "tools", "verified", "price in/cached/out", "as_of", "used by")
    rows = [header]
    for key in keys:
        entry = cat.get(key) or {}
        name, _, model = key.partition(":")
        price, source = price_source(name, model)
        if price is None:
            price_text = "-"
        else:
            price_text = (f"{price['input']:g}/{price.get('cached', price['input']):g}/"
                          f"{price['output']:g}" + (" (models.prices)" if source == "prices" else ""))
        reason = (entry.get("probe") or {}).get("error") if isinstance(entry.get("probe"), dict) else ""
        rows.append((
            key, tool_status(key) + (f" ({reason})" if reason else ""),
            str(entry.get("verified_at") or "-"), price_text, str(entry.get("as_of") or "-"),
            ", ".join(used.get(key, [])) or "-",
        ))
    widths = [max(len(r[i]) for r in rows) for i in range(len(header))]
    lines = ["Model catalog (prices: USD per 1M tokens):"]
    for row in rows:
        lines.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(row)).rstrip())
    return "\n".join(lines)


# -- probe ------------------------------------------------------------------

PROBE_PLAIN = "Reply with exactly one word: ready"
PROBE_TOOL = "How full is my disk right now? Check with one of your tools before you answer."
PROBE_RESULT = "(probe) result: / is 41% full, 120 GB free."


class ProbeFailed(Exception):
    def __init__(self, step: str, reason: str) -> None:
        super().__init__(f"{step}: {reason}")
        self.step, self.reason = step, reason


def _reason(exc: BaseException) -> str:
    """An error class plus an HTTP status when one is visible; never the
    message itself (it can quote a request, a key or a gateway's echo)."""
    import re

    match = re.search(r"\b([45]\d\d)\b", str(exc))
    return f"{type(exc).__name__} {match.group(1)}" if match else type(exc).__name__


def _probe_surface() -> tuple[str, list[dict[str, Any]]]:
    """The orchestrator profile's real system prompt and ``tools[]``, so the
    probe measures what an agent turn sends ("benchmark with the real tool
    schema", hard-won-llm-agent)."""
    from .agent import ORCHESTRATOR, Agent
    from . import registry

    registry.load_skills()
    agent = Agent(source="probe", profile=ORCHESTRATOR)
    return agent.system_prompt(), registry.tool_declarations(names=agent._allowed)


def probe(target: str, facade: Any = None) -> dict[str, Any]:
    """Probe ``provider:model`` with three real calls and record the outcome.

    1. a plain answer; 2. a tool call against the orchestrator's real prompt
    and tools (checked against that set; **no skill is ever executed**);
    3. a second turn replaying the call (opaque block included) with a
    synthetic result. Each call is a ledger row (role ``probe``). Calls the
    provider directly: no chain, no breaker, so a failure is this model's.
    Costs real tokens: run only by the owner (``kravein models probe``).
    """
    from . import llm

    facade = facade or llm.client()
    name, model, provider = _resolve(facade, target)
    if provider is None:
        raise ValueError(f"unknown provider '{name}' (known: {', '.join(sorted(facade.known_providers()))})")
    key = _key(name, model)
    totals = {"input": 0, "cached": 0, "output": 0, "reported": 0}
    started = time.monotonic()

    def call(step: str, messages: list[dict], system: str = "", tools: Any = None):
        t0 = time.monotonic()
        try:
            reply = provider.complete(messages, system=system, tools=tools, model=model)
        except Exception as exc:
            record(role="probe", agent="probe", provider=name, model=model,
                   latency_ms=(time.monotonic() - t0) * 1000, ok=False, error=_reason(exc))
            raise ProbeFailed(step, _reason(exc)) from None
        record(role="probe", agent="probe", provider=name, model=reply.model or model,
               usage=reply.usage, latency_ms=(time.monotonic() - t0) * 1000, ok=True)
        if reply.usage is not None:
            totals["reported"] += 1
            for field in ("input", "cached", "output"):
                totals[field] += int(getattr(reply.usage, field))
        return reply

    outcome: dict[str, Any] = {"ok": False, "step": "", "error": ""}
    try:
        if not provider.available():
            raise ProbeFailed("setup", "unavailable (no key or not configured)")
        if not model:
            raise ProbeFailed("setup", "no model")
        first = call("plain", [{"role": "user", "content": PROBE_PLAIN}])
        if not (first.text or "").strip():
            raise ProbeFailed("plain", "empty_reply")
        system, tools = _probe_surface()
        allowed = {t["name"] for t in tools}
        ask = [{"role": "user", "content": PROBE_TOOL}]
        second = call("tool_call", ask, system=system, tools=tools)
        if not second.tool_calls:
            raise ProbeFailed("tool_call", "no_tool_call")
        if any(c.name not in allowed for c in second.tool_calls):
            raise ProbeFailed("tool_call", "unknown_tool")
        replay = ask + [{
            "role": "assistant", "content": second.text,
            "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments,
                            "signature": c.signature} for c in second.tool_calls],
        }] + [{"role": "tool", "tool_call_id": c.id, "name": c.name, "content": PROBE_RESULT}
              for c in second.tool_calls]
        third = call("replay", replay, system=system, tools=tools)
        if not (third.text or "").strip() and not third.tool_calls:
            raise ProbeFailed("replay", "empty_reply")
        outcome["ok"] = True
    except ProbeFailed as failed:
        outcome.update(step=failed.step, error=failed.reason)

    probe_record = {
        "ok": outcome["ok"], "at": _today(), "step": outcome["step"], "error": outcome["error"],
        "latency_ms": int(round((time.monotonic() - started) * 1000)),
        **({k: totals[k] for k in ("input", "cached", "output")} if totals["reported"] else {}),
    }
    fields: dict[str, Any] = {
        "as_of": _today(),
        "tool_calling": "verified" if outcome["ok"] else "failed",
        "probe": probe_record,
    }
    existing = catalog().get(key) or {}
    if outcome["ok"]:
        fields["verified_at"] = _today()
    # Facts Kravein knows from the adapter, filled only when not set by hand.
    if "streaming" not in existing:
        fields["streaming"] = False          # no Kravein adapter streams yet
    if "opaque_replay" not in existing:
        fields["opaque_replay"] = name in ("gemini", "anthropic")
    cap = getattr(provider, "MAX_TOOLS", None)
    if "max_tools" not in existing and isinstance(cap, int) and cap < _UNLIMITED_TOOLS:
        fields["max_tools"] = cap
    entry = update_entry(key, **fields)
    audit.record("models.probe", model=key, ok=outcome["ok"], step=outcome["step"],
                 error=outcome["error"])
    return {"key": key, **entry}
