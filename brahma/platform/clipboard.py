"""Clipboard access.

Replaces ``pyperclip``, which needs an X11 helper and silently no-ops on a
pure Wayland session. Uses ``wl-clipboard`` on Wayland and ``xclip``/``xsel``
on X11.
"""

from __future__ import annotations

from . import detect, shell


def backend_name() -> str:
    env = detect.environment()
    if env.is_wayland and shell.have("wl-copy") and shell.have("wl-paste"):
        return "wl-clipboard"
    if shell.have("xclip"):
        return "xclip"
    if shell.have("xsel"):
        return "xsel"
    # wl-clipboard also works against XWayland-hosted clipboards.
    if shell.have("wl-copy"):
        return "wl-clipboard"
    return ""


def status() -> str:
    name = backend_name()
    if name:
        return f"Clipboard ready via {name}."
    if detect.environment().is_wayland:
        return "Clipboard unavailable. Install:  sudo pacman -S wl-clipboard"
    return "Clipboard unavailable. Install:  sudo pacman -S xclip"


def copy(text: str) -> str:
    """Put text on the clipboard."""
    backend = backend_name()
    if not backend:
        raise RuntimeError(status())

    if backend == "wl-clipboard":
        args = ["wl-copy", "--"]
    elif backend == "xclip":
        args = ["xclip", "-selection", "clipboard"]
    else:
        args = ["xsel", "--clipboard", "--input"]

    # wl-copy forks a server to own the selection; it must not be killed by
    # the timeout, so keep the window short and treat a timeout as success.
    result = shell.run(args, input_text=text, timeout=8)
    if not result.ok and not result.timed_out:
        raise RuntimeError(f"clipboard copy failed: {result.output}")

    preview = text if len(text) <= 60 else text[:60] + "…"
    return f"Copied to clipboard: {preview}"


def paste() -> str:
    """Read the clipboard contents."""
    backend = backend_name()
    if not backend:
        raise RuntimeError(status())

    if backend == "wl-clipboard":
        args = ["wl-paste", "--no-newline"]
    elif backend == "xclip":
        args = ["xclip", "-selection", "clipboard", "-o"]
    else:
        args = ["xsel", "--clipboard", "--output"]

    result = shell.run(args, timeout=8)
    if not result.ok:
        if "empty" in result.stderr.lower():
            return ""
        raise RuntimeError(f"clipboard read failed: {result.output}")
    return result.stdout


def clear() -> str:
    """Empty the clipboard."""
    backend = backend_name()
    if backend == "wl-clipboard":
        shell.run(["wl-copy", "--clear"], timeout=5)
    else:
        copy("")
    return "Clipboard cleared."
