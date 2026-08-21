"""Linux platform adapters.

Each module wraps one OS concern behind a small, stable interface so the
skills never call an external binary directly. This is the layer that
replaced the Windows-specific mechanisms:

    apps       .desktop index + launching   (was: Start-menu keystroke hack)
    audio      wpctl / pactl                (was: pycaw + comtypes)
    clipboard  wl-clipboard / xclip         (was: pyperclip)
    desktop    hyprctl / swaymsg / wmctrl   (was: pygetwindow + pywinauto)
    display    brightnessctl, DPMS          (was: WMI brightness via PowerShell)
    inputs     ydotool / wtype / xdotool    (was: pyautogui)
    notify     libnotify + D-Bus watching   (was: win10toast + wpndatabase.db)
    packages   pacman / AUR / flatpak       (was: nothing)
    power      systemctl / loginctl         (was: shutdown.exe, Task Scheduler)
    procs      psutil + /proc               (was: tasklist / taskkill)
    screen     grim / slurp / maim          (was: mss + pyautogui.screenshot)
    shell      audited subprocess wrapper   (was: scattered subprocess.run)
    tts        edge-tts + mpv               (was: edge-tts + winmm MCI)
"""

from . import (  # noqa: F401
    apps,
    audio,
    clipboard,
    desktop,
    detect,
    display,
    inputs,
    notify,
    packages,
    power,
    screen,
    shell,
    tts,
)

__all__ = [
    "apps", "audio", "clipboard", "desktop", "detect", "display",
    "inputs", "notify", "packages", "power", "screen", "shell", "tts",
]
