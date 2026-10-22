"""Lets the model widen its own toolset when request routing narrowed it."""

from __future__ import annotations

from ..registry import categories, skill


@skill(category="tools")
def more_tools(categories_wanted: list[str], _context=None) -> str:
    """Make more skills available for the rest of this request.

    Only the skills for the topics you were shown are loaded. If the task needs
    something that is not among them, ask for that topic here instead of
    guessing or giving up. Topics: apps, browser, documents, files, games,
    interact, llm, media, memory, notify, phone, power, routines, schedule,
    settings, shell, system, terminal, web, windows.

    Args:
        categories_wanted: Topic names to add, from the list above.
    """
    available = {name: group for name, group in categories().items() if name != "tools"}
    wanted = [c.strip().lower() for c in (categories_wanted or []) if c.strip()]
    unknown = [c for c in wanted if c not in available]
    good = [c for c in wanted if c in available]

    if not good:
        return (
            "No such topic. Choose from: " + ", ".join(sorted(available)) + "."
        )

    enable = getattr(_context, "enable_categories", None)
    if enable is None:
        return "Tool routing is not active here; every skill is already available."
    enable(good)

    names = [s.name for c in good for s in available[c]]
    note = f" (unknown, ignored: {', '.join(unknown)})" if unknown else ""
    return f"Added {', '.join(good)}{note}. Now available: {', '.join(sorted(names))}."
