"""Screen capture.

Replaces ``mss`` and ``pyautogui.screenshot()``, neither of which can capture
a Wayland surface. Uses the compositor's own capture protocol:

  * Wayland — ``grim`` (+ ``slurp`` for region select), ``grimblast`` on Hyprland
  * X11     — ``maim`` / ``scrot`` / ``import``
  * Portal  — ``xdg-desktop-portal`` fallback via ``gdbus`` is not attempted
              here because it always shows a picker dialog.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

from .. import config
from . import detect, desktop, shell


class CaptureError(RuntimeError):
    """Raised when no capture backend can produce an image."""


def backend_name() -> str:
    """Which capture tool will be used, or '' if none."""
    env = detect.environment()
    if env.is_wayland:
        for tool in ("grimblast", "grim"):
            if shell.have(tool):
                return tool
        return ""
    for tool in ("maim", "scrot", "import", "gnome-screenshot", "spectacle"):
        if shell.have(tool):
            return tool
    return ""


def status() -> str:
    name = backend_name()
    if name:
        return f"Screen capture ready via {name}."
    if detect.environment().is_wayland:
        return "Screen capture unavailable. Install grim:  sudo pacman -S grim slurp"
    return "Screen capture unavailable. Install maim:  sudo pacman -S maim"


def _default_path() -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return config.CACHE_DIR / f"screenshot-{stamp}.png"


def capture(
    output: str | Path | None = None,
    *,
    region: bool = False,
    window: bool = False,
    monitor: str = "",
) -> Path:
    """Capture the screen to a PNG file and return its path.

    Args:
        output: Destination path. Defaults to the cache dir.
        region: Interactively select a region (needs slurp on Wayland).
        window: Capture only the currently focused window.
        monitor: Capture a specific output by name.
    """
    tool = backend_name()
    if not tool:
        raise CaptureError(status())

    config.ensure_dirs()
    path = Path(output).expanduser() if output else _default_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    env = detect.environment()

    if env.is_wayland:
        args = _wayland_args(tool, path, region, window, monitor)
    else:
        args = _x11_args(tool, path, region, window)

    # Region select waits on the user, so it needs a longer leash.
    result = shell.run(args, timeout=120 if region else 20)

    if not result.ok or not path.exists() or path.stat().st_size == 0:
        if region and ("selection" in result.stderr.lower() or result.returncode == 1):
            raise CaptureError("Region selection was cancelled.")
        raise CaptureError(f"capture failed via {tool}: {result.output}")

    return path


def _wayland_args(
    tool: str, path: Path, region: bool, window: bool, monitor: str
) -> list[str]:
    if tool == "grimblast":
        mode = "area" if region else ("active" if window else "screen")
        return ["grimblast", "save", mode, str(path)]

    args = ["grim"]

    if region:
        if not shell.have("slurp"):
            raise CaptureError("Region capture needs slurp:  sudo pacman -S slurp")
        selection = shell.run(["slurp"], timeout=120)
        if not selection.ok or not selection.stdout.strip():
            raise CaptureError("Region selection was cancelled.")
        args += ["-g", selection.stdout.strip()]

    elif window:
        focused = desktop.backend().focused()
        if focused and focused.geometry:
            x, y, w, h = focused.geometry
            if w > 0 and h > 0:
                args += ["-g", f"{x},{y} {w}x{h}"]

    elif monitor:
        args += ["-o", monitor]

    args.append(str(path))
    return args


def _x11_args(tool: str, path: Path, region: bool, window: bool) -> list[str]:
    if tool == "maim":
        args = ["maim"]
        if region:
            args.append("--select")
        elif window:
            args += ["--window", "$(xdotool getactivewindow)"] if shell.have("xdotool") else []
        args.append(str(path))
        return args

    if tool == "scrot":
        args = ["scrot"]
        if region:
            args.append("--select")
        elif window:
            args.append("--focused")
        args.append(str(path))
        return args

    if tool == "import":
        return ["import", "-window", "root", str(path)]

    if tool == "gnome-screenshot":
        args = ["gnome-screenshot", "-f", str(path)]
        if region:
            args.append("-a")
        elif window:
            args.append("-w")
        return args

    return ["spectacle", "-b", "-n", "-o", str(path)]


def capture_base64(
    *, region: bool = False, window: bool = False, max_width: int = 1280, quality: int = 70
) -> tuple[str, str]:
    """Capture and return (base64_data, mime_type), downscaled for the model.

    The original sent 640x360 JPEG at quality 55 to keep the Live API fast;
    defaults here are a little higher since text-mode vision benefits from
    legible UI text.
    """
    path = capture(region=region, window=window)
    try:
        try:
            from PIL import Image
        except ImportError:
            data = path.read_bytes()
            return base64.b64encode(data).decode(), "image/png"

        with Image.open(path) as img:
            img = img.convert("RGB")
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize(
                    (max_width, max(1, round(img.height * ratio))),
                    Image.Resampling.LANCZOS,
                )
            import io

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            return base64.b64encode(buf.getvalue()).decode(), "image/jpeg"
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def screen_size() -> tuple[int, int]:
    """Total logical screen size, for coordinate maths."""
    env = detect.environment()
    if env.compositor == "hyprland" and shell.have("hyprctl"):
        import json

        result = shell.run(["hyprctl", "-j", "monitors"], timeout=5)
        if result.ok:
            try:
                monitors = json.loads(result.stdout)
            except json.JSONDecodeError:
                monitors = []
            if monitors:
                width = max(
                    (m.get("x", 0) + m.get("width", 0)) for m in monitors
                )
                height = max(
                    (m.get("y", 0) + m.get("height", 0)) for m in monitors
                )
                return int(width), int(height)

    if shell.have("xrandr") and env.is_x11:
        import re

        result = shell.run(["xrandr"], timeout=5)
        match = re.search(r"current\s+(\d+)\s*x\s*(\d+)", result.stdout)
        if match:
            return int(match.group(1)), int(match.group(2))

    return 0, 0
