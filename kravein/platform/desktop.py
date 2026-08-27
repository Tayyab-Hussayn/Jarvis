"""Window and workspace control.

Replaces ``pygetwindow`` and ``pywinauto`` (Windows UIA). Linux has no single
window API, so this presents one interface over several backends:

  * Hyprland  — ``hyprctl`` IPC (this machine)
  * Sway      — ``swaymsg`` IPC
  * X11       — ``wmctrl`` / ``xdotool``
  * KDE/GNOME — best-effort via D-Bus or the X11 fallback

Backends are picked at import time from :mod:`detect` and can be re-probed.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from . import detect, shell


def _lua_string(value: str) -> str:
    """Quote a Python string as a Lua literal.

    Everything sent to ``hyprctl eval`` is executed as Lua, so any value that
    came from a window title, a workspace name or a user command has to be
    escaped or it can break out of the expression.
    """
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _lua_value(value: str) -> str:
    """Render a workspace identifier as a Lua number when it is one."""
    text = str(value).strip()
    if text.lstrip("+-").isdigit():
        return text
    return _lua_string(text)


@dataclass
class Window:
    """A window, normalised across backends."""

    id: str
    title: str
    app: str          # app_id / WM_CLASS
    pid: int | None
    workspace: str
    focused: bool
    geometry: tuple[int, int, int, int] | None = None  # x, y, w, h

    def describe(self) -> str:
        mark = "→ " if self.focused else "  "
        where = f" [{self.workspace}]" if self.workspace else ""
        title = self.title or "(untitled)"
        return f"{mark}{self.app}: {title}{where}"


class WindowBackend:
    """Interface every compositor backend implements."""

    name = "none"

    def available(self) -> bool:
        return False

    def move_cursor(self, x: int, y: int) -> bool:
        """Place the pointer at an absolute position, if the compositor can.

        Backends that cannot say so, so the caller can fall back rather than
        assume the pointer went where it was asked. False means "not done",
        never "probably done".
        """
        return False

    def list_windows(self) -> list[Window]:
        raise NotImplementedError

    def focused(self) -> Window | None:
        for win in self.list_windows():
            if win.focused:
                return win
        return None

    def focus(self, window: Window) -> bool:
        raise NotImplementedError

    def close(self, window: Window) -> bool:
        raise NotImplementedError

    def kill(self, window: Window) -> bool:
        """Force-close. Backends without a distinct kill fall back to close."""
        return self.close(window)

    def fullscreen(self) -> bool:
        raise NotImplementedError

    def toggle_float(self) -> bool:
        raise NotImplementedError

    def workspace(self, target: str) -> bool:
        raise NotImplementedError

    def move_to_workspace(self, target: str, window: Window | None = None) -> bool:
        raise NotImplementedError

    def list_workspaces(self) -> list[str]:
        return []

    def spawn(self, command: list[str]) -> bool:
        """Launch through the compositor so the app lands on the right output."""
        shell.spawn(command)
        return True


# ---------------------------------------------------------------------------
# Hyprland
# ---------------------------------------------------------------------------

class HyprlandBackend(WindowBackend):
    """Hyprland, across the 0.56 dispatcher rewrite.

    Hyprland 0.56 replaced the shell-style dispatcher with a Lua API. The old
    ``hyprctl dispatch closewindow address:0x…`` form now fails to parse:

        error: [string "return hl.dispatch(closewindow address:0x…"]:1:
        ')' expected near 'address'

    So every operation has two spellings and the backend probes once, at
    first use, to decide which the running compositor speaks.

    The Lua forms below were each verified against a live 0.56.2 session:
        close    hl.dispatch(hl.dsp.window.close("address:X"))
        kill     hl.dispatch(hl.dsp.window.kill("address:X"))
        focus    hl.dispatch(hl.dsp.focus({window="address:X"}))
        ws       hl.dispatch(hl.dsp.focus({workspace=N}))
        move     hl.dispatch(hl.dsp.window.move({workspace=N}))

    Note that ``window.move``, ``float`` and ``fullscreen`` act on the
    *focused* window and take no selector, so targeting one means focusing it
    first — see :meth:`move_to_workspace`.
    """

    name = "hyprland"

    def __init__(self) -> None:
        self._lua: bool | None = None

    def available(self) -> bool:
        return shell.have("hyprctl")

    def _ipc(self, *args: str, json_out: bool = True) -> Any:
        cmd = ["hyprctl"]
        if json_out:
            cmd.append("-j")
        cmd.extend(args)
        result = shell.run(cmd, timeout=6)
        if not result.ok:
            return None
        if not json_out:
            return result.stdout.strip()
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return None

    # -- dispatcher dialect ---------------------------------------------

    def uses_lua(self) -> bool:
        """Does this Hyprland use the 0.56+ Lua dispatcher?

        Probed with ``no_op``, which changes nothing either way.
        """
        if self._lua is None:
            probe = shell.run(
                ["hyprctl", "eval", "return hl.dispatch(hl.dsp.no_op())"], timeout=6
            )
            output = (probe.stdout + probe.stderr).lower()
            self._lua = probe.ok and "error" not in output
        return self._lua

    @staticmethod
    def _accepted(result: "shell.Result") -> bool:
        """Hyprland answers 'ok'; warnings and errors go to the same stream."""
        if not result.ok:
            return False
        blob = (result.stdout + result.stderr).lower()
        if "error" in blob or "warning" in blob:
            return False
        return "ok" in blob

    def _eval(self, lua: str) -> bool:
        return self._accepted(shell.run(["hyprctl", "eval", lua], timeout=8))

    def _legacy(self, *args: str) -> bool:
        return self._accepted(shell.run(["hyprctl", "dispatch", *args], timeout=8))

    def _dispatch(self, lua: str, *legacy: str) -> bool:
        """Run an operation using whichever dialect the compositor speaks."""
        if self.uses_lua():
            return self._eval(lua)
        return self._legacy(*legacy)

    def move_cursor(self, x: int, y: int) -> bool:
        """Absolute pointer placement, which ydotool cannot do here.

        `ydotool mousemove --absolute` lands the pointer in the bottom-right
        corner no matter what coordinates it is given — its uinput device has
        no usable absolute axis range — and exits 0 while doing it. The
        compositor's own dispatcher is exact.

        The 0.56 Lua form insists on a named table: `hl.dsp.cursor.move(400,
        300)` and `{400, 300}` both fail with "'x' is required".
        """
        return self._dispatch(
            f"return hl.dispatch(hl.dsp.cursor.move({{x={int(x)},y={int(y)}}}))",
            "movecursor", str(int(x)), str(int(y)),
        )

    def list_windows(self) -> list[Window]:
        clients = self._ipc("clients") or []
        active = self._ipc("activewindow") or {}
        active_addr = active.get("address") if isinstance(active, dict) else None

        windows: list[Window] = []
        for client in clients:
            if not isinstance(client, dict):
                continue
            ws = client.get("workspace") or {}
            at = client.get("at") or [0, 0]
            size = client.get("size") or [0, 0]
            windows.append(Window(
                id=client.get("address", ""),
                title=client.get("title", ""),
                app=client.get("class", "") or client.get("initialClass", ""),
                pid=client.get("pid") if isinstance(client.get("pid"), int) else None,
                workspace=str(ws.get("name", ws.get("id", ""))),
                focused=client.get("address") == active_addr,
                geometry=(at[0], at[1], size[0], size[1]) if len(at) == 2 and len(size) == 2 else None,
            ))
        return windows

    def focus(self, window: Window) -> bool:
        return self._dispatch(
            f'return hl.dispatch(hl.dsp.focus({{window="address:{window.id}"}}))',
            "focuswindow", f"address:{window.id}",
        )

    def _act_on(self, window: Window, lua_action: str, *legacy: str) -> bool:
        """Run a focused-window action against a specific window.

        DANGEROUS TO GET WRONG. In the 0.56 Lua API ``window.close`` and
        ``window.kill`` operate on the **focused** window and silently ignore
        any ``"address:…"`` argument — passing one looks like it targets that
        window but destroys whatever happens to be focused instead. The window
        must therefore be focused first, and the focus confirmed, before the
        action is issued.
        """
        if not self.uses_lua():
            return self._legacy(*legacy)

        if not self.focus(window):
            return False

        # Confirm focus actually landed; acting blind is what kills the
        # wrong window.
        for _ in range(10):
            time.sleep(0.08)
            active = self._ipc("activewindow") or {}
            if isinstance(active, dict) and active.get("address") == window.id:
                break
        else:
            return False

        return self._eval(f"return hl.dispatch({lua_action})")

    def close(self, window: Window) -> bool:
        """Ask the window to close, so the app can prompt to save."""
        return self._act_on(
            window, "hl.dsp.window.close()",
            "closewindow", f"address:{window.id}",
        )

    def kill(self, window: Window) -> bool:
        """Force the window's client to die without asking it."""
        return self._act_on(
            window, "hl.dsp.window.kill()",
            "killwindow", f"address:{window.id}",
        )

    def fullscreen(self) -> bool:
        return self._dispatch(
            "return hl.dispatch(hl.dsp.window.fullscreen())",
            "fullscreen", "1",
        )

    def toggle_float(self) -> bool:
        return self._dispatch(
            "return hl.dispatch(hl.dsp.window.float())",
            "togglefloating",
        )

    def workspace(self, target: str) -> bool:
        return self._dispatch(
            f'return hl.dispatch(hl.dsp.focus({{workspace={_lua_value(target)}}}))',
            "workspace", str(target),
        )

    def move_to_workspace(self, target: str, window: Window | None = None) -> bool:
        """Move a window to a workspace.

        Like close and kill, the Lua dispatcher moves the *focused* window, so
        an explicit target is focused first.
        """
        action = f"hl.dsp.window.move({{workspace={_lua_value(target)}}})"

        if window is not None:
            return self._act_on(window, action, "movetoworkspace", str(target))

        return self._dispatch(
            f"return hl.dispatch({action})", "movetoworkspace", str(target)
        )

    def list_workspaces(self) -> list[str]:
        spaces = self._ipc("workspaces") or []
        return [
            str(w.get("name", w.get("id", "")))
            for w in spaces if isinstance(w, dict)
        ]

    def spawn(self, command: list[str]) -> bool:
        # Launching through the compositor lets window rules apply.
        joined = " ".join(command)
        if self._dispatch(
            f"return hl.dispatch(hl.dsp.exec_cmd({_lua_string(joined)}))",
            "exec", joined,
        ):
            return True
        shell.spawn(command)
        return True


