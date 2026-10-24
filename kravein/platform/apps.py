"""Application discovery and launching.

The Windows build launched apps by pressing the Start key and *typing the
app's name* (``open_app.py:_launch_windows``) — a fragile UI hack. Linux has
a real registry: the freedesktop ``.desktop`` entry spec. This module indexes
those entries, matches the user's phrasing against them, and launches through
the compositor so window rules apply.

Falls back to a bare PATH binary, then ``xdg-open`` for URLs and files.
"""

from __future__ import annotations

import functools
import os
import re
import shlex
import time
from dataclasses import dataclass
from pathlib import Path

from . import desktop, detect, shell


@dataclass
class DesktopEntry:
    """A parsed .desktop application entry."""

    name: str
    exec_cmd: str
    path: Path
    generic_name: str = ""
    comment: str = ""
    categories: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    terminal: bool = False
    no_display: bool = False
    startup_wm_class: str = ""
    icon: str = ""

    @property
    def id(self) -> str:
        """The desktop file id, e.g. 'firefox.desktop'."""
        return self.path.name

    def command(self) -> list[str]:
        """Exec line with field codes stripped, ready for exec."""
        # %f %F %u %U %i %c %k are launcher placeholders, not arguments.
        cleaned = re.sub(r"%[fFuUdDnNickvm]", "", self.exec_cmd).strip()
        try:
            return shlex.split(cleaned)
        except ValueError:
            return cleaned.split()

    def search_text(self) -> str:
        return " ".join([
            self.name, self.generic_name, self.comment,
            " ".join(self.keywords), Path(self.command()[0]).name if self.command() else "",
        ]).lower()


def _desktop_dirs() -> list[Path]:
    """Every directory the spec says applications live in."""
    dirs: list[Path] = []

    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    dirs.append(Path(data_home) / "applications")

    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    for raw in data_dirs.split(":"):
        if raw.strip():
            dirs.append(Path(raw.strip()) / "applications")

    # Flatpak exports live outside XDG_DATA_DIRS on some setups.
    dirs.append(Path.home() / ".local/share/flatpak/exports/share/applications")
    dirs.append(Path("/var/lib/flatpak/exports/share/applications"))
    # Snap, for completeness.
    dirs.append(Path("/var/lib/snapd/desktop/applications"))

    seen: set[Path] = set()
    out: list[Path] = []
    for d in dirs:
        if d in seen:
            continue
        seen.add(d)
        if d.is_dir():
            out.append(d)
    return out


