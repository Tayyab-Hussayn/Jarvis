"""Keyboard and mouse synthesis.

Replaces ``pyautogui``, which is X11/Win32-only and does nothing useful under
a Wayland compositor. Backends, in preference order:

  * ``ydotool`` — uinput based, works on any compositor (needs ``ydotoold``)
  * ``wtype``   — Wayland virtual-keyboard protocol (typing only, no mouse)
  * ``xdotool`` — X11 / XWayland

Key names use the pyautogui vocabulary the original skills already spoke, so
call sites did not have to change; :data:`_KEYMAP` translates them to Linux
input-event names.
"""

from __future__ import annotations

import time
from typing import Iterable

from . import detect, shell

# pyautogui-style name -> Linux KEY_* name (without the KEY_ prefix)
# Linux input event codes, from <linux/input-event-codes.h>.
#
# These MUST be numeric. ydotool accepts `28:1` but silently ignores `ENTER:1`
# and `KEY_ENTER:1` — and exits 0 either way, so the failure is invisible. The
# port shipped symbolic names for months, which meant every press_keys call and
# every hotkey did nothing at all while reporting success.
_KEYMAP = {
    "esc": "1", "escape": "1",
    "minus": "12", "equal": "13", "plus": "13",
    "backspace": "14", "tab": "15",
    "bracketleft": "26", "bracketright": "27",
    "enter": "28", "return": "28",
    "ctrl": "29", "control": "29", "ctrlleft": "29",
    "semicolon": "39", "apostrophe": "40", "quote": "40", "grave": "41",
    "shift": "42", "shiftleft": "42",
    "backslash": "43",
    "comma": "51", "period": "52", "dot": "52", "slash": "53",
    "shiftright": "54",
    "printscreen": "99", "print_screen": "99",
    "alt": "56", "altleft": "56",
    "space": "57", "capslock": "58",
    "up": "103", "down": "108", "left": "105", "right": "106",
    "home": "102", "end": "107", "pageup": "104", "pagedown": "109",
    "insert": "110", "delete": "111", "del": "111",
    "ctrlright": "97", "altright": "100",
    "win": "125", "super": "125", "meta": "125",
    "cmd": "125", "command": "125",
    "volumeup": "115", "volumedown": "114", "volumemute": "113",
    "playpause": "164", "nexttrack": "163", "prevtrack": "165",
}
# F1–F10 are 59–68; F11 and F12 jump to 87 and 88.
for _i, _code in enumerate(range(59, 69), start=1):
    _KEYMAP[f"f{_i}"] = str(_code)
_KEYMAP["f11"], _KEYMAP["f12"] = "87", "88"

# Letters and digits, in scancode order rather than alphabetical.
for _c, _code in zip("qwertyuiop", range(16, 26)):
    _KEYMAP[_c] = str(_code)
for _c, _code in zip("asdfghjkl", range(30, 39)):
    _KEYMAP[_c] = str(_code)
for _c, _code in zip("zxcvbnm", range(44, 51)):
    _KEYMAP[_c] = str(_code)
for _d, _code in zip("1234567890", list(range(2, 11)) + [11]):
    _KEYMAP[_d] = str(_code)

# X11 keysym names for the xdotool backend.
_X11_KEYMAP = {
    "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift",
    "win": "super", "super": "super", "meta": "super", "cmd": "super",
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
    "tab": "Tab", "space": "space", "backspace": "BackSpace",
    "delete": "Delete", "del": "Delete", "up": "Up", "down": "Down",
    "left": "Left", "right": "Right", "home": "Home", "end": "End",
    "pageup": "Prior", "pagedown": "Next", "equal": "equal", "minus": "minus",
}

MOUSE_BUTTONS = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}
_X11_BUTTONS = {"left": "1", "middle": "2", "right": "3"}


class InputError(RuntimeError):
    """Raised when no usable input backend is present."""


