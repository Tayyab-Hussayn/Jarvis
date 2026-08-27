"""Drive the graphical file manager.

The file *operations* — rename, delete, copy, create — already exist as
filesystem skills (`move_path`, `delete_path`, `copy_path`, `create_directory`)
and go through the safety layer. Those are the reliable path and should stay the
default: they act on the filesystem directly, they are confirmed where it
matters, and they report what actually happened.

What was missing is the *window*: opening the file manager somewhere, showing a
file in it, following along visually. This does that through the file manager's
own command line rather than by typing at its GUI.

Dolphin, Nautilus, Thunar and Nemo all accept a path argument and a
select/highlight flag. That is a real control surface — no synthetic input, no
guessing at coordinates, and it works whether or not the window is focused.
Driving a file manager with keystrokes is how the Windows build did this sort of
thing, and it is why it broke whenever a window was slow.
"""

from __future__ import annotations

from pathlib import Path

from .. import config
from ..platform import shell
from ..registry import skill
from ..safety import Danger, check_path, enforce

# Managers we know how to drive, in preference order. The second element is the
# flag that reveals-and-highlights a specific file rather than opening its
# directory blind.
_MANAGERS = (
    ("dolphin", "--select"),
    ("nautilus", "--select"),
    ("nemo", None),
    ("thunar", None),
    ("pcmanfm", None),
    ("caja", "--no-desktop"),
)


# Dolphin (and most others) open a new window per invocation and expose no
# D-Bus method to retarget an existing one — checked: org.kde.dolphin-<pid>
# has no changeUrl. Navigating four directories therefore left four windows.
# So the window Kravein opened is remembered and replaced on the next
# navigation. Only ever a process we spawned ourselves, verified by its command
# line before anything is signalled.
_last_window: dict[str, object] = {"pid": None, "manager": ""}


def _replace_previous(manager: str) -> None:
    pid = _last_window.get("pid")
    if not pid or _last_window.get("manager") != manager:
        return
    try:
        import psutil

        proc = psutil.Process(int(pid))
        if manager not in " ".join(proc.cmdline()).lower():
            return          # pid reused by something else; leave it alone
        proc.terminate()
        proc.wait(timeout=2)
    except Exception:
        pass
    finally:
        _last_window["pid"] = None


def _remember(manager: str, args: list[str]) -> None:
    """Record the window we just opened so the next navigation can replace it."""
    try:
        import psutil
        import time as _t

        _t.sleep(0.35)
        newest = None
        for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            info = proc.info
            if (info.get("name") or "").lower() != manager:
                continue
            if newest is None or info["create_time"] > newest.info["create_time"]:
                newest = proc
        if newest is not None:
            _last_window.update(pid=newest.info["pid"], manager=manager)
    except Exception:
        pass


def _manager() -> tuple[str, str | None]:
    """The file manager to drive, honouring an explicit setting first."""
    configured = str(config.get("filemanager.command", "") or "").strip()
    if configured and shell.have(configured):
        flag = dict(_MANAGERS).get(configured)
        return configured, flag
    for name, flag in _MANAGERS:
        if shell.have(name):
            return name, flag
    return "", None


def _resolve(path: str) -> Path:
    return config.resolve_user_path(path or "~")


@skill(category="files", danger=Danger.MODERATE)
def open_file_manager(path: str = "~", new_window: bool = False) -> str:
    """Open the file manager at a directory.

    Opening a path this way is also how you navigate: pass the subdirectory to
    go into it, or its parent to come back out.

    Args:
        path: Directory to show. Defaults to your home directory.
        new_window: Force a new window instead of reusing an open one.
    """
    manager, _ = _manager()
    if not manager:
        return (
            "No supported file manager found. Tried: "
            + ", ".join(name for name, _ in _MANAGERS)
        )

    target = _resolve(path)
    if not target.exists():
        return f"{target} does not exist."
    if not target.is_dir():
        # Asking to "open" a file in the manager means show it in its folder.
        return show_in_file_manager(str(target))

    args = [manager]
    if new_window and manager in {"dolphin", "nautilus"}:
        args.append("--new-window")
    else:
        # Navigating means moving one window around, not accumulating them.
        _replace_previous(manager)
    args.append(str(target))

    if not shell.spawn(args):
        return f"Could not launch {manager}."
    if not new_window:
        _remember(manager, args)
    return f"Opened {target} in {manager}."


@skill(category="files", danger=Danger.MODERATE)
def show_in_file_manager(path: str) -> str:
    """Reveal a file in the file manager, highlighted in its folder.

    Args:
        path: The file to show.
    """
    manager, select = _manager()
    if not manager:
        return "No supported file manager found."

    target = _resolve(path)
    if not target.exists():
        return f"{target} does not exist."

    if select:
        args = [manager, select, str(target)]
    else:
        # No highlight flag — open the containing folder instead, which is the
        # honest fallback rather than pretending the file was selected.
        args = [manager, str(target.parent)]

    if not shell.spawn(args):
        return f"Could not launch {manager}."
    if select:
        return f"Showing {target.name} in {manager} ({target.parent})."
    return (
        f"Opened {target.parent} in {manager}. It cannot highlight a single "
        f"file, so look for {target.name}."
    )


@skill(category="files", danger=Danger.MODERATE)
def rename_path(path: str, new_name: str, _source: str = "local", _confirm: bool = False) -> str:
    """Rename a file or folder, keeping it in the same directory.

    This changes the file on disk; an open file manager will reflect it.

    Args:
        path: The file or folder to rename.
        new_name: The new name — just the name, not a full path.
    """
    target = _resolve(path)
    if not target.exists():
        return f"{target} does not exist."

    clean = (new_name or "").strip()
    if not clean:
        return "No new name given."
    if "/" in clean:
        return "Give just the new name, not a path. Use move_path to relocate it."

    destination = target.parent / clean
    if destination.exists():
        return f"{destination} already exists — pick another name."

    # Renaming is reversible, but it can still lose track of something the user
    # cares about, so it is checked like any other write.
    verdict = check_path(destination, write=True)
    verdict.danger = max(verdict.danger, Danger.MODERATE)
    verdict.reasons.append(f"renames {target.name} to {clean} in {target.parent}")
    enforce(
        verdict, f"rename {target.name} to {clean}",
        skill="rename_path", source=_source, pre_confirmed=_confirm,
    )

    try:
        target.rename(destination)
    except OSError as exc:
        return f"Could not rename {target.name}: {exc}"
    return f"Renamed {target.name} to {clean} in {target.parent}."


@skill(category="files", danger=Danger.SAFE)
def file_manager_status() -> str:
    """Which file manager Kravein will drive, and how to change it."""
    manager, select = _manager()
    if not manager:
        return (
            "No supported file manager is installed. Tried: "
            + ", ".join(name for name, _ in _MANAGERS)
        )
    highlight = "can highlight a specific file" if select else "opens folders only"
    return (
        f"Using {manager} ({highlight}).\n"
        "Set filemanager.command in settings to choose a different one."
    )