def _parse_entry(path: Path) -> DesktopEntry | None:
    """Read the [Desktop Entry] group out of a .desktop file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    in_group = False
    fields: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            # Only the main group; ignore [Desktop Action ...] sections.
            in_group = stripped == "[Desktop Entry]"
            continue
        if not in_group or "=" not in stripped or stripped.startswith("#"):
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        # Skip localised keys like Name[de]; we want the untranslated one.
        if "[" in key:
            continue
        fields.setdefault(key, value.strip())

    if fields.get("Type", "Application") != "Application":
        return None
    if not fields.get("Exec") or not fields.get("Name"):
        return None
    if fields.get("Hidden", "").lower() == "true":
        return None

    def split_list(raw: str) -> tuple[str, ...]:
        return tuple(p.strip() for p in raw.split(";") if p.strip())

    return DesktopEntry(
        name=fields["Name"],
        exec_cmd=fields["Exec"],
        path=path,
        generic_name=fields.get("GenericName", ""),
        comment=fields.get("Comment", ""),
        categories=split_list(fields.get("Categories", "")),
        keywords=split_list(fields.get("Keywords", "")),
        terminal=fields.get("Terminal", "").lower() == "true",
        no_display=fields.get("NoDisplay", "").lower() == "true",
        startup_wm_class=fields.get("StartupWMClass", ""),
        icon=fields.get("Icon", ""),
    )


@functools.lru_cache(maxsize=1)
def index() -> list[DesktopEntry]:
    """Index every installed application. Cached for the process lifetime."""
    entries: dict[str, DesktopEntry] = {}
    for directory in _desktop_dirs():
        try:
            files = sorted(directory.glob("*.desktop"))
        except OSError:
            continue
        for path in files:
            # Earlier dirs (user overrides) win over later ones (system).
            if path.name in entries:
                continue
            entry = _parse_entry(path)
            if entry:
                entries[path.name] = entry
    return list(entries.values())


def refresh_index() -> int:
    index.cache_clear()
    return len(index())


# Phrasings the user is likely to say, mapped to what to actually look for.
_ALIASES = {
    "browser": "firefox",
    "web browser": "firefox",
    "terminal": "",           # resolved by category below
    "console": "",
    "file manager": "",
    "files": "",
    "explorer": "",
    "file explorer": "",
    "text editor": "",
    "calculator": "",
    "task manager": "",
    "system monitor": "",
    "settings": "",
    "control panel": "",
    "vscode": "code",
    "vs code": "visual studio code",
    "notepad": "text editor",
    "cmd": "terminal",
    "powershell": "terminal",
}

# Fallback by freedesktop category when the user names a *kind* of app.
_CATEGORY_HINTS = {
    "terminal": ("TerminalEmulator",),
    "console": ("TerminalEmulator",),
    "file manager": ("FileManager",),
    "files": ("FileManager",),
    "explorer": ("FileManager",),
    "file explorer": ("FileManager",),
    "text editor": ("TextEditor",),
    "notepad": ("TextEditor",),
    "calculator": ("Calculator",),
    "task manager": ("Monitor", "System"),
    "system monitor": ("Monitor",),
    "settings": ("Settings",),
    "control panel": ("Settings",),
    "music player": ("AudioVideo", "Player"),
    "video player": ("Video", "Player"),
    "image viewer": ("Graphics", "Viewer"),
}


def find(query: str, limit: int = 8) -> list[DesktopEntry]:
    """Rank installed applications against a natural-language query."""
    text = (query or "").strip().lower()
    if not text:
        return []

    if text in _ALIASES and _ALIASES[text]:
        text = _ALIASES[text]

    entries = index()
    scored: list[tuple[int, DesktopEntry]] = []

    # When the user names a *kind* of app ("terminal", "file manager"), the
    # freedesktop category is a far better signal than a name substring —
    # otherwise "terminal" matches any editor whose description mentions it.
    wanted_categories: tuple[str, ...] = ()
    for hint, categories in _CATEGORY_HINTS.items():
        if text == hint:
            wanted_categories = categories
            break

    for entry in entries:
        name = entry.name.lower()
        binary = Path(entry.command()[0]).name.lower() if entry.command() else ""
        score = 0

        if name == text or binary == text:
            score = 100
        elif entry.id.removesuffix(".desktop").lower() == text:
            score = 95
        elif name.startswith(text) or binary.startswith(text):
            score = 80
        elif text in name:
            score = 60
        elif text in binary:
            score = 55
        elif any(text == k.lower() for k in entry.keywords):
            score = 50
        elif text in entry.search_text():
            score = 30

        # A category match outranks any weak textual match.
        if wanted_categories and any(c in entry.categories for c in wanted_categories):
            score = max(score, 90)

        # Prefer things the user can actually see in a menu.
        if entry.no_display:
            score -= 25

        if score > 0:
            scored.append((score, entry))

    if not scored:
        for hint, categories in _CATEGORY_HINTS.items():
            if hint in text or text in hint:
                for entry in entries:
                    if any(c in entry.categories for c in categories):
                        bonus = 40 - (10 if entry.no_display else 0)
                        scored.append((bonus, entry))
                break

    scored.sort(key=lambda pair: (-pair[0], pair[1].name.lower()))
    return [entry for _, entry in scored[:limit]]


def is_running(entry: DesktopEntry) -> bool:
    """Is this app already on screen?

    Matches StartupWMClass first (the spec's own mechanism for tying a
    window back to its .desktop entry), then the binary name.
    """
    wm_class = (entry.startup_wm_class or "").lower()
    binary = Path(entry.command()[0]).name.lower() if entry.command() else ""

    for win in desktop.backend().list_windows():
        app = (win.app or "").lower()
        if wm_class and app == wm_class:
            return True
        if binary and (app == binary or app.startswith(binary)):
            return True
    return False


def focus_running(entry: DesktopEntry) -> bool:
    """Focus an existing window for this app instead of opening a second one."""
    wm_class = (entry.startup_wm_class or "").lower()
    binary = Path(entry.command()[0]).name.lower() if entry.command() else ""

    backend = desktop.backend()
    for win in backend.list_windows():
        app = (win.app or "").lower()
        if (wm_class and app == wm_class) or (binary and app.startswith(binary)):
            return backend.focus(win)
    return False


def _terminal_command() -> list[str]:
    """Find a terminal emulator to host Terminal=true entries."""
    for term, flag in (
        ("kitty", "-e"), ("foot", "-e"), ("alacritty", "-e"),
        ("wezterm", "-e"), ("konsole", "-e"), ("gnome-terminal", "--"),
        ("xterm", "-e"),
    ):
        if shell.have(term):
            return [term, flag]
    return []


def launch(entry: DesktopEntry, *, focus_existing: bool = True) -> str:
    """Launch an application entry."""
    if focus_existing and is_running(entry):
        if focus_running(entry):
            return f"{entry.name} was already running — focused it."

    command = entry.command()
    if not command:
        return f"{entry.name} has no runnable Exec line."

    if entry.terminal:
        term = _terminal_command()
        if not term:
            return f"{entry.name} needs a terminal, and none was found."
        command = term + command

    # gio launch honours the full entry (env, working dir, startup notify).
    if shell.have("gio"):
        try:
            shell.spawn(["gio", "launch", str(entry.path)])
            return f"Launched {entry.name}."
        except RuntimeError:
            pass

    if shell.have("gtk-launch"):
        result = shell.run(
            ["gtk-launch", entry.id.removesuffix(".desktop")], timeout=8
        )
        if result.ok:
            return f"Launched {entry.name}."

    try:
        desktop.backend().spawn(command)
    except RuntimeError as exc:
        return f"Could not launch {entry.name}: {exc}"

    return f"Launched {entry.name}."


def list_entries(category: str = "", limit: int = 0) -> list[dict]:
    """Structured application list for UI panels: visible entries, by name.

    ``list_apps`` renders the same data as text for the model.
    """
    needle = category.lower()
    entries = [
        e for e in index()
        if not e.no_display
        and (not needle or any(needle in c.lower() for c in e.categories))
    ]
    entries.sort(key=lambda e: e.name.lower())
    if limit > 0:
        entries = entries[:limit]
    return [
        {"name": e.name, "id": e.id, "icon": e.icon, "exec": e.exec_cmd}
        for e in entries
    ]


def _is_exact(entry: DesktopEntry, text: str) -> bool:
    binary = Path(entry.command()[0]).name.lower() if entry.command() else ""
    return text in (
        entry.name.lower(), binary, entry.id.removesuffix(".desktop").lower()
    )


def check_workspace(workspace: str) -> str | None:
    """Normalise a workspace argument, or None when it is not acceptable.

    Positive numbers and the names of existing workspaces only: the value
    ends up inside a compositor command, and a workspace that does not exist
    and is not a number would be created by accident from a typo.
    """
    text = str(workspace).strip()
    if text.isdigit() and int(text) > 0:
        return str(int(text))
    if text and text in desktop.backend().list_workspaces():
        return text
    return None


def launch_on_workspace(query: str, workspace: str) -> str:
    """Start a new instance of an app on a workspace without switching to it."""
    backend = desktop.backend()
    if not backend.can_spawn_on_workspace:
        return (
            f"Opening an app on a chosen workspace is not supported on this "
            f"desktop ({backend.name}). Open it normally instead."
        )

    target = check_workspace(workspace)
    if target is None:
        return (
            f"'{workspace}' is not a workspace I can use. Give a workspace "
            "number or the name of an existing workspace."
        )

    matches = find(query)
    if not matches:
        return (
            f"Could not find an application matching '{query}'. "
            f"Try `search_apps {query}` to see what is installed."
        )
    text = query.strip().lower()
    exact = [m for m in matches if _is_exact(m, text)]
    if len(exact) == 1:
        entry = exact[0]
    elif len(matches) == 1:
        entry = matches[0]
    else:
        names = ", ".join(m.name for m in (exact or matches)[:6])
        return (
            f"'{query}' matches several applications ({names}). "
            "Say which one to open."
        )

    command = entry.command()
    if not command:
        return f"{entry.name} has no runnable Exec line."
    if entry.terminal:
        term = _terminal_command()
        if not term:
            return f"{entry.name} needs a terminal, and none was found."
        command = term + command

    before = {w.id for w in backend.list_windows()}
    try:
        accepted = backend.spawn_on_workspace(command, target)
    except RuntimeError as exc:
        return f"Could not launch {entry.name}: {exc}"
    if not accepted:
        return f"The compositor refused to start {entry.name} on workspace {target}."

    # The dispatch only says the process was started. A launcher that forks
    # or hands off to a running instance (browsers) ignores the workspace
    # rule, so look for the window before claiming where it went.
    for _ in range(20):
        time.sleep(0.3)
        new = [w for w in backend.list_windows() if w.id not in before]
        if new:
            win = new[0]
            if str(win.workspace) == target:
                return f"Started {entry.name} on workspace {target}."
            return (
                f"Started {entry.name}, but its window opened on workspace "
                f"{win.workspace}, not {target}."
            )
    return (
        f"Started {entry.name} for workspace {target}; no new window has "
        "appeared yet, so I cannot confirm where it landed."
    )


def launch_by_name(query: str) -> str:
    """Resolve a name to an app and launch it."""
    matches = find(query)

    if matches:
        return launch(matches[0])

    # Not an installed .desktop app — maybe it is a plain binary.
    binary = shell.which(query) or shell.which(query.lower().replace(" ", "-"))
    if binary:
        shell.spawn([binary])
        return f"Launched {Path(binary).name}."

    # Or a URL / file path.
    if re.match(r"^(https?|file|mailto)://", query) or Path(query).expanduser().exists():
        return open_uri(query)

    return (
        f"Could not find an application matching '{query}'. "
        f"Try `apps_search {query}` to see what is installed."
    )


def open_uri(target: str) -> str:
    """Open a file, directory or URL with the user's default handler.

    Replaces ``os.startfile`` (used in the office/pdf/docx modules).
    """
    text = (target or "").strip()
    if not text:
        return "Nothing to open."

    path = Path(text).expanduser()
    if path.exists():
        text = str(path.resolve())
    elif not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", text):
        return f"Not found: {target}"

    opener = shell.first_available("xdg-open", "gio")
    if not opener:
        return "No opener available (install xdg-utils)."

    args = ["gio", "open", text] if opener == "gio" else ["xdg-open", text]
    try:
        shell.spawn(args)
    except RuntimeError as exc:
        return f"Could not open {target}: {exc}"

    time.sleep(0.3)
    return f"Opened {text}."


def describe_matches(query: str, limit: int = 8) -> str:
    """List installed apps matching a query."""
    matches = find(query, limit=limit)
    if not matches:
        return f"No installed application matches '{query}'."
    lines = [f"{len(matches)} match(es) for '{query}':"]
    for entry in matches:
        binary = Path(entry.command()[0]).name if entry.command() else "?"
        desc = entry.comment or entry.generic_name
        lines.append(f"  {entry.name} ({binary})" + (f" — {desc}" if desc else ""))
    return "\n".join(lines)
