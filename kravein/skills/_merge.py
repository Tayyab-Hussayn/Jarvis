"""Helpers for merged skills (one skill, an ``action`` parameter).

A merged skill is a thin dispatcher over plain functions that used to be
skills of their own. The plain functions keep their ``safety.enforce`` calls
and declared targets, so every state-changing action is still enforced
exactly where it was; the dispatcher only chooses which one runs. Old tool
names are registered as aliases with a preset ``action`` (see ``registry``).
"""

from __future__ import annotations

import inspect
from typing import Any, Callable


def run(func: Callable, /, _source: str = "local", _confirm: bool = False,
        **kwargs: Any) -> Any:
    """Call ``func`` with only the arguments that were actually given.

    ``None`` means "not provided", so the plain function's own default
    applies (``change_volume`` with no delta still means +10). The injected
    ``_source``/``_confirm`` go through only to functions that accept them.
    """
    params = inspect.signature(func).parameters
    call = {k: v for k, v in kwargs.items() if v is not None and k in params}
    if "_source" in params:
        call["_source"] = _source
    if "_confirm" in params:
        call["_confirm"] = _confirm
    return func(**call)


def unknown(action: str, valid: list[str]) -> str:
    return f"Unknown action '{action}'. Use one of: {', '.join(valid)}."