# ---------------------------------------------------------------------------
# Sway
# ---------------------------------------------------------------------------

class SwayBackend(WindowBackend):
    name = "sway"

    def available(self) -> bool:
        return shell.have("swaymsg")

    def _tree(self) -> dict | None:
        result = shell.run(["swaymsg", "-t", "get_tree", "-r"], timeout=6)
        if not result.ok:
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return None

    def _walk(self, node: dict, workspace: str = "") -> list[Window]:
        out: list[Window] = []
        if node.get("type") == "workspace":
            workspace = str(node.get("name", ""))

        if node.get("pid") and node.get("type") in ("con", "floating_con"):
            rect = node.get("rect") or {}
            out.append(Window(
                id=str(node.get("id", "")),
                title=node.get("name", "") or "",
                app=node.get("app_id") or (node.get("window_properties") or {}).get("class", ""),
                pid=node.get("pid"),
                workspace=workspace,
                focused=bool(node.get("focused")),
                geometry=(rect.get("x", 0), rect.get("y", 0), rect.get("width", 0), rect.get("height", 0)),
            ))

        for child_key in ("nodes", "floating_nodes"):
            for child in node.get(child_key, []) or []:
                out.extend(self._walk(child, workspace))
        return out

    def list_windows(self) -> list[Window]:
        tree = self._tree()
        return self._walk(tree) if tree else []

    def _cmd(self, command: str) -> bool:
        return shell.run(["swaymsg", command], timeout=6).ok

    def focus(self, window: Window) -> bool:
        return self._cmd(f'[con_id={window.id}] focus')

    def close(self, window: Window) -> bool:
        return self._cmd(f'[con_id={window.id}] kill')

    def fullscreen(self) -> bool:
        return self._cmd("fullscreen toggle")

    def toggle_float(self) -> bool:
        return self._cmd("floating toggle")

    def workspace(self, target: str) -> bool:
        return self._cmd(f"workspace {target}")

    def move_to_workspace(self, target: str, window: Window | None = None) -> bool:
        if window is not None:
            return self._cmd(
                f'[con_id={window.id}] move container to workspace {target}'
            )
        return self._cmd(f"move container to workspace {target}")

    def list_workspaces(self) -> list[str]:
        result = shell.run(["swaymsg", "-t", "get_workspaces", "-r"], timeout=6)
        if not result.ok:
            return []
        try:
            return [str(w.get("name", "")) for w in json.loads(result.stdout)]
        except json.JSONDecodeError:
            return []

    def spawn(self, command: list[str]) -> bool:
        return self._cmd(f'exec {" ".join(command)}')


