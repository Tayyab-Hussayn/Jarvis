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


def price_of(provider: str, model: str) -> dict[str, float] | None:
    """``models.prices["provider:model"]`` (USD per 1M tokens), or ``None``.

    Read as a whole dict, never via a dotted ``config.get``: model ids hold
    dots (``gemini-3.5-flash-lite``) and would split into nonsense paths.
    """
    prices = config.get("models.prices") or {}
    if not isinstance(prices, dict):
        return None
    entry = prices.get(f"{provider}:{model}")
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
