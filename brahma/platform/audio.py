"""Volume, mute and media transport.

Replaces ``pycaw``/``comtypes`` (Windows Core Audio) and the volume-key
presses the original sent through pyautogui. Prefers WirePlumber's ``wpctl``,
falls back to ``pactl`` then ALSA ``amixer``.
"""

from __future__ import annotations

import re

from . import detect, shell

SINK = "@DEFAULT_AUDIO_SINK@"
SOURCE = "@DEFAULT_AUDIO_SOURCE@"


def _backend() -> str:
    env = detect.environment()
    if shell.have("wpctl") and env.audio == "pipewire":
        return "wpctl"
    if shell.have("pactl"):
        return "pactl"
    if shell.have("amixer"):
        return "amixer"
    return ""


def _require() -> str:
    backend = _backend()
    if not backend:
        raise RuntimeError(
            "No audio control available. Install wireplumber or pulseaudio-utils."
        )
    return backend


def get_volume() -> tuple[int, bool]:
    """Return (volume_percent, muted)."""
    backend = _require()

    if backend == "wpctl":
        result = shell.run(["wpctl", "get-volume", SINK], timeout=5)
        if result.ok:
            # "Volume: 0.65" or "Volume: 0.65 [MUTED]"
            match = re.search(r"Volume:\s*([0-9.]+)", result.stdout)
            volume = round(float(match.group(1)) * 100) if match else 0
            return volume, "MUTED" in result.stdout.upper()

    elif backend == "pactl":
        vol_result = shell.run(["pactl", "get-sink-volume", SINK], timeout=5)
        mute_result = shell.run(["pactl", "get-sink-mute", SINK], timeout=5)
        volume = 0
        if vol_result.ok:
            match = re.search(r"(\d+)%", vol_result.stdout)
            volume = int(match.group(1)) if match else 0
        muted = "yes" in mute_result.stdout.lower()
        return volume, muted

    else:
        result = shell.run(["amixer", "get", "Master"], timeout=5)
        if result.ok:
            match = re.search(r"\[(\d+)%\]", result.stdout)
            volume = int(match.group(1)) if match else 0
            return volume, "[off]" in result.stdout
    return 0, False


def set_volume(percent: int) -> str:
    """Set the output volume, clamped to 0-100."""
    backend = _require()
    percent = max(0, min(100, int(percent)))

    if backend == "wpctl":
        result = shell.run(["wpctl", "set-volume", SINK, f"{percent / 100:.2f}"], timeout=5)
    elif backend == "pactl":
        result = shell.run(["pactl", "set-sink-volume", SINK, f"{percent}%"], timeout=5)
    else:
        result = shell.run(["amixer", "-q", "set", "Master", f"{percent}%"], timeout=5)

    if not result.ok:
        raise RuntimeError(f"could not set volume: {result.output}")
    return f"Volume set to {percent}%."


def change_volume(delta: int) -> str:
    """Nudge the volume by a signed percentage."""
    backend = _require()
    delta = max(-100, min(100, int(delta)))
    sign = "+" if delta >= 0 else "-"
    magnitude = abs(delta)

    if backend == "wpctl":
        # wpctl clamps at 1.0 with --limit so a nudge can never blow past 100%.
        result = shell.run(
            ["wpctl", "set-volume", "--limit", "1.0", SINK, f"{magnitude / 100:.2f}{sign}"],
            timeout=5,
        )
    elif backend == "pactl":
        result = shell.run(["pactl", "set-sink-volume", SINK, f"{sign}{magnitude}%"], timeout=5)
    else:
        result = shell.run(
            ["amixer", "-q", "set", "Master", f"{magnitude}%{'+' if delta >= 0 else '-'}"],
            timeout=5,
        )

    if not result.ok:
        raise RuntimeError(f"could not change volume: {result.output}")
    volume, muted = get_volume()
    return f"Volume {'up' if delta >= 0 else 'down'} → {volume}%{' (muted)' if muted else ''}."


def set_mute(state: bool | None = None) -> str:
    """Mute, unmute, or toggle when ``state`` is None."""
    backend = _require()
    arg = "toggle" if state is None else ("1" if state else "0")

    if backend == "wpctl":
        result = shell.run(["wpctl", "set-mute", SINK, arg], timeout=5)
    elif backend == "pactl":
        result = shell.run(["pactl", "set-sink-mute", SINK, arg], timeout=5)
    else:
        result = shell.run(
            ["amixer", "-q", "set", "Master", "toggle" if state is None else ("mute" if state else "unmute")],
            timeout=5,
        )

    if not result.ok:
        raise RuntimeError(f"could not change mute: {result.output}")
    _, muted = get_volume()
    return "Audio muted." if muted else "Audio unmuted."


def set_mic_mute(state: bool | None = None) -> str:
    """Mute, unmute or toggle the default input source."""
    backend = _require()
    arg = "toggle" if state is None else ("1" if state else "0")
    if backend == "wpctl":
        result = shell.run(["wpctl", "set-mute", SOURCE, arg], timeout=5)
    elif backend == "pactl":
        result = shell.run(["pactl", "set-source-mute", SOURCE, arg], timeout=5)
    else:
        result = shell.run(["amixer", "-q", "set", "Capture", "toggle"], timeout=5)
    if not result.ok:
        raise RuntimeError(f"could not change mic mute: {result.output}")
    return "Microphone toggled."


def list_sinks() -> str:
    """List output devices."""
    if shell.have("wpctl"):
        result = shell.run(["wpctl", "status"], timeout=6)
        if result.ok:
            return result.stdout
    if shell.have("pactl"):
        result = shell.run(["pactl", "list", "short", "sinks"], timeout=6)
        if result.ok:
            return result.stdout.strip() or "No sinks found."
    return "No audio backend available."


# ---------------------------------------------------------------------------
# Media transport (playerctl / MPRIS)
# ---------------------------------------------------------------------------

def _require_playerctl() -> None:
    if not shell.have("playerctl"):
        raise RuntimeError("playerctl is not installed. Run: sudo pacman -S playerctl")


def media(command: str) -> str:
    """Send an MPRIS transport command: play-pause, next, previous, stop."""
    _require_playerctl()
    valid = {
        "play": "play", "pause": "pause", "play_pause": "play-pause",
        "playpause": "play-pause", "toggle": "play-pause",
        "next": "next", "previous": "previous", "prev": "previous",
        "stop": "stop",
    }
    action = valid.get((command or "").lower().replace("-", "_"))
    if action is None:
        raise ValueError(f"unknown media command: {command}")

    result = shell.run(["playerctl", action], timeout=5)
    if not result.ok:
        if "No players found" in result.stderr:
            return "No media player is running."
        raise RuntimeError(f"media command failed: {result.output}")
    return f"Media: {action}."


def now_playing() -> str:
    """What is currently playing, via MPRIS."""
    if not shell.have("playerctl"):
        return "playerctl is not installed."
    result = shell.run(
        ["playerctl", "metadata", "--format",
         "{{playerName}}: {{artist}} — {{title}} [{{status}}]"],
        timeout=5,
    )
    if not result.ok:
        return "Nothing is playing."
    return result.stdout.strip() or "Nothing is playing."
