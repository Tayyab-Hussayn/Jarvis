"""Runtime environment detection.

The original assumed "Windows 10/11". Linux has no single answer: the display
server, compositor, audio stack, init system and package manager all vary.
Skills query this module rather than hardcoding a desktop environment the way
``computer_settings.py`` hardcoded GNOME.
"""

from __future__ import annotations

import functools
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

from . import shell


@dataclass
class Environment:
    """Everything the skills need to know about the host."""

    distro: str
    distro_id: str
    kernel: str
    session_type: str          # wayland | x11 | tty
    desktop: str               # Hyprland, sway, GNOME, KDE, …
    compositor: str            # hyprland | sway | gnome | kde | x11 | unknown
    audio: str                 # pipewire | pulseaudio | alsa | none
    init: str                  # systemd | openrc | unknown
    package_manager: str       # pacman | apt | dnf | zypper | unknown
    aur_helper: str            # yay | paru | ""
    has_flatpak: bool
    tools: dict[str, bool] = field(default_factory=dict)

    @property
    def is_wayland(self) -> bool:
        return self.session_type == "wayland"

    @property
    def is_x11(self) -> bool:
        return self.session_type == "x11"

    @property
    def is_arch(self) -> bool:
        return self.package_manager == "pacman"

    def summary(self) -> str:
        return (
            f"{self.distro} · {self.kernel} · {self.desktop} "
            f"({self.session_type}) · {self.audio} · {self.package_manager}"
        )


def _read_os_release() -> dict[str, str]:
    data: dict[str, str] = {}
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        p = Path(path)
        if not p.exists():
            continue
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if "=" not in line or line.startswith("#"):
                    continue
                key, _, value = line.partition("=")
                data[key.strip()] = value.strip().strip('"').strip("'")
        except OSError:
            continue
        break
    return data


def _detect_session() -> str:
    session = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if session in ("wayland", "x11", "tty"):
        return session
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "tty"


def _detect_compositor(desktop: str) -> str:
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if os.environ.get("SWAYSOCK"):
        return "sway"

    lowered = desktop.lower()
    for token, name in (
        ("hyprland", "hyprland"),
        ("sway", "sway"),
        ("gnome", "gnome"),
        ("kde", "kde"),
        ("plasma", "kde"),
        ("xfce", "xfce"),
        ("river", "river"),
        ("niri", "niri"),
    ):
        if token in lowered:
            return name

    if _detect_session() == "x11":
        return "x11"
    return "unknown"


def _detect_audio() -> str:
    # wpctl only exists with WirePlumber, which means PipeWire.
    if shell.have("wpctl"):
        probe = shell.run(["wpctl", "status"], timeout=5)
        if probe.ok:
            return "pipewire"
    if shell.have("pactl"):
        probe = shell.run(["pactl", "info"], timeout=5)
        if probe.ok:
            if "PipeWire" in probe.stdout:
                return "pipewire"
            return "pulseaudio"
    if shell.have("amixer"):
        return "alsa"
    return "none"


def _detect_init() -> str:
    if Path("/run/systemd/system").is_dir():
        return "systemd"
    if shell.have("openrc"):
        return "openrc"
    return "unknown"


def _detect_package_manager() -> str:
    for binary, name in (
        ("pacman", "pacman"),
        ("apt-get", "apt"),
        ("dnf", "dnf"),
        ("zypper", "zypper"),
        ("xbps-install", "xbps"),
        ("emerge", "portage"),
    ):
        if shell.have(binary):
            return name
    return "unknown"


_INTERESTING_TOOLS = (
    "hyprctl", "swaymsg", "wmctrl", "xdotool", "ydotool", "wtype", "dotool",
    "grim", "slurp", "wl-copy", "wl-paste", "xclip", "xsel", "scrot", "maim",
    "grimblast", "gnome-screenshot", "spectacle",
    "wpctl", "pactl", "amixer", "playerctl", "brightnessctl", "light",
    "notify-send", "dunstify", "makoctl",
    "nmcli", "bluetoothctl", "systemctl", "loginctl", "journalctl",
    "pacman", "yay", "paru", "flatpak", "checkupdates",
    "gio", "gtk-launch", "xdg-open", "xdg-mime",
    "mpv", "ffplay", "paplay", "pw-play", "ffmpeg",
    "libreoffice", "tesseract", "steam", "steamcmd", "legendary", "heroic",
    "git", "docker", "podman", "lsof", "ss",
)


@functools.lru_cache(maxsize=1)
def environment() -> Environment:
    """Detect the host environment once per process."""
    os_release = _read_os_release()
    desktop = (
        os.environ.get("XDG_CURRENT_DESKTOP")
        or os.environ.get("DESKTOP_SESSION")
        or "none"
    )

    aur_helper = ""
    for helper in ("paru", "yay"):
        if shell.have(helper):
            aur_helper = helper
            break

    return Environment(
        distro=os_release.get("PRETTY_NAME") or os_release.get("NAME") or "Linux",
        distro_id=os_release.get("ID", "linux"),
        kernel=platform.release(),
        session_type=_detect_session(),
        desktop=desktop,
        compositor=_detect_compositor(desktop),
        audio=_detect_audio(),
        init=_detect_init(),
        package_manager=_detect_package_manager(),
        aur_helper=aur_helper,
        has_flatpak=shell.have("flatpak"),
        tools={name: shell.have(name) for name in _INTERESTING_TOOLS},
    )


def refresh() -> Environment:
    """Re-detect after installing new tools."""
    shell.clear_which_cache()
    environment.cache_clear()
    return environment()


def missing_report() -> list[tuple[str, str, str]]:
    """List capabilities that are degraded, with the package that fixes them.

    Returns (capability, missing tool, install hint) triples.
    """
    env = environment()
    report: list[tuple[str, str, str]] = []

    def need(capability: str, candidates: tuple[str, ...], package: str) -> None:
        if not any(env.tools.get(c) for c in candidates):
            report.append((capability, " or ".join(candidates), package))

    if env.is_wayland:
        need("screenshots", ("grim", "grimblast"), "grim")
        need("region select", ("slurp",), "slurp")
        need("clipboard", ("wl-copy",), "wl-clipboard")
        need("input synthesis", ("ydotool", "wtype", "dotool"), "ydotool")
        if env.compositor == "hyprland":
            need("window control", ("hyprctl",), "hyprland")
    else:
        need("screenshots", ("grim", "scrot", "maim"), "maim")
        need("clipboard", ("xclip", "xsel"), "xclip")
        need("input synthesis", ("xdotool", "ydotool"), "xdotool")
        need("window control", ("wmctrl", "xdotool"), "wmctrl")

    need("volume control", ("wpctl", "pactl", "amixer"), "wireplumber")
    need("brightness", ("brightnessctl", "light"), "brightnessctl")
    need("media keys", ("playerctl",), "playerctl")
    need("notifications", ("notify-send", "dunstify"), "libnotify")
    need("network control", ("nmcli",), "networkmanager")
    need("audio playback", ("mpv", "ffplay", "paplay", "pw-play"), "mpv")

    return report