# ---------------------------------------------------------------------------
# X11
# ---------------------------------------------------------------------------

class X11Backend(WindowBackend):
    name = "x11"

    def available(self) -> bool:
        return shell.have("wmctrl") or shell.have("xdotool")

    def list_windows(self) -> list[Window]:
        if not shell.have("wmctrl"):
            return self._list_via_xdotool()

        result = shell.run(["wmctrl", "-lpGx"], timeout=6)
        if not result.ok:
            return []

        active_id = ""
        if shell.have("xdotool"):
            probe = shell.run(["xdotool", "getactivewindow"], timeout=4)
            if probe.ok:
                try:
                    active_id = hex(int(probe.stdout.strip()))
                except ValueError:
                    active_id = ""

        windows: list[Window] = []
        for line in result.stdout.splitlines():
            parts = line.split(None, 9)
            if len(parts) < 9:
                continue
            win_id, desktop, pid, x, y, w, h, wm_class = parts[:8]
            title = parts[9] if len(parts) > 9 else ""
            try:
                normalised = hex(int(win_id, 16))
            except ValueError:
                normalised = win_id
            windows.append(Window(
                id=win_id,
                title=title,
                app=wm_class.split(".")[-1],
                pid=int(pid) if pid.isdigit() else None,
                workspace=desktop,
                focused=normalised == active_id,
                geometry=(int(x), int(y), int(w), int(h)) if x.lstrip("-").isdigit() else None,
            ))
        return windows

    def _list_via_xdotool(self) -> list[Window]:
        result = shell.run(["xdotool", "search", "--onlyvisible", "--name", ""], timeout=6)
        if not result.ok:
            return []
        windows = []
        for wid in result.stdout.split():
            name = shell.run(["xdotool", "getwindowname", wid], timeout=3)
            windows.append(Window(
                id=wid, title=name.stdout.strip(), app="", pid=None,
                workspace="", focused=False,
            ))
        return windows

    def focus(self, window: Window) -> bool:
        if shell.have("wmctrl"):
            return shell.run(["wmctrl", "-i", "-a", window.id], timeout=5).ok
        return shell.run(["xdotool", "windowactivate", window.id], timeout=5).ok

    def close(self, window: Window) -> bool:
        if shell.have("wmctrl"):
            return shell.run(["wmctrl", "-i", "-c", window.id], timeout=5).ok
        return shell.run(["xdotool", "windowclose", window.id], timeout=5).ok

    def fullscreen(self) -> bool:
        return shell.run(
            ["wmctrl", "-r", ":ACTIVE:", "-b", "toggle,fullscreen"], timeout=5
        ).ok

    def toggle_float(self) -> bool:
        return False

    def workspace(self, target: str) -> bool:
        return shell.run(["wmctrl", "-s", str(target)], timeout=5).ok

    def move_to_workspace(self, target: str, window: Window | None = None) -> bool:
        if window is not None:
            return shell.run(
                ["wmctrl", "-i", "-r", window.id, "-t", str(target)], timeout=5
            ).ok
        return shell.run(
            ["wmctrl", "-r", ":ACTIVE:", "-t", str(target)], timeout=5
        ).ok


