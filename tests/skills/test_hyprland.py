"""Tests: hyprland.

Split out of the legacy tests/test_linux_port.py with no change to any test body.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kravein import llm, registry, safety  # noqa: E402,F401
from kravein.safety import Danger  # noqa: E402,F401


# Hyprland dispatcher (0.56 Lua rewrite)
# ---------------------------------------------------------------------------

def _hypr(monkeypatch, *, lua=True):
    """A Hyprland backend with the dialect pinned and calls recorded."""
    from kravein.platform import desktop

    backend = desktop.HyprlandBackend()
    backend._lua = lua
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        from kravein.platform import shell

        calls.append(list(args))
        return shell.Result(list(args), 0, "ok", "")

    monkeypatch.setattr("kravein.platform.shell.run", fake_run)
    return backend, calls


def _window(addr="0xABC"):
    from kravein.platform import desktop

    return desktop.Window(
        id=addr, title="t", app="a", pid=1, workspace="1", focused=False
    )


def test_hyprland_lua_escaping_blocks_injection():
    from kravein.platform.desktop import _lua_string, _lua_value

    # Window titles and user commands reach hyprctl eval as Lua source.
    assert _lua_string('a"b') == '"a\\"b"'
    assert _lua_string("a\\b") == '"a\\\\b"'
    assert _lua_string("a\nb") == '"a\\nb"'
    # Numeric workspaces stay numbers; names get quoted.
    assert _lua_value("3") == "3"
    assert _lua_value("-1") == "-1"
    assert _lua_value("special") == '"special"'


def test_hyprland_close_focuses_before_acting(monkeypatch):
    """The bug that killed the wrong window.

    In the 0.56 Lua API window.close() acts on the FOCUSED window and ignores
    any address argument, so close must focus the target first and confirm the
    focus landed. Passing an address alone destroys whatever is focused.
    """
    from kravein.platform import desktop

    backend, calls = _hypr(monkeypatch)
    target = _window("0xDEAD")
    monkeypatch.setattr(
        backend, "_ipc", lambda *a, **k: {"address": "0xDEAD"}
    )

    assert backend.close(target)

    joined = [" ".join(c) for c in calls]
    focus_index = next(i for i, c in enumerate(joined) if "hl.dsp.focus" in c)
    close_index = next(i for i, c in enumerate(joined) if "window.close" in c)
    assert focus_index < close_index, "must focus before closing"
    # The address must never be passed to close as if it were a selector.
    assert "0xDEAD" not in joined[close_index]
    assert "0xDEAD" in joined[focus_index]


def test_hyprland_close_aborts_if_focus_does_not_land(monkeypatch):
    """If focus fails, closing would destroy the wrong window — so refuse."""
    from kravein.platform import desktop

    backend, calls = _hypr(monkeypatch)
    # activewindow never becomes the target.
    monkeypatch.setattr(backend, "_ipc", lambda *a, **k: {"address": "0xOTHER"})

    assert backend.close(_window("0xDEAD")) is False
    assert not any("window.close" in " ".join(c) for c in calls)


def test_hyprland_kill_also_focuses_first(monkeypatch):
    backend, calls = _hypr(monkeypatch)
    monkeypatch.setattr(backend, "_ipc", lambda *a, **k: {"address": "0xDEAD"})

    assert backend.kill(_window("0xDEAD"))
    joined = [" ".join(c) for c in calls]
    assert any("hl.dsp.focus" in c for c in joined)
    assert any("window.kill()" in c for c in joined)


def test_hyprland_legacy_dialect_uses_old_syntax(monkeypatch):
    backend, calls = _hypr(monkeypatch, lua=False)
    backend.close(_window("0xBEEF"))
    joined = " ".join(" ".join(c) for c in calls)
    assert "dispatch closewindow address:0xBEEF" in joined
    assert "hl.dsp" not in joined


def test_hyprland_workspace_switch_uses_focus_table(monkeypatch):
    backend, calls = _hypr(monkeypatch)
    backend.workspace("4")
    joined = " ".join(" ".join(c) for c in calls)
    assert "hl.dsp.focus({workspace=4})" in joined


def test_hyprland_rejects_warnings_as_failure(monkeypatch):
    """'window not found' comes back as a warning, not a non-zero exit."""
    from kravein.platform import desktop, shell

    backend = desktop.HyprlandBackend()
    backend._lua = True
    monkeypatch.setattr(
        "kravein.platform.shell.run",
        lambda args, **k: shell.Result(list(args), 0, "warning: window not found", ""),
    )
    assert backend._eval("return 1") is False


def test_hyprland_accepts_plain_ok(monkeypatch):
    from kravein.platform import desktop, shell

    backend = desktop.HyprlandBackend()
    backend._lua = True
    monkeypatch.setattr(
        "kravein.platform.shell.run",
        lambda args, **k: shell.Result(list(args), 0, "ok", ""),
    )
    assert backend._eval("return 1") is True


def test_hyprland_lua_detection_is_cached(monkeypatch):
    from kravein.platform import desktop, shell

    backend = desktop.HyprlandBackend()
    probes = []

    def fake_run(args, **kwargs):
        probes.append(list(args))
        return shell.Result(list(args), 0, "ok", "")

    monkeypatch.setattr("kravein.platform.shell.run", fake_run)
    assert backend.uses_lua() is True
    assert backend.uses_lua() is True
    assert len(probes) == 1, "dialect must be probed only once"


def test_every_backend_implements_kill():
    from kravein.platform import desktop

    for cls in (desktop.HyprlandBackend, desktop.SwayBackend,
                desktop.X11Backend, desktop.NullBackend):
        assert hasattr(cls, "kill") or hasattr(desktop.WindowBackend, "kill")


def test_move_to_workspace_accepts_a_target_window():
    import inspect

    from kravein.platform import desktop

    for cls in (desktop.HyprlandBackend, desktop.SwayBackend,
                desktop.X11Backend, desktop.NullBackend):
        sig = inspect.signature(cls.move_to_workspace)
        assert "window" in sig.parameters, f"{cls.__name__} lost the window arg"


# ---------------------------------------------------------------------------
