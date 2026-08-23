"""Run commands in a *visible* terminal, by typing into it.

`run_command` executes and returns output — better whenever the caller only
wants the result. This is for when the command must run in a terminal the user
can see and keep interacting with: a dev server, a REPL, a long tail -f.

There is no protocol for typing into someone else's terminal, so this is the one
place synthetic input is genuinely required. The Windows build did the same
thing and got away with it by never checking anything — it typed at whatever had
focus, which is how it sent messages to the wrong person when a window was slow.

Every rule below exists because of a bug already fixed elsewhere in this
codebase:

* the target is resolved the way `close_window` resolves one — ambiguity is
  refused, not guessed;
* focus is confirmed to have landed before a single key is sent;
* the command is risk-classified exactly as `run_command` classifies it, because
  text typed at a shell runs with the user's privileges either way;
* Brahma's own terminal is never a target.
"""

from __future__ import annotations

import shlex
import time

from ..platform import desktop, inputs
from ..registry import skill
from ..safety import Danger, classify_command, describe_targets, enforce, own_pids
from .windows import AmbiguousTarget, _describe, _resolve_one

# Terminals we know how to spawn, best first.
_TERMINALS = ("foot", "kitty", "alacritty", "wezterm", "gnome-terminal", "konsole")

# How long to wait for a spawned terminal's window to appear.
_APPEAR_TIMEOUT = 6.0


def _pick_terminal(preferred: str = "") -> str:
    from ..platform import shell

    if preferred:
        return preferred if shell.have(preferred) else ""
    for name in _TERMINALS:
        if shell.have(name):
            return name
    return ""


def _focus_and_verify(window) -> bool:
    """Focus a window and confirm it actually landed.

    Typing into a window that was never focused is how the wrong thing gets
    typed into. If focus does not arrive, we send nothing at all.
    """
    backend = desktop.backend()
    if not backend.focus(window):
        return False
    for _ in range(12):
        time.sleep(0.08)
        current = backend.focused()
        if current is not None and current.id == window.id:
            return True
    return False


def _type_command(command: str, press_enter: bool) -> str:
    """Clear whatever is staged on the prompt, then type.

    Without the clear, the command is appended to anything already sitting on
    the line — a half-typed command, or output from a previous attempt — and
    the shell runs the concatenation. That is what produced
    `/tmp/brahma-term-test. OK DIRECT_OK` from a clean `echo DIRECT_OK`, and
    why the first end-to-end run silently did nothing: the resulting line was
    not a valid command.
    """
    # Ctrl+C abandons any partial line without executing it; Ctrl+U clears
    # what readline is holding. Both are harmless at an idle prompt.
    with _quiet():
        inputs.press("ctrl+c")
        time.sleep(0.12)
        inputs.press("ctrl+u")
        time.sleep(0.12)

    inputs.type_text(command)
    if press_enter:
        time.sleep(0.15)
        inputs.press("Return")
    return command


def _quiet():
    import contextlib

    return contextlib.suppress(Exception)


def _check_input_available() -> str:
    if not inputs.backend_name():
        return inputs.status()
    return ""


@skill(category="terminal", danger=Danger.DESTRUCTIVE)
def type_into_terminal(
    command: str,
    query: str = "",
    workspace: str = "",
    press_enter: bool = True,
    _source: str = "local",
    _confirm: bool = False,
) -> str:
    """Type a command into an already-open terminal window and run it.

    Use `run_command` instead when you only need the output — this exists for
    commands that must run in a terminal the user can watch.

    Args:
        command: The command line to type.
        query: Which terminal — app name or part of its title. Omit for the
            focused window.
        workspace: Only consider terminals on this workspace.
        press_enter: Press Return after typing. Set false to leave it staged.
    """
    command = (command or "").strip()
    if not command:
        return "No command given."

    problem = _check_input_available()
    if problem:
        return problem

    backend = desktop.backend()
    if not backend.available():
        return "No supported compositor is available."

    if query:
        try:
            target = _resolve_one(query, workspace)
        except AmbiguousTarget as exc:
            return exc.message
    else:
        if workspace:
            return "Say which terminal to type into, or focus it first."
        target = backend.focused()
        if target is None:
            return "No window is focused."

    if target.pid in own_pids():
        return (
            f"Refusing: {_describe(target)} is the terminal Brahma is running "
            "in. Typing into it would interfere with the assistant itself."
        )

    # The text is going to a shell, so it runs with the user's privileges the
    # moment Return is pressed. A human pressing that key does not make
    # `rm -rf` safer, so it is classified exactly as run_command classifies it.
    verdict = classify_command(command)
    if verdict.danger < Danger.DESTRUCTIVE:
        verdict.danger = Danger.DESTRUCTIVE
    verdict.reasons.insert(
        0, f"types and runs `{command}` in {_describe(target)}"
    )
    enforce(
        verdict, f"run `{command}` in the terminal on workspace {target.workspace}",
        skill="type_into_terminal", source=_source, pre_confirmed=_confirm,
        targets=describe_targets(
            f"window:{target.id}", f"terminal:{target.app}",
            "" if query else "type_into_terminal:implicit",
        ),
    )

    origin = backend.focused()
    if not _focus_and_verify(target):
        return (
            f"Could not focus {_describe(target)} — nothing was typed. "
            "Focus does not always land on Wayland; try again."
        )

    try:
        _type_command(command, press_enter)
    except inputs.InputError as exc:
        return f"Could not type into the terminal: {exc}"

    # Leave the user where they were, not where the command ran.
    if origin is not None and origin.id != target.id:
        with_suppress = True
        try:
            backend.focus(origin)
        except Exception:
            with_suppress = False
        del with_suppress

    # Synthetic input is fire-and-forget: the keystrokes go to the compositor
    # and there is no channel back to say the shell accepted them. Report what
    # was actually done rather than asserting the command succeeded.
    if press_enter:
        return (
            f"Typed `{command}` into {_describe(target)} and pressed Return. "
            "Check the terminal for its output — typing cannot be confirmed "
            "from here."
        )
    return f"Typed `{command}` into {_describe(target)} without running it."