class NullBackend(WindowBackend):
    """Used on a TTY or an unsupported compositor."""

    name = "none"

    def available(self) -> bool:
        return True

    def list_windows(self) -> list[Window]:
        return []

    def focus(self, window: Window) -> bool:
        return False

    def close(self, window: Window) -> bool:
        return False

    def fullscreen(self) -> bool:
        return False

    def toggle_float(self) -> bool:
        return False

    def workspace(self, target: str) -> bool:
        return False

    def move_to_workspace(self, target: str, window: Window | None = None) -> bool:
        return False


_backend: WindowBackend | None = None


def backend() -> WindowBackend:
    """Pick the window backend for this session."""
    global _backend
    if _backend is not None:
        return _backend

    env = detect.environment()
    candidates: list[WindowBackend] = []
    if env.compositor == "hyprland":
        candidates = [HyprlandBackend(), SwayBackend(), X11Backend()]
    elif env.compositor == "sway":
        candidates = [SwayBackend(), HyprlandBackend(), X11Backend()]
    else:
        candidates = [X11Backend(), HyprlandBackend(), SwayBackend()]

    for candidate in candidates:
        if candidate.available():
            _backend = candidate
            break
    else:
        _backend = NullBackend()

    return _backend


def reset_backend() -> None:
    global _backend
    _backend = None


