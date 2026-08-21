"""Launching and managing applications.

Ports ``actions/open_app.py``. The Windows path drove the Start menu with
synthetic keystrokes; here we resolve real ``.desktop`` entries.
"""

from __future__ import annotations

import time

from ..platform import apps as apps_platform
from ..platform import desktop, packages, shell
from ..registry import skill
from ..safety import Danger, Verdict, enforce


@skill(category="apps", danger=Danger.MODERATE)
def open_app(app_name: str) -> str:
    """Launch an application by name. Focuses it if it is already running.

    Args:
        app_name: What to open, e.g. 'firefox', 'terminal', 'file manager'.
    """
    if not app_name.strip():
        return "Which application should I open?"
    return apps_platform.launch_by_name(app_name)


@skill(category="apps", danger=Danger.SAFE)
def search_apps(query: str, limit: int = 8) -> str:
    """Find installed applications matching a name or description.

    Args:
        query: What to search for.
        limit: Maximum results.
    """
    return apps_platform.describe_matches(query, limit=limit)


@skill(category="apps", danger=Danger.SAFE)
def list_apps(category: str = "", limit: int = 60) -> str:
    """List installed applications, optionally filtered by category.

    Args:
        category: A freedesktop category, e.g. 'Development', 'Graphics'.
        limit: Maximum results.
    """
    entries = apps_platform.index()
    if category:
        needle = category.lower()
        entries = [e for e in entries if any(needle in c.lower() for c in e.categories)]

    entries = [e for e in entries if not e.no_display]
    entries.sort(key=lambda e: e.name.lower())

    if not entries:
        return f"No applications found{f' in category {category}' if category else ''}."

    shown = entries[:limit]
    lines = [f"{len(entries)} application(s){f' in {category}' if category else ''}:"]
    lines += [f"  {e.name}" for e in shown]
    if len(entries) > limit:
        lines.append(f"  … and {len(entries) - limit} more")
    return "\n".join(lines)


@skill(category="apps", danger=Danger.DESTRUCTIVE)
def close_app(
    app_name: str,
    force: bool = False,
    _source: str = "local",
    _confirm: bool = False,
) -> str:
    """Close an application's windows.

    By default this asks the window manager to close the window, so the app
    can prompt to save work. Use force to kill the client outright when it
    ignores a polite close.

    Args:
        app_name: Which application's windows to close.
        force: Kill the client instead of asking it to close.
    """
    matches = desktop.find_windows(app_name)
    if not matches:
        return f"No open window matches '{app_name}'."

    verb = "kill" if force else "close"
    verdict = Verdict(
        danger=Danger.DESTRUCTIVE,
        reasons=[f"{verb}s {len(matches)} window(s) of '{app_name}'"]
        + (["forced — the app cannot save first"] if force else []),
    )
    enforce(verdict, f"{verb} {len(matches)} window(s) of '{app_name}'",
            skill="close_app", source=_source, pre_confirmed=_confirm)

    backend = desktop.backend()
    targets = list(matches)
    accepted = []
    for win in targets:
        ok = backend.kill(win) if force else backend.close(win)
        if ok:
            accepted.append(win)

    # The compositor accepting a close request is not the same as the window
    # going away, so report what actually happened rather than assuming.
    time.sleep(0.9)
    remaining = {w.id for w in backend.list_windows()}
    gone = [w for w in targets if w.id not in remaining]
    survived = [w for w in targets if w.id in remaining]

    if gone:
        names = ", ".join(f"{w.app}: {w.title[:40]}" for w in gone)
        message = f"Closed {len(gone)} window(s): {names}"
        if survived:
            message += (
                f"\n{len(survived)} still open — the app may be asking you to "
                "save. Re-run with force=true to kill it."
            )
        return message

    if accepted:
        return (
            f"The compositor accepted the {verb} request but "
            f"{len(survived)} window(s) are still open. "
            f"'{app_name}' may be showing a save prompt."
            + ("" if force else " Re-run with force=true to kill it.")
        )

    return (
        f"Could not {verb} any window for '{app_name}'. "
        f"Window backend: {backend.name}."
    )


@skill(category="apps", danger=Danger.SAFE)
def search_packages(query: str, limit: int = 12) -> str:
    """Search the Arch repositories for installable packages.

    Args:
        query: What to search for.
        limit: Maximum results.
    """
    return packages.search(query, limit=limit)


@skill(category="apps", danger=Danger.SAFE)
def package_info(package: str) -> str:
    """Show details about a package, installed or not.

    Args:
        package: Package name.
    """
    return packages.info(package)


@skill(category="apps", danger=Danger.SAFE)
def check_updates() -> str:
    """Check for pending system, AUR and Flatpak updates."""
    return packages.check_updates()


@skill(category="apps", danger=Danger.CRITICAL)
def install_package(
    package_names: list[str],
    from_aur: bool = False,
    _source: str = "local",
    _confirm: bool = False,
) -> str:
    """Install packages. Requires confirmation, then hands back the command.

    Installing needs root, which the assistant deliberately does not hold, so
    this returns the exact command for you to run.

    Args:
        package_names: Packages to install.
        from_aur: Use the AUR helper instead of the official repositories.
    """
    if not package_names:
        return "Which packages should I install?"

    verdict = Verdict(
        danger=Danger.CRITICAL,
        reasons=[f"installs {len(package_names)} package(s) system-wide"],
    )
    enforce(verdict, f"install {', '.join(package_names)}",
            skill="install_package", source=_source, pre_confirmed=_confirm)

    return packages.install(package_names, aur=from_aur)


@skill(category="apps", danger=Danger.SAFE)
def which_package_owns(target: str) -> str:
    """Find which package provides a file or command.

    Args:
        target: A command name or absolute file path.
    """
    return packages.which_package(target)


@skill(category="apps", danger=Danger.SAFE)
def list_orphan_packages() -> str:
    """List packages nothing depends on any more."""
    return packages.orphans()
