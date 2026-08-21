"""Package management.

The Windows build had no equivalent — installing things was manual. Since the
target is Arch, the assistant can resolve its own missing dependencies via
pacman/AUR, which is what makes "install X for me" a real capability.

Installs and removals always require root, so this module never runs sudo
itself: it either uses a passwordless path (checkupdates, queries) or returns
the exact command for the user to run. That keeps the assistant from
half-completing a transaction it cannot authenticate.
"""

from __future__ import annotations

import re

from . import detect, shell


def _pm() -> str:
    return detect.environment().package_manager


def search(query: str, limit: int = 15) -> str:
    """Search the repositories."""
    pm = _pm()
    if pm != "pacman":
        return f"Search is only implemented for pacman (found: {pm})."

    result = shell.run(["pacman", "-Ss", query], timeout=30)
    if not result.ok:
        return f"No packages matched '{query}'."

    lines = result.stdout.splitlines()
    entries: list[str] = []
    for i in range(0, len(lines) - 1, 2):
        header = lines[i].strip()
        desc = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if header:
            entries.append(f"  {header}\n      {desc}")
        if len(entries) >= limit:
            break

    if not entries:
        return f"No packages matched '{query}'."
    return f"Repository matches for '{query}':\n" + "\n".join(entries)


def is_installed(package: str) -> bool:
    """Check a package is installed."""
    if _pm() != "pacman":
        return False
    return shell.run(["pacman", "-Qq", package], timeout=10).ok


def info(package: str) -> str:
    """Show details for an installed or available package."""
    if _pm() != "pacman":
        return "Only pacman is supported."

    local = shell.run(["pacman", "-Qi", package], timeout=15)
    if local.ok:
        return f"[installed]\n{local.stdout.strip()}"

    remote = shell.run(["pacman", "-Si", package], timeout=20)
    if remote.ok:
        return f"[not installed]\n{remote.stdout.strip()}"

    return f"Package '{package}' not found."


def list_installed(explicit_only: bool = True, limit: int = 100) -> str:
    """List installed packages."""
    if _pm() != "pacman":
        return "Only pacman is supported."
    args = ["pacman", "-Qe" if explicit_only else "-Q"]
    result = shell.run(args, timeout=20)
    if not result.ok:
        return f"Could not list packages: {result.output}"

    lines = result.stdout.splitlines()
    scope = "explicitly installed" if explicit_only else "installed"
    head = "\n".join(f"  {l}" for l in lines[:limit])
    more = f"\n  … and {len(lines) - limit} more" if len(lines) > limit else ""
    return f"{len(lines)} {scope} package(s):\n{head}{more}"


def check_updates() -> str:
    """Check for pending updates without touching the sync database.

    ``checkupdates`` (pacman-contrib) is the safe way to do this — a plain
    ``pacman -Sy`` would leave the system in a partial-upgrade state.
    """
    pm = _pm()
    if pm != "pacman":
        return f"Update checking is only implemented for pacman (found: {pm})."

    if not shell.have("checkupdates"):
        return (
            "checkupdates is not installed — it is the only safe way to check "
            "for updates without a partial sync.\n"
            "Install it with:  sudo pacman -S pacman-contrib"
        )

    result = shell.run(["checkupdates"], timeout=90)
    # checkupdates exits 2 when there is nothing to do.
    if result.returncode == 2 or not result.stdout.strip():
        repo_line = "No repository updates available."
    else:
        updates = result.stdout.strip().splitlines()
        listed = "\n".join(f"  {u}" for u in updates[:40])
        more = f"\n  … and {len(updates) - 40} more" if len(updates) > 40 else ""
        repo_line = f"{len(updates)} repository update(s):\n{listed}{more}"

    aur_line = ""
    helper = detect.environment().aur_helper
    if helper:
        aur = shell.run([helper, "-Qua"], timeout=120)
        if aur.ok and aur.stdout.strip():
            aur_updates = aur.stdout.strip().splitlines()
            aur_line = f"\n\n{len(aur_updates)} AUR update(s):\n" + "\n".join(
                f"  {u}" for u in aur_updates[:20]
            )

    flat_line = ""
    if detect.environment().has_flatpak:
        flat = shell.run(["flatpak", "remote-ls", "--updates"], timeout=90)
        if flat.ok and flat.stdout.strip():
            count = len(flat.stdout.strip().splitlines())
            flat_line = f"\n\n{count} Flatpak update(s) available."

    return repo_line + aur_line + flat_line


def install_command(packages: list[str], aur: bool = False) -> str:
    """Build the command that installs the given packages."""
    pm = _pm()
    names = " ".join(packages)
    if pm != "pacman":
        return f"# unsupported package manager: {pm}"

    helper = detect.environment().aur_helper
    if aur and helper:
        return f"{helper} -S --needed {names}"
    return f"sudo pacman -S --needed {names}"


def install(packages: list[str], aur: bool = False) -> str:
    """Attempt an install, or hand back the command if root is needed.

    An AUR helper runs as the normal user and prompts for its own sudo
    password interactively, so we do not try to drive it non-interactively.
    """
    if not packages:
        return "No packages given."
    if _pm() != "pacman":
        return f"Only pacman is supported (found: {_pm()})."

    already = [p for p in packages if is_installed(p)]
    todo = [p for p in packages if p not in already]

    if not todo:
        return f"Already installed: {', '.join(already)}."

    command = install_command(todo, aur=aur)
    note = f"Already installed: {', '.join(already)}.\n" if already else ""

    return (
        f"{note}Installing packages needs root, which I cannot supply "
        f"non-interactively.\nRun this yourself:\n\n  {command}\n"
    )


def remove_command(packages: list[str]) -> str:
    return f"sudo pacman -Rns {' '.join(packages)}"


def orphans() -> str:
    """List packages no longer required by anything."""
    if _pm() != "pacman":
        return "Only pacman is supported."
    result = shell.run(["pacman", "-Qtdq"], timeout=20)
    if not result.ok or not result.stdout.strip():
        return "No orphaned packages."
    items = result.stdout.strip().splitlines()
    return (
        f"{len(items)} orphaned package(s):\n"
        + "\n".join(f"  {i}" for i in items)
        + f"\n\nRemove them with:  sudo pacman -Rns {' '.join(items)}"
    )


def cache_size() -> str:
    """How much disk the package cache is using."""
    result = shell.run(["du", "-sh", "/var/cache/pacman/pkg"], timeout=30)
    if not result.ok:
        return "Could not read the package cache size."
    size = result.stdout.split()[0] if result.stdout.split() else "?"
    return (
        f"Package cache: {size}\n"
        "Clean old versions with:  sudo paccache -r"
    )


def which_package(path_or_binary: str) -> str:
    """Find which package owns a file or command."""
    if _pm() != "pacman":
        return "Only pacman is supported."

    target = path_or_binary
    if "/" not in target:
        resolved = shell.which(target)
        if resolved:
            target = resolved

    result = shell.run(["pacman", "-Qo", target], timeout=15)
    if result.ok:
        return result.stdout.strip()

    # Not owned locally — check the files database if it is synced.
    remote = shell.run(["pacman", "-F", target], timeout=30)
    if remote.ok and remote.stdout.strip():
        return remote.stdout.strip()

    return f"No package owns '{path_or_binary}'."


def ensure(binary: str, package: str = "") -> tuple[bool, str]:
    """Check a binary exists, returning an install hint if it does not."""
    if shell.have(binary):
        return True, ""
    pkg = package or binary
    return False, f"'{binary}' is missing. Install it with:  sudo pacman -S {pkg}"
