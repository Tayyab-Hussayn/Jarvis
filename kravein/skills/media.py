"""Media playback and wallpaper.

Ports ``actions/youtube_video.py`` (which shelled out to ``notepad.exe`` and
relied on a browser) and the wallpaper half of ``actions/desktop.py`` (which
called ``SystemParametersInfoW`` and read the registry).

Video plays through ``mpv`` + ``yt-dlp``, which streams without opening a
browser at all — better than the original behaviour, not just equivalent.
"""

from __future__ import annotations

import contextlib
import time
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from .. import config
from ..platform import audio, apps, detect, shell
from ..registry import skill
from ..safety import Danger, check_path, enforce

_VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".flv"}
_AUDIO_EXTS = {".mp3", ".flac", ".wav", ".ogg", ".opus", ".m4a", ".aac"}


@skill(category="media", danger=Danger.MODERATE)
def play_video(query: str, audio_only: bool = False, fullscreen: bool = False) -> str:
    """Play a video from YouTube, a URL, or a local file.

    A plain search phrase is resolved through YouTube search. Playback uses
    mpv, so nothing opens in a browser.

    Args:
        query: A search phrase, a URL, or a path to a local file.
        audio_only: Stream just the audio track.
        fullscreen: Start playback fullscreen.
    """
    query = (query or "").strip()
    if not query:
        return "What should I play?"

    if not shell.have("mpv"):
        return "mpv is not installed. Run:  sudo pacman -S mpv"

    args = ["mpv", "--force-window=yes"]
    if audio_only:
        args.append("--no-video")
    if fullscreen:
        args.append("--fullscreen")

    local = config.resolve_user_path(query)
    if local.exists() and local.is_file():
        shell.spawn([*args, str(local)])
        return f"Playing {local.name} in mpv."

    if query.startswith(("http://", "https://")):
        if not shell.have("yt-dlp") and "youtube" in query:
            return "Streaming YouTube needs yt-dlp. Run:  sudo pacman -S yt-dlp"
        shell.spawn([*args, query])
        return f"Streaming {query} in mpv."

    # A search phrase: let yt-dlp resolve it to the top YouTube result.
    if not shell.have("yt-dlp"):
        encoded = urllib.parse.quote_plus(query)
        return apps.open_uri(f"https://www.youtube.com/results?search_query={encoded}")

    shell.spawn([*args, f"ytdl://ytsearch1:{query}"])
    return f"Searching YouTube for '{query}' and playing the top result in mpv."


@skill(category="media", danger=Danger.SAFE)
def find_video(query: str, limit: int = 5) -> str:
    """Search YouTube and list the results without playing anything.

    Args:
        query: What to search for.
        limit: How many results to list.
    """
    if not shell.have("yt-dlp"):
        return "yt-dlp is not installed. Run:  sudo pacman -S yt-dlp"

    limit = max(1, min(int(limit), 20))
    result = shell.run(
        ["yt-dlp", f"ytsearch{limit}:{query}",
         "--flat-playlist", "--print", "%(title)s :: %(duration_string)s :: %(webpage_url)s",
         "--no-warnings"],
        timeout=90,
    )
    if not result.ok or not result.stdout.strip():
        return f"No results for '{query}'."

    lines = [f"Results for '{query}':"]
    for i, row in enumerate(result.stdout.strip().splitlines(), 1):
        parts = row.split(" :: ")
        if len(parts) >= 3:
            lines.append(f"  {i}. {parts[0]}  [{parts[1]}]\n     {parts[2]}")
        else:
            lines.append(f"  {i}. {row}")
    return "\n".join(lines)


@skill(category="media", danger=Danger.MODERATE)
def download_video(
    url: str,
    audio_only: bool = False,
    destination: str = "",
    _source: str = "local",
    _confirm: bool = False,
) -> str:
    """Download a video or its audio track with yt-dlp.

    Args:
        url: The video URL.
        audio_only: Extract audio to an mp3 instead of downloading video.
        destination: Where to save it. Defaults to your Videos or Music folder.
    """
    if not shell.have("yt-dlp"):
        return "yt-dlp is not installed. Run:  sudo pacman -S yt-dlp"

    dirs = config.user_dirs()
    target_dir = (
        config.resolve_user_path(destination) if destination
        else (dirs["music"] if audio_only else dirs["videos"])
    )
    enforce(check_path(target_dir, write=True), f"download into {target_dir}",
            skill="download_video", source=_source, pre_confirmed=_confirm)

    target_dir.mkdir(parents=True, exist_ok=True)

    args = ["yt-dlp", "--no-warnings", "-o", str(target_dir / "%(title)s.%(ext)s")]
    if audio_only:
        args += ["-x", "--audio-format", "mp3"]
    args.append(url)

    result = shell.run(args, timeout=900)
    if not result.ok:
        return f"Download failed: {result.stderr.strip()[:400]}"
    return f"Downloaded to {target_dir}.\n{result.stdout.strip()[-500:]}"