@skill(category="terminal", danger=Danger.DESTRUCTIVE)
def open_terminal(
    command: str = "",
    terminal: str = "",
    workspace: str = "",
    _source: str = "local",
    _confirm: bool = False,
) -> str:
    """Open a new terminal window, optionally running a command in it.

    The command is typed into the new terminal rather than passed on its
    command line, so the shell stays open afterwards and the user can keep
    working in it.

    Args:
        command: Optional command to run once the terminal is open.
        terminal: Which terminal emulator. Defaults to the first available.
        workspace: Switch to this workspace before opening.
    """
    from ..platform import shell

    emulator = _pick_terminal(terminal)
    if not emulator:
        return (
            f"No supported terminal found. Tried: {', '.join(_TERMINALS)}."
            if not terminal
            else f"{terminal} is not installed."
        )

    command = (command or "").strip()
    backend = desktop.backend()

    if command:
        problem = _check_input_available()
        if problem:
            return problem
        verdict = classify_command(command)
        if verdict.danger < Danger.DESTRUCTIVE:
            verdict.danger = Danger.DESTRUCTIVE
        verdict.reasons.insert(0, f"opens a {emulator} terminal and runs `{command}`")
        enforce(
            verdict, f"open {emulator} and run `{command}`",
            skill="open_terminal", source=_source, pre_confirmed=_confirm,
            targets=describe_targets(f"terminal:{emulator}", f"command:{command}"),
        )

    if workspace and backend.available():
        backend.workspace(str(workspace))
        time.sleep(0.3)

    before = {w.id for w in backend.list_windows()} if backend.available() else set()

    if not shell.spawn([emulator]):
        return f"Could not launch {emulator}."

    # Wait for the window rather than assuming it appeared.
    target = None
    deadline = time.monotonic() + _APPEAR_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(0.2)
        fresh = [w for w in backend.list_windows() if w.id not in before]
        matching = [w for w in fresh if emulator.lower() in (w.app or "").lower()]
        if matching:
            target = matching[0]
            break

    if target is None:
        return (
            f"Launched {emulator}, but its window did not appear within "
            f"{_APPEAR_TIMEOUT:.0f}s — nothing was typed."
        )

    if not command:
        return f"Opened {_describe(target)}"

    # Give the shell a moment to draw its prompt, or the first keystrokes land
    # before anything is listening for them.
    time.sleep(0.6)
    if not _focus_and_verify(target):
        return (
            f"Opened {_describe(target)} but could not confirm focus, so the "
            "command was not typed."
        )

    try:
        _type_command(command, press_enter=True)
    except inputs.InputError as exc:
        return f"Opened the terminal but could not type into it: {exc}"

    return (
        f"Opened {_describe(target)} and typed `{command}` into it. "
        "Check the terminal for its output."
    )


@skill(category="terminal", danger=Danger.SAFE)
def list_terminals() -> str:
    """List open terminal windows, so one can be chosen to type into."""
    backend = desktop.backend()
    if not backend.available():
        return "No supported compositor is available."

    known = {t.lower() for t in _TERMINALS}
    mine = own_pids()
    found = [
        w for w in backend.list_windows()
        if (w.app or "").lower() in known
        or any(k in (w.app or "").lower() for k in known)
    ]
    if not found:
        return "No terminal windows are open."

    lines = [f"{len(found)} terminal window(s):"]
    for w in found:
        note = "  ← Brahma is running here" if w.pid in mine else ""
        lines.append(f"  {_describe(w)}{note}")
    return "\n".join(lines)
