"""Window and workspace control.

Ports the window half of ``actions/computer_settings.py`` (which sent
``win+left``, ``alt+f4`` and friends through pyautogui) onto real compositor
IPC, so it works without synthetic input.
"""

from __future__ import annotations

import time

from ..platform import desktop
from ..registry import skill
from ..safety import Danger, Verdict, enforce


@skill(category="windows", danger=Danger.SAFE)
def list_windows() -> str:
    """List every open window, grouped by workspace."""
    return desktop.describe_windows()


@skill(category="windows", danger=Danger.SAFE)
def focused_window() -> str:
    """Describe the window that currently has focus."""
    win = desktop.backend().focused()
    if win is None:
        return "No window is focused."
    lines = [
        f"App       : {win.app}",
        f"Title     : {win.title}",
        f"Workspace : {win.workspace}",
    ]
    if win.pid:
        lines.append(f"PID       : {win.pid}")
    if win.geometry:
        x, y, w, h = win.geometry
        lines.append(f"Geometry  : {w}x{h} at ({x}, {y})")
    return "\n".join(lines)


@skill(category="windows", danger=Danger.MODERATE)
def focus_window(query: str) -> str:
    """Bring a window to the front by app name or title.

    Args:
        query: Part of the application id or window title.
    """
    matches = desktop.find_windows(query)
    if not matches:
        return f"No window matches '{query}'."

    target = matches[0]
    if desktop.backend().focus(target):
        return f"Focused {target.app}: {target.title[:60]}"
    return f"Could not focus {target.app}."


@skill(category="windows", danger=Danger.MODERATE)
def switch_workspace(workspace: str) -> str:
    """Switch to a workspace.

    Args:
        workspace: Workspace name or number, e.g. '3'.
    """
    if desktop.backend().workspace(str(workspace)):
        return f"Switched to workspace {workspace}."
    return f"Could not switch to workspace {workspace}."


@skill(category="windows", danger=Danger.MODERATE)
def move_window_to_workspace(workspace: str) -> str:
    """Move the focused window to another workspace.

    Args:
        workspace: Destination workspace name or number.
    """
    backend = desktop.backend()
    win = backend.focused()
    if win is None:
        return "No window is focused."
    if backend.move_to_workspace(str(workspace), window=win):
        return f"Moved {win.app} to workspace {workspace}."
    return f"Could not move the window to workspace {workspace}."


@skill(category="windows", danger=Danger.SAFE)
def list_workspaces() -> str:
    """List the compositor's workspaces."""
    spaces = desktop.backend().list_workspaces()
    if not spaces:
        return "No workspaces reported by this compositor."
    windows = desktop.backend().list_windows()
    counts: dict[str, int] = {}
    for win in windows:
        counts[win.workspace] = counts.get(win.workspace, 0) + 1
    lines = [f"{len(spaces)} workspace(s):"]
    for space in spaces:
        lines.append(f"  {space}: {counts.get(space, 0)} window(s)")
    return "\n".join(lines)


@skill(category="windows", danger=Danger.MODERATE)
def toggle_fullscreen() -> str:
    """Toggle fullscreen on the focused window."""
    if desktop.backend().fullscreen():
        return "Toggled fullscreen."
    return "Could not toggle fullscreen on this compositor."


@skill(category="windows", danger=Danger.MODERATE)
def toggle_floating() -> str:
    """Toggle tiling/floating on the focused window."""
    if desktop.backend().toggle_float():
        return "Toggled floating."
    return "This compositor does not support floating toggle."


@skill(category="windows", danger=Danger.DESTRUCTIVE)
def close_window(
    query: str = "",
    force: bool = False,
    _source: str = "local",
    _confirm: bool = False,
) -> str:
    """Close a window. Defaults to the focused one.

    Args:
        query: Optional app name or title; omit to close the focused window.
        force: Kill the client instead of politely asking it to close.
    """
    backend = desktop.backend()
    if query:
        matches = desktop.find_windows(query)
        if not matches:
            return f"No window matches '{query}'."
        target = matches[0]
    else:
        target = backend.focused()
        if target is None:
            return "No window is focused."

    verb = "kill" if force else "close"
    verdict = Verdict(
        danger=Danger.DESTRUCTIVE,
        reasons=[f"{verb}s '{target.app}: {target.title[:50]}' (unsaved work may be lost)"],
    )
    enforce(verdict, f"{verb} window '{target.app}'",
            skill="close_window", source=_source, pre_confirmed=_confirm)

    accepted = backend.kill(target) if force else backend.close(target)

    # Verify it actually went away rather than trusting the request.
    time.sleep(0.9)
    if target.id not in {w.id for w in backend.list_windows()}:
        return f"Closed {target.app}: {target.title[:60]}"

    if accepted:
        return (
            f"{target.app} is still open — it may be asking you to save."
            + ("" if force else " Re-run with force=true to kill it.")
        )
    return f"Could not {verb} {target.app} (backend: {backend.name})."