def _ydotool_socket() -> str:
    """Locate the ydotoold socket.

    ydotool checks $YDOTOOL_SOCKET, then $XDG_RUNTIME_DIR/.ydotool_socket,
    then /tmp/.ydotool_socket. The daemon must own a live socket or every
    call fails.
    """
    import os
    from pathlib import Path

    candidates = []
    explicit = os.environ.get("YDOTOOL_SOCKET", "").strip()
    if explicit:
        candidates.append(Path(explicit))
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime:
        candidates.append(Path(runtime) / ".ydotool_socket")
    candidates.append(Path("/tmp/.ydotool_socket"))

    for path in candidates:
        try:
            if path.is_socket():
                return str(path)
        except OSError:
            continue
    return ""


def _ydotool_ready() -> tuple[bool, str]:
    """Check ydotool is installed *and* its daemon socket is live."""
    if not shell.have("ydotool"):
        return False, "ydotool is not installed"
    if not _ydotool_socket():
        return False, "ydotoold is not running"
    return True, ""


def backend_name() -> str:
    """Which input backend will be used, or '' if none work."""
    ok, _ = _ydotool_ready()
    if ok:
        return "ydotool"
    if shell.have("wtype") and detect.environment().is_wayland:
        return "wtype"
    if shell.have("xdotool"):
        return "xdotool"
    if shell.have("dotool"):
        return "dotool"
    return ""


def status() -> str:
    """Human-readable readiness report, with the fix if it is not ready."""
    name = backend_name()
    if name:
        return f"Input synthesis ready via {name}."

    env = detect.environment()
    if env.is_wayland:
        ok, why = _ydotool_ready()
        if not ok and "not running" in why:
            return (
                "Input synthesis unavailable: ydotoold is not running.\n"
                "Start it with:  systemctl --user enable --now ydotool.service\n"
                "(some builds ship it as a system unit: "
                "sudo systemctl enable --now ydotoold.service)"
            )
        return (
            "Input synthesis unavailable on Wayland. Install ydotool:\n"
            "  sudo pacman -S ydotool && sudo systemctl enable --now ydotoold"
        )
    return "Input synthesis unavailable. Install xdotool:  sudo pacman -S xdotool"


def _require() -> str:
    name = backend_name()
    if not name:
        raise InputError(status())
    return name


def _map_key(key: str, backend: str) -> str:
    key = (key or "").strip().lower()
    if backend == "xdotool":
        return _X11_KEYMAP.get(key, key)
    mapped = _KEYMAP.get(key)
    if mapped is None:
        raise InputError(f"unknown key name: '{key}'")
    return mapped


def type_text(text: str, delay_ms: int = 12) -> str:
    """Type a literal string at the focused surface."""
    if not text:
        return "Nothing to type."
    backend = _require()

    if backend == "ydotool":
        result = shell.run(
            ["ydotool", "type", "--key-delay", str(max(1, delay_ms)), "--", text],
            timeout=max(15, len(text) * delay_ms / 1000 + 10),
        )
    elif backend == "wtype":
        result = shell.run(["wtype", "--", text], timeout=30)
    elif backend == "dotool":
        result = shell.run(["dotool"], input_text=f"type {text}\n", timeout=30)
    else:
        result = shell.run(
            ["xdotool", "type", "--delay", str(max(1, delay_ms)), "--", text],
            timeout=30,
        )

    if not result.ok:
        raise InputError(f"typing failed via {backend}: {result.output}")
    preview = text if len(text) <= 60 else text[:60] + "…"
    return f"Typed: {preview}"


