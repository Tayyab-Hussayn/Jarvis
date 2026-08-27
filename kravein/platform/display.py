"""Screen brightness, idle inhibition and monitor state.

Replaces the WMI/PowerShell brightness calls and the ``xset dpms`` shell
pipeline the original used. ``brightnessctl`` is the portable choice; the
compositor is asked directly for monitor state where it exposes it.
"""

from __future__ import annotations

import json
import re

from . import detect, shell


def _backend() -> str:
    if shell.have("brightnessctl"):
        return "brightnessctl"
    if shell.have("light"):
        return "light"
    return ""


def _require() -> str:
    backend = _backend()
    if not backend:
        raise RuntimeError(
            "No brightness control available. Run: sudo pacman -S brightnessctl"
        )
    return backend


def get_brightness() -> int:
    """Current backlight brightness as a percentage."""
    backend = _require()
    if backend == "brightnessctl":
        result = shell.run(["brightnessctl", "-m", "info"], timeout=5)
        if result.ok:
            # machine-readable: name,type,current,percent%,max
            parts = result.stdout.strip().split(",")
            if len(parts) >= 4:
                match = re.search(r"(\d+)", parts[3])
                if match:
                    return int(match.group(1))
    else:
        result = shell.run(["light", "-G"], timeout=5)
        if result.ok:
            try:
                return round(float(result.stdout.strip()))
            except ValueError:
                pass
    return -1


def set_brightness(percent: int) -> str:
    """Set backlight brightness (clamped to 5-100 so the screen never blacks out)."""
    backend = _require()
    percent = max(5, min(100, int(percent)))

    if backend == "brightnessctl":
        result = shell.run(["brightnessctl", "set", f"{percent}%"], timeout=6)
    else:
        result = shell.run(["light", "-S", str(percent)], timeout=6)

    if not result.ok:
        return _permission_hint(result.output)
    return f"Brightness set to {percent}%."


def change_brightness(delta: int) -> str:
    """Nudge brightness by a signed percentage."""
    backend = _require()
    delta = int(delta)
    magnitude = abs(delta)

    if backend == "brightnessctl":
        arg = f"{magnitude}%+" if delta >= 0 else f"{magnitude}%-"
        result = shell.run(["brightnessctl", "set", arg], timeout=6)
    else:
        arg = ["-A", str(magnitude)] if delta >= 0 else ["-U", str(magnitude)]
        result = shell.run(["light", *arg], timeout=6)

    if not result.ok:
        return _permission_hint(result.output)
    current = get_brightness()
    return f"Brightness {'up' if delta >= 0 else 'down'} → {current}%."


def _permission_hint(error: str) -> str:
    """brightnessctl needs the user in the 'video' group to write sysfs."""
    if "permission" in error.lower() or "denied" in error.lower():
        return (
            "Could not change brightness: permission denied on the backlight device.\n"
            "Fix it with:  sudo usermod -aG video $USER   (then log out and back in)"
        )
    return f"Could not change brightness: {error}"


def _hypr_dpms(state: str) -> bool:
    """Set DPMS on Hyprland, across the 0.56 dispatcher rewrite.

    See :class:`kravein.platform.desktop.HyprlandBackend` — 0.56 replaced the
    shell-style dispatcher with a Lua API, so both spellings are tried.
    """
    from . import desktop

    backend = desktop.backend()
    if isinstance(backend, desktop.HyprlandBackend):
        return backend._dispatch(
            f'return hl.dispatch(hl.dsp.dpms("{state}"))', "dpms", state
        )
    return shell.run(["hyprctl", "dispatch", "dpms", state], timeout=5).ok


def blank_screen() -> str:
    """Turn the displays off. The Linux answer to Windows' SC_MONITORPOWER."""
    env = detect.environment()

    if env.compositor == "hyprland" and shell.have("hyprctl"):
        if _hypr_dpms("off"):
            return "Displays turned off. Move the mouse or press a key to wake."

    if env.compositor == "sway" and shell.have("swaymsg"):
        result = shell.run(["swaymsg", "output", "*", "dpms", "off"], timeout=5)
        if result.ok:
            return "Displays turned off."

    if shell.have("xset") and env.is_x11:
        result = shell.run(["xset", "dpms", "force", "off"], timeout=5)
        if result.ok:
            return "Displays turned off."

    if shell.have("loginctl"):
        shell.run(["loginctl", "lock-session"], timeout=5)
        return "Could not blank the display directly; locked the session instead."

    return "No supported way to blank the display on this session."


def wake_screen() -> str:
    """Turn the displays back on."""
    env = detect.environment()
    if env.compositor == "hyprland" and shell.have("hyprctl"):
        if _hypr_dpms("on"):
            return "Displays turned on."
    if env.compositor == "sway" and shell.have("swaymsg"):
        if shell.run(["swaymsg", "output", "*", "dpms", "on"], timeout=5).ok:
            return "Displays turned on."
    if shell.have("xset") and env.is_x11:
        if shell.run(["xset", "dpms", "force", "on"], timeout=5).ok:
            return "Displays turned on."
    return "Could not wake the displays."


def list_monitors() -> str:
    """Describe connected monitors."""
    env = detect.environment()

    if env.compositor == "hyprland" and shell.have("hyprctl"):
        result = shell.run(["hyprctl", "-j", "monitors"], timeout=6)
        if result.ok:
            try:
                monitors = json.loads(result.stdout)
            except json.JSONDecodeError:
                monitors = []
            if monitors:
                lines = []
                for mon in monitors:
                    lines.append(
                        f"  {mon.get('name')}: {mon.get('width')}x{mon.get('height')}"
                        f"@{round(mon.get('refreshRate', 0))}Hz"
                        f" scale {mon.get('scale')}"
                        f"{' (focused)' if mon.get('focused') else ''}"
                    )
                return f"{len(monitors)} monitor(s):\n" + "\n".join(lines)

    if shell.have("wlr-randr"):
        result = shell.run(["wlr-randr"], timeout=6)
        if result.ok:
            return result.stdout.strip()

    if shell.have("xrandr") and env.is_x11:
        result = shell.run(["xrandr", "--query"], timeout=6)
        if result.ok:
            lines = [l for l in result.stdout.splitlines() if " connected" in l]
            return "\n".join(lines) or result.stdout.strip()

    return "Could not enumerate monitors on this session."


def set_dark_mode(enabled: bool | None = None) -> str:
    """Toggle the desktop colour scheme.

    Replaces the Windows registry ``AppsUseLightTheme`` flip. On Linux the
    portable setting is the freedesktop appearance portal, which GTK, Qt and
    most toolkits follow.
    """
    if not shell.have("gsettings"):
        return "gsettings is not available; cannot change the colour scheme."

    current = shell.run(
        ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"], timeout=5
    )
    is_dark = "dark" in (current.stdout or "").lower()
    target = (not is_dark) if enabled is None else bool(enabled)
    value = "prefer-dark" if target else "default"

    result = shell.run(
        ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", value],
        timeout=5,
    )
    if not result.ok:
        return f"Could not set colour scheme: {result.output}"

    # Keep the legacy GTK theme key in sync for apps that ignore color-scheme.
    theme = shell.run(
        ["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"], timeout=5
    )
    name = (theme.stdout or "").strip().strip("'")
    if name:
        base = name.removesuffix("-dark").removesuffix("-Dark")
        wanted = f"{base}-Dark" if target else base
        shell.run(
            ["gsettings", "set", "org.gnome.desktop.interface", "gtk-theme", wanted],
            timeout=5,
        )

    return f"Colour scheme set to {'dark' if target else 'light'}."