def focus_and_verify(window: Window, attempts: int = 12, pause: float = 0.08) -> bool:
    """Focus a window and confirm focus actually landed.

    Wayland focus requests are asynchronous and can be refused outright, so
    `backend().focus()` returning True only means the request was accepted.
    Anything that then synthesises input — typing, scrolling — must know the
    keystrokes will reach the window it named and not whatever happened to be
    in front. If focus does not arrive, callers send nothing.
    """
    if not backend().focus(window):
        return False
    for _ in range(attempts):
        time.sleep(pause)
        current = backend().focused()
        if current is not None and current.id == window.id:
            return True
    return False


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def find_windows(query: str) -> list[Window]:
    """Find windows whose title, app id or pid matches ``query``.

    Matching is ranked: exact app id first, then prefix, then substring.
    """
    text = (query or "").strip().lower()
    windows = backend().list_windows()
    if not text:
        return windows

    exact: list[Window] = []
    prefix: list[Window] = []
    partial: list[Window] = []

    for win in windows:
        app = (win.app or "").lower()
        title = (win.title or "").lower()
        if app == text or str(win.pid) == text:
            exact.append(win)
        elif app.startswith(text) or title.startswith(text):
            prefix.append(win)
        elif text in app or text in title:
            partial.append(win)

    return exact + prefix + partial


def describe_windows() -> str:
    """Human-readable window list."""
    windows = backend().list_windows()
    if not windows:
        impl = backend().name
        if impl == "none":
            return "No window backend available (not running a graphical session?)."
        return "No open windows."

    by_workspace: dict[str, list[Window]] = {}
    for win in windows:
        by_workspace.setdefault(win.workspace or "?", []).append(win)

    lines = [f"{len(windows)} window(s) via {backend().name}:"]
    for ws in sorted(by_workspace, key=lambda k: (len(k), k)):
        lines.append(f"  workspace {ws}:")
        for win in by_workspace[ws]:
            lines.append("    " + win.describe())
    return "\n".join(lines)
