"""Game library management.

Native rewrite of ``actions/game_updater.py``, which found Steam and Epic
through the Windows registry and then drove their GUIs with pywinauto.

On Linux:
  * Steam libraries are discovered from ``libraryfolders.vdf`` and the
    per-app ``appmanifest_*.acf`` files, which is the documented layout.
  * Steam is driven through its ``steam://`` URL handler rather than by
    clicking its UI, so no window automation is needed.
  * Epic has no native client; Heroic and Legendary are the real equivalents
    and are used when installed.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..platform import apps, shell
from ..registry import skill
from ..safety import Danger

_STEAM_ROOTS = (
    Path.home() / ".steam" / "steam",
    Path.home() / ".local" / "share" / "Steam",
    Path.home() / ".steam" / "root",
    Path.home() / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam",
)


def _steam_root() -> Path | None:
    for root in _STEAM_ROOTS:
        if (root / "steamapps").is_dir():
            return root
    return None


def _parse_vdf_pairs(text: str) -> list[tuple[str, str]]:
    """Pull "key" "value" pairs out of Valve's KeyValues format."""
    return re.findall(r'"([^"]+)"\s+"([^"]*)"', text)


def _library_paths(root: Path) -> list[Path]:
    """Every Steam library folder, including those on other drives."""
    libraries = [root / "steamapps"]

    vdf = root / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return libraries
        for key, value in _parse_vdf_pairs(text):
            if key == "path":
                candidate = Path(value) / "steamapps"
                if candidate.is_dir() and candidate not in libraries:
                    libraries.append(candidate)
    return libraries


def _installed_games(root: Path) -> list[dict[str, str]]:
    """Read every appmanifest to build the installed-game list."""
    games: list[dict[str, str]] = []
    for library in _library_paths(root):
        try:
            manifests = sorted(library.glob("appmanifest_*.acf"))
        except OSError:
            continue
        for manifest in manifests:
            try:
                text = manifest.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fields = dict(_parse_vdf_pairs(text))
            appid = fields.get("appid", "")
            name = fields.get("name", "")
            if not appid or not name:
                continue
            size = fields.get("SizeOnDisk", "0")
            try:
                size_gb = f"{int(size) / (1024 ** 3):.1f} GB"
            except ValueError:
                size_gb = "?"
            # StateFlags 4 means fully installed; anything else is in flux.
            state = fields.get("StateFlags", "")
            games.append({
                "appid": appid,
                "name": name,
                "size": size_gb,
                "library": str(library),
                "state": "installed" if state == "4" else f"state {state}",
            })
    return sorted(games, key=lambda g: g["name"].lower())


@skill(category="games", danger=Danger.SAFE)
def list_games() -> str:
    """List installed games from Steam and, if present, Heroic/Legendary."""
    sections: list[str] = []

    root = _steam_root()
    if root is None:
        sections.append("Steam: not installed (no steamapps directory found).")
    else:
        games = _installed_games(root)
        if not games:
            sections.append(f"Steam: installed at {root}, but no games found.")
        else:
            lines = [f"Steam ({len(games)} game(s), root {root}):"]
            for game in games:
                note = "" if game["state"] == "installed" else f"  [{game['state']}]"
                lines.append(f"  {game['name']}  ({game['size']}, appid {game['appid']}){note}")
            sections.append("\n".join(lines))

    if shell.have("legendary"):
        result = shell.run(["legendary", "list-installed", "--csv"], timeout=45)
        if result.ok and result.stdout.strip():
            rows = result.stdout.strip().splitlines()[1:]
            if rows:
                lines = [f"Epic via Legendary ({len(rows)} game(s)):"]
                for row in rows[:40]:
                    parts = row.split(",")
                    if len(parts) >= 2:
                        lines.append(f"  {parts[1]}  (id {parts[0]})")
                sections.append("\n".join(lines))
    elif shell.have("heroic") or apps.find("heroic"):
        sections.append(
            "Epic: Heroic Games Launcher is installed. It has no CLI, so open "
            "it to manage Epic titles."
        )
    else:
        sections.append(
            "Epic: no Linux client found. Install Heroic (yay -S heroic-games-launcher-bin) "
            "or Legendary (sudo pacman -S legendary)."
        )

    return "\n\n".join(sections)


@skill(category="games", danger=Danger.MODERATE)
def launch_game(name: str) -> str:
    """Launch a game by name through Steam or Legendary.

    Args:
        name: The game's name, or part of it.
    """
    needle = (name or "").strip().lower()
    if not needle:
        return "Which game should I launch?"

    root = _steam_root()
    if root is not None:
        matches = [
            game for game in _installed_games(root)
            if needle in game["name"].lower()
        ]
        if matches:
            game = matches[0]
            # steam:// is Steam's own IPC; far more reliable than UI clicking.
            apps.open_uri(f"steam://rungameid/{game['appid']}")
            return f"Launching {game['name']} via Steam (appid {game['appid']})."

    if shell.have("legendary"):
        listing = shell.run(["legendary", "list-installed", "--csv"], timeout=45)
        if listing.ok:
            for row in listing.stdout.splitlines()[1:]:
                parts = row.split(",")
                if len(parts) >= 2 and needle in parts[1].lower():
                    shell.spawn(["legendary", "launch", parts[0]])
                    return f"Launching {parts[1]} via Legendary."

    return f"No installed game matches '{name}'. Run list_games to see what is available."


@skill(category="games", danger=Danger.MODERATE)
def update_games(_source: str = "local", _confirm: bool = False) -> str:
    """Trigger updates for installed games.

    Steam updates its own titles on launch, so this opens the downloads
    page; Legendary can be driven directly.
    """
    actions: list[str] = []

    root = _steam_root()
    if root is not None:
        apps.open_uri("steam://open/downloads")
        actions.append(
            "Opened Steam's Downloads page — Steam queues and applies its own updates there."
        )

    if shell.have("legendary"):
        result = shell.run(["legendary", "list-installed", "--check-updates", "--csv"], timeout=90)
        if result.ok:
            pending = [
                r for r in result.stdout.splitlines()[1:] if r.strip().endswith("True")
            ]
            if pending:
                actions.append(
                    f"Legendary reports {len(pending)} Epic game(s) with updates. "
                    "Apply them with:  legendary update <app_name>"
                )
            else:
                actions.append("Legendary reports all Epic games are up to date.")

    if not actions:
        return "No supported game launcher is installed."
    return "\n".join(actions)


@skill(category="games", danger=Danger.SAFE)
def game_storage() -> str:
    """Show how much disk space each Steam library is using."""
    root = _steam_root()
    if root is None:
        return "Steam is not installed."

    games = _installed_games(root)
    if not games:
        return "No Steam games installed."

    by_library: dict[str, list[dict[str, str]]] = {}
    for game in games:
        by_library.setdefault(game["library"], []).append(game)

    lines = []
    for library, entries in by_library.items():
        total = 0.0
        for game in entries:
            try:
                total += float(game["size"].split()[0])
            except (ValueError, IndexError):
                pass
        lines.append(f"{library}: {len(entries)} game(s), {total:.1f} GB")
        biggest = sorted(
            entries,
            key=lambda g: -float(g["size"].split()[0] or 0)
            if g["size"].split() and g["size"].split()[0].replace(".", "").isdigit() else 0,
        )[:5]
        for game in biggest:
            lines.append(f"    {game['size']:>9}  {game['name']}")
    return "\n".join(lines)
