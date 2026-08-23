"""Window and workspace control.

Ports the window half of ``actions/computer_settings.py`` (which sent
``win+left``, ``alt+f4`` and friends through pyautogui) onto real compositor
IPC, so it works without synthetic input.
"""

from __future__ import annotations

import os
import time

from ..platform import desktop
from ..registry import skill
from ..safety import Danger, Verdict, describe_targets, enforce

# How long to wait for windows to actually disappear. Hyprland answering "ok"
# only means the request was accepted — an app showing a save prompt stays put.
_GONE_TIMEOUT = 2.5
_GONE_POLL = 0.15


def _own_pids() -> set[int]:
    """This process and its ancestors — i.e. the terminal hosting Brahma.

    Closing a workspace must not close the session doing the closing. That has
    happened: a window was targeted by list position and the assistant killed
    its own terminal mid-task. A bulk close is exactly where that repeats, so
    these are filtered out and reported rather than acted on.
    """
    pids: set[int] = {os.getpid()}
    try:
        import psutil

        for parent in psutil.Process().parents():
            pids.add(parent.pid)
    except Exception:
        pass
    return pids


class AmbiguousTarget(Exception):
    """More than one window matched and there is no basis to choose."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _describe(w) -> str:
    return f"workspace {w.workspace} · {w.app} · {w.title[:44]} · pid {w.pid}"


def _resolve_one(query: str, workspace: str = ""):
    """Resolve exactly one window, or refuse.

    `find_windows` returns *every* match ranked by quality, and callers used to
    take `matches[0]`. With three `foot` terminals open that picked an
    arbitrary one: a user who asked to close the terminal on workspace 4, from
    workspace 1, had the workspace-2 terminal closed instead — the one running
    the server. The confirmation card said only "close window 'foot'", so there
    was nothing in it to catch the mistake.

    Guessing is the bug. When the query cannot identify a single window this
    raises, and the caller reports the candidates so the choice comes back to
    whoever asked.
    """
    matches = desktop.find_windows(query)
    if workspace:
        want = str(workspace).strip()
        matches = [w for w in matches if str(w.workspace) == want]
        if not matches:
            raise AmbiguousTarget(
                f"No window matching '{query}' on workspace {want}."
            )

    if not matches:
        raise AmbiguousTarget(f"No window matches '{query}'.")
    if len(matches) == 1:
        return matches[0]

    # An exact app-id match that is unique still counts as unambiguous.
    text = (query or "").strip().lower()
    exact = [w for w in matches if (w.app or "").lower() == text]
    if len(exact) == 1:
        return exact[0]

    lines = [
        f"'{query}' matches {len(matches)} windows — say which one "
        f"(add workspace=…, or use a distinctive part of the title):"
    ]
    lines += [f"  {_describe(w)}" for w in matches]
    raise AmbiguousTarget("\n".join(lines))


def _restore_workspace(backend, origin) -> None:
    """Put the user back where they were before we focused a target."""
    if origin is None or not origin.workspace:
        return
    try:
        current = backend.focused()
        if current is not None and str(current.workspace) == str(origin.workspace):
            return
        backend.workspace(str(origin.workspace))
    except Exception:
        pass


def _still_matches(backend, target) -> bool:
    """Is the window we resolved still the same window?

    Resolution, confirmation and the action are three separate moments, and
    windows open and close in between. Acting on a stale handle is how the
    wrong thing gets closed.
    """
    for w in backend.list_windows():
        if w.id == target.id:
            return w.app == target.app
    return False


def _wait_until_gone(backend, ids: set[str], timeout: float = _GONE_TIMEOUT) -> set[str]:
    """Poll until the given windows disappear. Returns the ones still open.

    One shared wait for a batch, rather than a fixed sleep per window — closing
    six windows used to cost six full sleeps even when they all went instantly.
    """
    deadline = time.monotonic() + timeout
    remaining = set(ids)
    while remaining and time.monotonic() < deadline:
        time.sleep(_GONE_POLL)
        try:
            alive = {w.id for w in backend.list_windows()}
        except Exception:
            break
        remaining &= alive
    return remaining


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
def focus_window(query: str, workspace: str = "") -> str:
    """Bring a window to the front by app name or title.

    Args:
        query: Part of the application id or window title.
        workspace: Only consider windows on this workspace.
    """
    try:
        target = _resolve_one(query, workspace)
    except AmbiguousTarget as exc:
        return exc.message

    if desktop.backend().focus(target):
        return f"Focused {_describe(target)}"
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
    workspace: str = "",
    force: bool = False,
    _source: str = "local",
    _confirm: bool = False,
) -> str:
    """Close a window. Defaults to the focused one.

    Args:
        query: Optional app name or title; omit to close the focused window.
        workspace: Only consider windows on this workspace. Use it whenever the
            app name could match more than one window.
        force: Kill the client instead of politely asking it to close.
    """
    backend = desktop.backend()
    if query:
        try:
            target = _resolve_one(query, workspace)
        except AmbiguousTarget as exc:
            return exc.message
    else:
        if workspace:
            return "Give a window to close, or use close_workspace for a whole workspace."
        target = backend.focused()
        if target is None:
            return "No window is focused."

    # Never close the session Brahma is running in, whatever was asked for.
    if target.pid in _own_pids():
        return (
            f"Refusing: {_describe(target)} is the window Brahma is running in. "
            "Closing it would end this session."
        )

    # Remember where the user actually is. Acting on a window has to focus it,
    # and leaving them on a workspace they never asked for is disorienting —
    # it is also what made a mis-targeted close look like a workspace switch.
    origin = backend.focused()

    verb = "kill" if force else "close"
    verdict = Verdict(
        danger=Danger.DESTRUCTIVE,
        # Name the workspace, title and pid. "close window 'foot'" was not
        # reviewable with three foot terminals open, so approving it could not
        # be an informed decision.
        reasons=[f"{verb}s {_describe(target)} (unsaved work may be lost)"],
    )
    # Declaring targets rather than letting safety.py read them back out of the
    # sentence. Two of these matter:
    #   window:<id>  the exact window, so a retry under any verb is caught.
    #   close_window:implicit  set only when no query was given, i.e. the target
    #     was "whatever is focused". Declining that means the user rejected a
    #     guess — and the next call can silently resolve a *different* window,
    #     on a different workspace, because focus moves. Recording the guess
    #     itself stops the retry instead of prompting about something the user
    #     never asked about.
    enforce(verdict, f"{verb} window '{target.app}' on workspace {target.workspace}",
            skill="close_window", source=_source, pre_confirmed=_confirm,
            targets=describe_targets(
                f"window:{target.id}",
                f"app:{target.app}",
                "" if query else "close_window:implicit",
            ))

    # Resolution, confirmation and action are three separate moments. Between
    # them the window list can change, and acting on a stale handle is exactly
    # how the wrong window gets destroyed.
    if not _still_matches(backend, target):
        return (
            f"Not closing anything: {target.app} on workspace {target.workspace} "
            "is no longer there. Ask again and I will re-check."
        )

    accepted = backend.kill(target) if force else backend.close(target)

    # Verify it actually went away rather than trusting the request. Polling
    # returns as soon as it is gone instead of always paying a fixed sleep.
    gone = not _wait_until_gone(backend, {target.id})
    _restore_workspace(backend, origin)
    if gone:
        return f"Closed {_describe(target)}"

    if accepted:
        return (
            f"{target.app} on workspace {target.workspace} is still open — "
            "it may be asking you to save."
            + ("" if force else " Re-run with force=true to kill it.")
        )
    return f"Could not {verb} {target.app} (backend: {backend.name})."


@skill(category="windows", danger=Danger.DESTRUCTIVE)
def close_workspace(
    workspace: str = "",
    force: bool = False,
    _source: str = "local",
    _confirm: bool = False,
) -> str:
    """Close every window on a workspace.

    Args:
        workspace: Which workspace to clear; omit for the current one.
        force: Kill the clients instead of politely asking them to close.
    """
    backend = desktop.backend()
    if not backend.available():
        return "No supported compositor is available."

    target = str(workspace).strip()
    if not target:
        current = backend.focused()
        if current is None or not current.workspace:
            return "No workspace is focused — say which one to close."
        target = str(current.workspace)

    windows = [w for w in backend.list_windows() if str(w.workspace) == target]
    if not windows:
        return f"Workspace {target} is already empty."

    # Never close the session running Brahma. Doing this by hand is how a
    # terminal got killed mid-task once already.
    mine = _own_pids()
    protected = [w for w in windows if w.pid in mine]
    closable = [w for w in windows if w.pid not in mine]

    if not closable:
        return (
            f"Workspace {target} only holds the window Brahma is running in "
            f"({protected[0].app}), so there is nothing safe to close."
        )

    verb = "kill" if force else "close"
    listing = "; ".join(f"{w.app}: {w.title[:40]}" for w in closable)

    # One confirmation for the whole workspace. Asking per window turned
    # "close workspace 2" into a queue of prompts, and answering no to one of
    # them left the rest half-done.
    verdict = Verdict(
        danger=Danger.DESTRUCTIVE,
        reasons=[
            f"{verb}s {len(closable)} window(s) on workspace {target} "
            f"(unsaved work may be lost): {listing}"
        ],
    )
    enforce(
        verdict,
        f"{verb} {len(closable)} window(s) on workspace {target}",
        skill="close_workspace", source=_source, pre_confirmed=_confirm,
        targets=describe_targets(
            f"workspace:{target}",
            *[f"window:{w.id}" for w in closable],
        ),
    )

    # Issue every close first, then wait once for the batch. Waiting after each
    # one made a six-window workspace take six times as long as it needed to.
    rejected = []
    for window in closable:
        try:
            accepted = backend.kill(window) if force else backend.close(window)
        except Exception:
            accepted = False
        if not accepted:
            rejected.append(window)

    still_open = _wait_until_gone(backend, {w.id for w in closable})

    closed = [w for w in closable if w.id not in still_open]
    lines = [f"Workspace {target}: closed {len(closed)} of {len(closable)} window(s)."]

    if closed:
        lines += [f"  closed  {w.app}: {w.title[:50]}" for w in closed]

    stuck = [w for w in closable if w.id in still_open]
    if stuck:
        lines.append("")
        for window in stuck:
            why = (
                "did not accept the request"
                if window in rejected
                else "still open — it may be asking you to save"
            )
            lines.append(f"  still open  {window.app}: {window.title[:40]} — {why}")
        if not force:
            lines.append("Re-run with force=true to kill the ones left.")

    if protected:
        lines.append("")
        lines.append(
            f"  skipped  {protected[0].app} — Brahma is running in it."
            if len(protected) == 1
            else f"  skipped  {len(protected)} window(s) hosting Brahma."
        )
    return "\n".join(lines)
