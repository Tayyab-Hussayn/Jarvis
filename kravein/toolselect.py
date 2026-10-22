"""Choose which skills a request is shown, instead of showing all of them.

Every request used to carry every tool schema — ~14k tokens, and a provider
with a tool cap (NovaRouter, 128) could not serve a turn at all. Most requests
need one or two categories.

Routing is a *narrowing*, never a gate, and each layer exists because the one
before it can be wrong:

* a few small categories are always present (``CORE``), including ``shell``,
  whose ``run_command`` is a catch-all when a category was missed;
* the categories used earlier in the conversation stay on, so "now close it"
  still has the window tools;
* ``more_tools`` lets the model ask for a category it was not shown;
* when nothing matches at all, the answer is *everything* — exactly what
  happened before routing existed. A router that guesses wrong must degrade to
  the old behaviour, not to a model that cannot do what was asked.

Matching is plain substring search on the request: no model call, no latency.
Over-selecting is harmless (it only costs tokens); under-selecting is what the
layers above are for. Keywords therefore lean generous, and include the Roman
Urdu the assistant is actually spoken to in.
"""

from __future__ import annotations

# Small and used constantly. ``shell`` is the escape hatch for a missed match.
CORE: frozenset[str] = frozenset({"system", "memory", "windows", "apps", "shell"})

# Present only when routing is on; the model's way to widen its own toolset.
ROUTING_ONLY_SKILLS: frozenset[str] = frozenset({"more_tools"})

CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "browser": (
        "browser", "chrome", "tab", "website", "site", "page", "youtube",
        "google", "duckduckgo", "amazon", "github", "click", "login",
        "scroll", "search",
    ),
    "web": (
        "http", "www.", ".com", "url", "link", "weather", "mausam", "internet",
        "search", "google", "open the", "website", "news",
    ),
    "files": (
        "file", "folder", "directory", "dir ", "path", "delete", "remove",
        "rename", "move", "copy", "download", "desktop", "documents",
        "dolphin", "organize", "organise", "disk", "space",
    ),
    "documents": (
        "document", "pdf", "docx", "word", "spreadsheet", "excel", "xlsx",
        "presentation", "slides", "pptx", "report", "convert",
    ),
    "settings": (
        "volume", "awaz", "sound", "mute", "brightness", "wifi", "wi-fi",
        "bluetooth", "dark mode", "light mode", "theme", "microphone", "mic",
        "audio", "monitor", "screen off", "play", "pause", "next track",
    ),
    "media": (
        "spotify", "song", "music", "gana", "video", "wallpaper", "play",
        "download", "movie", "playlist", "album", "artist",
    ),
    "interact": (
        "click", "type ", "typing", "press", "keyboard", "mouse", "cursor",
        "screenshot", "screen", "clipboard", "copy", "paste", "scroll",
        "key", "hotkey",
    ),
    "terminal": (
        "terminal", "console", "konsole", "foot", "kitty", "alacritty",
        "tmux", "shell window",
    ),
    "notify": (
        "remind", "reminder", "notification", "notify", "alert", "speak",
        "say ", "bolo", "yaad", "tell me",
    ),
    "schedule": (
        "schedule", "remind", "cron", "every ", "daily", "weekly", "tomorrow",
        "kal", "baje", "tonight", "later", "in an hour", "in 10", "job",
        "recurring", "repeat",
    ),
    "routines": (
        "routine", "habit", "streak", "step", "morning", "evening", "chain",
        "tracker", "done", "skip",
    ),
    "power": (
        "shutdown", "shut down", "reboot", "restart", "suspend", "sleep",
        "lock", "log out", "logout", "battery", "power",
    ),
    "games": ("game", "steam", "play ", "launch", "gaming"),
    "llm": (
        "model", "provider", "llm", "failover", "gemini", "cline",
        "openrouter", "novarouter",
    ),
}


def classify(text: str) -> set[str]:
    """Categories whose hints appear in ``text``. Empty means "no idea"."""
    low = f" {(text or '').lower()} "
    return {
        category
        for category, hints in CATEGORY_HINTS.items()
        if any(h in low for h in hints)
    }


def select(text: str, sticky: set[str] | frozenset[str] = frozenset()) -> set[str] | None:
    """Categories to expose for this request, or ``None`` for every skill.

    ``None`` is the fail-open answer, used when the request matched no
    category and nothing earlier in the conversation narrows it.
    """
    matched = classify(text)
    if not matched and not sticky:
        return None
    return matched | set(sticky) | set(CORE)