def press(keys: str | Iterable[str]) -> str:
    """Press a key or a combination.

    Accepts 'enter', 'ctrl+c', or ['ctrl', 'c'].
    """
    backend = _require()
    if isinstance(keys, str):
        parts = [k for k in keys.replace(" ", "").split("+") if k]
    else:
        parts = [str(k) for k in keys]
    if not parts:
        return "No keys given."

    if backend == "ydotool":
        mapped = [_map_key(k, backend) for k in parts]
        # ydotool key takes press (:1) then release (:0) events.
        sequence = [f"{k}:1" for k in mapped] + [f"{k}:0" for k in reversed(mapped)]
        result = shell.run(["ydotool", "key", *sequence], timeout=10)
    elif backend == "xdotool":
        combo = "+".join(_map_key(k, backend) for k in parts)
        result = shell.run(["xdotool", "key", combo], timeout=10)
    elif backend == "wtype":
        args: list[str] = []
        for k in parts[:-1]:
            args += ["-M", _X11_KEYMAP.get(k.lower(), k.lower())]
        args += ["-k", _X11_KEYMAP.get(parts[-1].lower(), parts[-1].lower())]
        for k in parts[:-1]:
            args += ["-m", _X11_KEYMAP.get(k.lower(), k.lower())]
        result = shell.run(["wtype", *args], timeout=10)
    else:
        result = shell.run(["dotool"], input_text=f"key {'+'.join(parts)}\n", timeout=10)

    if not result.ok:
        raise InputError(f"key press failed via {backend}: {result.output}")
    return f"Pressed: {'+'.join(parts)}"


def click(button: str = "left", count: int = 1) -> str:
    """Click at the current pointer position."""
    backend = _require()
    button = (button or "left").lower()
    count = max(1, min(int(count), 5))

    if backend == "ydotool":
        code = MOUSE_BUTTONS.get(button)
        if code is None:
            raise InputError(f"unknown mouse button: {button}")
        for _ in range(count):
            result = shell.run(["ydotool", "click", code], timeout=8)
            if not result.ok:
                raise InputError(f"click failed: {result.output}")
            time.sleep(0.06)
    elif backend == "xdotool":
        code = _X11_BUTTONS.get(button, "1")
        result = shell.run(
            ["xdotool", "click", "--repeat", str(count), code], timeout=8
        )
        if not result.ok:
            raise InputError(f"click failed: {result.output}")
    else:
        raise InputError(f"{backend} cannot synthesise mouse input")

    return f"Clicked {button}{f' ×{count}' if count > 1 else ''}."


def move_mouse(x: int, y: int, absolute: bool = True) -> str:
    """Move the pointer.

    Absolute positioning needs ydotool (Wayland gives no global cursor API
    to unprivileged clients) or xdotool on X11.
    """
    backend = _require()
    if backend == "ydotool":
        args = ["ydotool", "mousemove"]
        if absolute:
            args.append("--absolute")
        args += ["-x", str(int(x)), "-y", str(int(y))]
        result = shell.run(args, timeout=8)
    elif backend == "xdotool":
        args = ["xdotool", "mousemove"]
        if not absolute:
            args.append("--relative")
        args += ["--", str(int(x)), str(int(y))]
        result = shell.run(args, timeout=8)
    else:
        raise InputError(f"{backend} cannot move the pointer")

    if not result.ok:
        raise InputError(f"mouse move failed: {result.output}")
    kind = "to" if absolute else "by"
    return f"Moved pointer {kind} ({x}, {y})."


def scroll(direction: str = "down", amount: int = 3) -> str:
    """Scroll the wheel."""
    backend = _require()
    direction = (direction or "down").lower()
    amount = max(1, min(int(amount), 25))

    if backend == "ydotool":
        # ydotool mousemove --wheel uses y for vertical, x for horizontal.
        dx, dy = 0, 0
        if direction == "down":
            dy = -amount
        elif direction == "up":
            dy = amount
        elif direction == "left":
            dx = -amount
        elif direction == "right":
            dx = amount
        else:
            raise InputError(f"unknown scroll direction: {direction}")
        result = shell.run(
            ["ydotool", "mousemove", "--wheel", "-x", str(dx), "-y", str(dy)],
            timeout=8,
        )
    elif backend == "xdotool":
        code = {"up": "4", "down": "5", "left": "6", "right": "7"}.get(direction)
        if code is None:
            raise InputError(f"unknown scroll direction: {direction}")
        result = shell.run(["xdotool", "click", "--repeat", str(amount), code], timeout=8)
    else:
        raise InputError(f"{backend} cannot synthesise scrolling")

    if not result.ok:
        raise InputError(f"scroll failed: {result.output}")
    return f"Scrolled {direction} ×{amount}."