# ---------------------------------------------------------------------------
# Wallpaper
# ---------------------------------------------------------------------------

# Compositors whose wallpaper is drawn by a dedicated layer-shell client.
# On these, gsettings writes a key that nothing reads, so it is not a setter.
_STANDALONE_COMPOSITORS = {"hyprland", "sway", "river", "niri"}


def _wallpaper_setter() -> str:
    """Pick a wallpaper tool that will actually take effect on this session."""
    env = detect.environment()

    if env.is_wayland:
        for tool in ("swww", "hyprpaper", "swaybg", "wpaperd"):
            if shell.have(tool):
                return tool
        # A bare wlroots compositor has no desktop shell to fall back on.
        if env.compositor in _STANDALONE_COMPOSITORS:
            return ""

    if env.is_x11:
        for tool in ("feh", "nitrogen"):
            if shell.have(tool):
                return tool

    # GNOME, Cinnamon and friends genuinely honour this key.
    if shell.have("gsettings") and env.compositor not in _STANDALONE_COMPOSITORS:
        return "gsettings"
    return ""


@skill(category="media", danger=Danger.MODERATE)
def set_wallpaper(path: str, _source: str = "local", _confirm: bool = False) -> str:
    """Set the desktop wallpaper from an image file or URL.

    Args:
        path: Path to an image, or an http(s) URL to download first.
    """
    raw = (path or "").strip()
    if not raw:
        return "Give an image path or URL."

    temp: Path | None = None
    if raw.startswith(("http://", "https://")):
        suffix = Path(urllib.parse.urlparse(raw).path).suffix or ".jpg"
        temp = Path(tempfile.gettempdir()) / f"kravein-wallpaper{suffix}"
        try:
            with urllib.request.urlopen(raw, timeout=30) as response:
                temp.write_bytes(response.read())
        except Exception as exc:
            return f"Could not download {raw}: {exc}"
        image = temp
    else:
        image = config.resolve_user_path(raw)

    if not image.exists():
        return f"Image not found: {image}"
    if image.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        return f"Unsupported image format: {image.suffix}"

    tool = _wallpaper_setter()
    if not tool:
        env = detect.environment()
        suggestion = "swww" if env.is_wayland else "feh"
        return (
            "No wallpaper tool is installed.\n"
            f"On {env.desktop} install one with:  sudo pacman -S {suggestion}"
        )

    if tool == "swww":
        # swww needs its daemon; start it if it is not already up.
        if not shell.run(["swww", "query"], timeout=5).ok:
            shell.spawn(["swww-daemon"])
            import time

            time.sleep(1.0)
        result = shell.run(["swww", "img", str(image), "--transition-type", "fade"], timeout=20)

    elif tool == "hyprpaper":
        shell.run(["hyprctl", "hyprpaper", "preload", str(image)], timeout=15)
        result = shell.run(["hyprctl", "hyprpaper", "wallpaper", f",{image}"], timeout=15)

    elif tool == "swaybg":
        shell.run(["pkill", "-x", "swaybg"], timeout=8)
        shell.spawn(["swaybg", "-i", str(image), "-m", "fill"])
        result = shell.Result([], 0, "", "")

    elif tool == "feh":
        result = shell.run(["feh", "--bg-fill", str(image)], timeout=15)

    elif tool == "nitrogen":
        result = shell.run(["nitrogen", "--set-zoom-fill", "--save", str(image)], timeout=15)

    else:  # gsettings — GNOME and anything following its schema
        uri = image.resolve().as_uri()
        shell.run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri", uri], timeout=10)
        result = shell.run(
            ["gsettings", "set", "org.gnome.desktop.background", "picture-uri-dark", uri],
            timeout=10,
        )

    # Keep a stable copy so the wallpaper survives the temp file being cleaned.
    if temp is not None:
        kept = config.DATA_DIR / f"wallpaper{temp.suffix}"
        try:
            config.ensure_dirs()
            shutil.copy2(temp, kept)
            temp.unlink(missing_ok=True)
        except OSError:
            pass

    if not result.ok:
        return f"Could not set the wallpaper via {tool}: {result.output}"
    return f"Wallpaper set via {tool}: {image.name}"


@skill(category="media", danger=Danger.SAFE)
def wallpaper_status() -> str:
    """Report which wallpaper tool is available on this session."""
    tool = _wallpaper_setter()
    env = detect.environment()
    if not tool:
        suggestion = "swww" if env.is_wayland else "feh"
        return (
            f"No wallpaper tool installed for {env.desktop} ({env.session_type}).\n"
            f"Install one with:  sudo pacman -S {suggestion}"
        )
    return f"Wallpaper tool: {tool} (session: {env.session_type}, desktop: {env.desktop})"


# ---------------------------------------------------------------------------
# Spotify — over MPRIS, not keystrokes
# ---------------------------------------------------------------------------
#
# The Windows build focused Spotify's window and typed at it, which fails
# whenever the window is slow, unfocused or laid out differently. MPRIS exposes
# OpenUri, so navigating to a playlist is one D-Bus call with nothing to aim at
# and nothing to verify visually.

# Well-known Spotify URIs. "Liked songs" has no playlist id — it is a
# collection, which is why searching for a playlist by that name never worked.
_SPOTIFY_SHORTCUTS = {
    "liked songs": "spotify:collection:tracks",
    "liked": "spotify:collection:tracks",
    "my liked songs": "spotify:collection:tracks",
    "saved songs": "spotify:collection:tracks",
    "favourites": "spotify:collection:tracks",
    "favorites": "spotify:collection:tracks",
    # The rest of Spotify's own views, reachable as URIs rather than by
    # clicking the sidebar.
    "home": "spotify:home",
    "search": "spotify:search",
    "queue": "spotify:queue",
    "library": "spotify:collection",
    "your library": "spotify:collection",
    "collection": "spotify:collection",
    "albums": "spotify:collection:albums",
    "saved albums": "spotify:collection:albums",
    "artists": "spotify:collection:artists",
    "podcasts": "spotify:collection:podcasts",
    "playlists": "spotify:collection:playlists",
}


_SPOTIFY_LABELS = {
    "spotify:collection:tracks": "your liked songs",
    "spotify:collection": "your library",
    "spotify:collection:albums": "your saved albums",
    "spotify:collection:artists": "your artists",
    "spotify:collection:playlists": "your playlists",
    "spotify:home": "Spotify home",
    "spotify:queue": "the queue",
}


def _spotify_uri(what: str) -> str:
    """Turn a plain request into a Spotify URI."""
    text = (what or "").strip()
    if not text:
        return "spotify:collection:tracks"
    if text.lower().startswith("spotify:"):
        return text
    shortcut = _SPOTIFY_SHORTCUTS.get(text.lower())
    if shortcut:
        return shortcut
    # Anything else is a search. Spotify opens the search view for this.
    return "spotify:search:" + urllib.parse.quote(text)


def _wait_for_player(prefix: str, seconds: float = 15.0) -> str:
    """Wait for a player to appear on the bus after launching it."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        name = audio.find_player(prefix)
        if name:
            return name
        time.sleep(0.4)
    return ""


@skill(category="media", danger=Danger.MODERATE)
def spotify_play(what: str = "liked songs") -> str:
    """Open something in Spotify and play it.

    Launches Spotify first if it is not already running.

    Args:
        what: "liked songs", a search phrase like "lo-fi beats", or a raw
            spotify: URI such as spotify:playlist:37i9dQZF1DXcBWIGoYBM5M.
    """
    uri = _spotify_uri(what)

    player = audio.find_player("spotify")
    if not player:
        launched = apps.launch_by_name("spotify")
        if "No" in launched and "match" in launched:
            return "Spotify does not appear to be installed."
        player = _wait_for_player("spotify")
        if not player:
            return (
                "Spotify was launched but never appeared on the D-Bus session "
                "bus, so it cannot be controlled. Is it finished starting?"
            )

    try:
        audio.open_uri(uri, player=player)
    except RuntimeError as exc:
        return f"Could not open {uri}: {exc}"

    # OpenUri navigates; whether it also starts playback depends on the URI.
    # A collection or playlist plays; a search only shows results, because
    # Spotify does not know which result you meant.
    navigational = uri.startswith("spotify:search") or uri in {
        "spotify:home", "spotify:collection", "spotify:queue",
    }
    if not navigational:
        with contextlib.suppress(Exception):
            time.sleep(0.6)
            shell.run(["playerctl", "-p", player, "play"], timeout=5)

    label = _SPOTIFY_LABELS.get(uri, uri)
    if navigational:
        if uri.startswith("spotify:search:"):
            return (
                f"Searched Spotify for {what!r}. Results are on screen — say "
                "which one to play, or use spotify_play with its spotify: URI."
            )
        return f"Opened {label} in Spotify."

    with contextlib.suppress(Exception):
        time.sleep(0.8)
        playing = audio.metadata(player, "{{artist}} — {{title}}")
        if playing:
            return f"Playing {playing} from {label}."
    return f"Opened {label} in Spotify."


@skill(category="media", danger=Danger.SAFE)
def spotify_status() -> str:
    """What Spotify is playing right now."""
    player = audio.find_player("spotify")
    if not player:
        return "Spotify is not running."
    line = audio.metadata(
        player, "{{artist}} — {{title}} ({{album}}) [{{status}}]"
    )
    return line or "Spotify is running but nothing is playing."
