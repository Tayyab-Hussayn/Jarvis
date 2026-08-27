"""File and directory management.

Ports ``actions/file_controller.py`` and the file half of ``actions/desktop.py``.
Deletes go to the XDG trash (via send2trash) rather than being unlinked, which
is the Linux equivalent of the original's Recycle Bin behaviour.
"""

from __future__ import annotations

import os
import shutil
import stat
from datetime import datetime
from pathlib import Path

from .. import config
from ..platform import apps, shell
from ..registry import skill
from ..safety import (Danger, Verdict, check_delete, check_path,
                      describe_targets, enforce)

FILE_TYPE_MAP = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".heic", ".avif"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".md", ".odt", ".rtf", ".epub"},
    "Spreadsheets": {".xls", ".xlsx", ".csv", ".ods", ".tsv"},
    "Presentations": {".ppt", ".pptx", ".odp"},
    "Videos": {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
    "Music": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".opus", ".m4a"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".zst"},
    "Code": {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".json", ".yaml",
             ".yml", ".toml", ".sh", ".rs", ".go", ".c", ".cpp", ".h", ".java", ".php"},
    "Packages": {".pkg.tar.zst", ".appimage", ".deb", ".rpm", ".flatpakref"},
}


def _human_bytes(value: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def _mode_string(path: Path) -> str:
    try:
        return stat.filemode(path.lstat().st_mode)
    except OSError:
        return "?" * 10


@skill(category="files", danger=Danger.SAFE)
def list_files(
    path: str = "home",
    show_hidden: bool = False,
    long_format: bool = False,
    limit: int = 200,
) -> str:
    """List the contents of a directory.

    Args:
        path: Directory path, or a shortcut like 'home', 'desktop', 'downloads'.
        show_hidden: Include dotfiles.
        long_format: Show permissions, owner and modification time.
        limit: Maximum entries to return.
    """
    target = config.resolve_user_path(path)

    if not target.exists():
        return f"Path not found: {target}"
    if not target.is_dir():
        return describe_file(str(target))

    try:
        entries = sorted(
            target.iterdir(),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
    except PermissionError:
        return f"Permission denied: {target}"
    except OSError as exc:
        return f"Could not read {target}: {exc}"

    if not show_hidden:
        entries = [e for e in entries if not e.name.startswith(".")]

    if not entries:
        return f"{target} is empty."

    shown = entries[:limit]
    lines = []
    for entry in shown:
        try:
            info = entry.lstat()
        except OSError:
            lines.append(f"  ?  {entry.name}")
            continue

        is_dir = entry.is_dir() and not entry.is_symlink()
        icon = "/" if is_dir else ("@" if entry.is_symlink() else "")
        size = "" if is_dir else _human_bytes(info.st_size)

        if long_format:
            mtime = datetime.fromtimestamp(info.st_mtime)
            try:
                import pwd

                owner = pwd.getpwuid(info.st_uid).pw_name
            except (ImportError, KeyError):
                owner = str(info.st_uid)
            lines.append(
                f"  {_mode_string(entry)} {owner:<10} {size:>9}  "
                f"{mtime:%Y-%m-%d %H:%M}  {entry.name}{icon}"
            )
        else:
            lines.append(f"  {entry.name}{icon}" + (f"  ({size})" if size else ""))

    header = f"{target} — {len(entries)} item(s)"
    footer = f"\n  … and {len(entries) - limit} more" if len(entries) > limit else ""
    return f"{header}:\n" + "\n".join(lines) + footer


@skill(category="files", danger=Danger.SAFE)
def describe_file(path: str) -> str:
    """Show metadata for a file or directory: size, permissions, type, dates.

    Args:
        path: The file or directory to describe.
    """
    target = config.resolve_user_path(path)
    if not target.exists():
        return f"Not found: {target}"

    try:
        info = target.lstat()
    except OSError as exc:
        return f"Could not stat {target}: {exc}"

    kind = "directory" if target.is_dir() else "symlink" if target.is_symlink() else "file"
    lines = [
        f"{target}",
        f"  Type       : {kind}",
        f"  Size       : {_human_bytes(info.st_size)} ({info.st_size:,} bytes)",
        f"  Permissions: {_mode_string(target)}",
        f"  Modified   : {datetime.fromtimestamp(info.st_mtime):%Y-%m-%d %H:%M:%S}",
        f"  Accessed   : {datetime.fromtimestamp(info.st_atime):%Y-%m-%d %H:%M:%S}",
    ]

    if target.is_symlink():
        try:
            lines.append(f"  Points to  : {os.readlink(target)}")
        except OSError:
            pass

    if target.is_dir():
        try:
            children = list(target.iterdir())
            files = sum(1 for c in children if c.is_file())
            dirs = sum(1 for c in children if c.is_dir())
            lines.append(f"  Contains   : {files} file(s), {dirs} directory(ies)")
        except (OSError, PermissionError):
            pass
    elif shell.have("file"):
        probe = shell.run(["file", "-b", str(target)], timeout=8)
        if probe.ok:
            lines.append(f"  Detected   : {probe.stdout.strip()}")

    return "\n".join(lines)


@skill(category="files", danger=Danger.SAFE)
def read_file(path: str, max_lines: int = 200, start_line: int = 1) -> str:
    """Read a text file's contents.

    Args:
        path: File to read.
        max_lines: Maximum lines to return.
        start_line: 1-indexed line to start from.
    """
    target = config.resolve_user_path(path)
    if not target.exists():
        return f"Not found: {target}"
    if target.is_dir():
        return f"{target} is a directory. Use list_files instead."

    try:
        if target.stat().st_size > 20 * 1024 * 1024:
            return f"{target} is larger than 20 MB; refusing to read it into memory."
    except OSError as exc:
        return f"Could not stat {target}: {exc}"

    try:
        with target.open("r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except PermissionError:
        return f"Permission denied: {target}"
    except OSError as exc:
        return f"Could not read {target}: {exc}"

    start = max(1, int(start_line))
    chunk = lines[start - 1: start - 1 + max(1, int(max_lines))]
    if not chunk:
        return f"{target} has {len(lines)} line(s); nothing at line {start}."

    body = "".join(chunk)
    suffix = ""
    if start - 1 + len(chunk) < len(lines):
        suffix = f"\n… {len(lines) - (start - 1 + len(chunk))} more line(s)"
    return f"{target} (lines {start}-{start + len(chunk) - 1} of {len(lines)}):\n{body}{suffix}"


@skill(category="files", danger=Danger.MODERATE)
def write_file(
    path: str,
    content: str,
    append: bool = False,
    _source: str = "local",
    _confirm: bool = False,
) -> str:
    """Create or overwrite a text file.

    Args:
        path: Destination file.
        content: Text to write.
        append: Append instead of replacing the file.
    """
    target = config.resolve_user_path(path)
    verdict = check_path(target, write=True)

    # Silently clobbering an existing file is destructive; creating is not.
    if target.exists() and not append:
        verdict.danger = max(verdict.danger, Danger.DESTRUCTIVE)
        verdict.reasons.append(f"overwrites the existing file {target}")

    enforce(
        verdict,
        f"{'append to' if append else 'write'} {target}",
        skill="write_file", source=_source, pre_confirmed=_confirm,
    )

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a" if append else "w", encoding="utf-8") as handle:
            handle.write(content)
    except PermissionError:
        return f"Permission denied: {target}"
    except OSError as exc:
        return f"Could not write {target}: {exc}"

    action = "Appended to" if append else "Wrote"
    return f"{action} {target} ({len(content):,} chars)."


@skill(category="files", danger=Danger.MODERATE)
def create_directory(path: str) -> str:
    """Create a directory, including any missing parents.

    Args:
        path: Directory to create.
    """
    target = config.resolve_user_path(path)
    enforce(check_path(target, write=True), f"create directory {target}",
            skill="create_directory")

    try:
        target.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        return f"Permission denied: {target}"
    except OSError as exc:
        return f"Could not create {target}: {exc}"
    return f"Directory ready: {target}"


@skill(category="files", danger=Danger.DESTRUCTIVE)
def delete_path(
    path: str,
    permanent: bool = False,
    _source: str = "local",
    _confirm: bool = False,
) -> str:
    """Delete a file or directory. Moves it to the trash unless told otherwise.

    Args:
        path: What to delete.
        permanent: Bypass the trash and remove it irrecoverably.
    """
    target = config.resolve_user_path(path)
    if not target.exists() and not target.is_symlink():
        return f"Not found: {target}"

    verdict = check_delete(target)
    if permanent:
        verdict.danger = Danger.CRITICAL
        verdict.reasons.append("permanent — bypasses the trash, cannot be undone")

    enforce(
        verdict,
        f"{'permanently delete' if permanent else 'move to trash'} {target}",
        skill="delete_path", source=_source, pre_confirmed=_confirm,
    )

    if not permanent:
        try:
            import send2trash

            send2trash.send2trash(str(target))
            return f"Moved to trash: {target}"
        except ImportError:
            return (
                f"send2trash is not installed, so {target} cannot be trashed safely.\n"
                "Install it (pip install send2trash), or re-run with permanent=true."
            )
        except Exception as exc:
            # Filesystems outside $HOME (tmpfs, external mounts) often have no
            # .Trash directory, and the spec says not to create one silently.
            return (
                f"Could not move {target} to the trash: {exc}\n"
                f"That usually means {target} is on a filesystem with no trash "
                "directory (tmpfs, an external drive, or another mount).\n"
                "Re-run with permanent=true to delete it outright."
            )

    try:
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
    except PermissionError:
        return f"Permission denied: {target}"
    except OSError as exc:
        return f"Could not delete {target}: {exc}"

    return f"Permanently deleted: {target}"


@skill(category="files", danger=Danger.DESTRUCTIVE)
def move_path(
    source: str,
    destination: str,
    _source: str = "local",
    _confirm: bool = False,
) -> str:
    """Move or rename a file or directory.

    Args:
        source: What to move.
        destination: Where to move it. A directory keeps the original name.
    """
    src = config.resolve_user_path(source)
    dst = config.resolve_user_path(destination)

    if not src.exists() and not src.is_symlink():
        return f"Source not found: {src}"

    if dst.is_dir():
        dst = dst / src.name

    verdict = check_path(dst, write=True)
    verdict.danger = max(verdict.danger, Danger.DESTRUCTIVE)
    verdict.reasons.append(f"moves {src} to {dst}")
    if dst.exists():
        verdict.danger = Danger.CRITICAL
        verdict.reasons.append(f"{dst} already exists and would be replaced")

    enforce(verdict, f"move {src} to {dst}",
            skill="move_path", source=_source, pre_confirmed=_confirm)

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    except PermissionError:
        return f"Permission denied moving {src}"
    except OSError as exc:
        return f"Could not move {src}: {exc}"
    return f"Moved {src} → {dst}"


@skill(category="files", danger=Danger.MODERATE)
def copy_path(source: str, destination: str, _source: str = "local", _confirm: bool = False) -> str:
    """Copy a file or directory.

    Args:
        source: What to copy.
        destination: Where to copy it.
    """
    src = config.resolve_user_path(source)
    dst = config.resolve_user_path(destination)

    if not src.exists():
        return f"Source not found: {src}"
    if dst.is_dir():
        dst = dst / src.name

    verdict = check_path(dst, write=True)
    if dst.exists():
        verdict.danger = max(verdict.danger, Danger.DESTRUCTIVE)
        verdict.reasons.append(f"{dst} already exists and would be overwritten")

    enforce(verdict, f"copy {src} to {dst}",
            skill="copy_path", source=_source, pre_confirmed=_confirm)

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
    except OSError as exc:
        return f"Could not copy {src}: {exc}"
    return f"Copied {src} → {dst}"


@skill(category="files", danger=Danger.SAFE)
def find_files(
    name: str,
    path: str = "home",
    max_results: int = 50,
    content: str = "",
) -> str:
    """Search for files by name, and optionally by their contents.

    Args:
        name: Filename or glob pattern, e.g. '*.pdf' or 'report'.
        path: Directory to search under.
        max_results: Maximum matches to return.
        content: Also require this text to appear inside the file.
    """
    root = config.resolve_user_path(path)
    if not root.is_dir():
        return f"Not a directory: {root}"

    pattern = name if any(c in name for c in "*?[") else f"*{name}*"
    max_results = max(1, min(int(max_results), 500))

    # fd is dramatically faster on big trees; fall back to pathlib.
    matches: list[Path] = []
    if shell.have("fd"):
        result = shell.run(
            ["fd", "--hidden", "--no-ignore", "--glob", pattern, "--", str(root)],
            timeout=60,
        )
        if result.ok:
            matches = [Path(l) for l in result.stdout.splitlines() if l.strip()]
    if not matches:
        try:
            for found in root.rglob(pattern):
                matches.append(found)
                if len(matches) >= max_results * 4:
                    break
        except (OSError, PermissionError):
            pass

    if content:
        filtered = []
        needle = content.lower()
        for candidate in matches:
            if not candidate.is_file():
                continue
            try:
                if candidate.stat().st_size > 5 * 1024 * 1024:
                    continue
                text = candidate.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if needle in text.lower():
                filtered.append(candidate)
            if len(filtered) >= max_results:
                break
        matches = filtered

    matches = matches[:max_results]
    if not matches:
        where = f" containing '{content}'" if content else ""
        return f"No files matching '{name}'{where} under {root}."

    lines = [f"{len(matches)} match(es) for '{name}' under {root}:"]
    for match in matches:
        try:
            size = _human_bytes(match.stat().st_size) if match.is_file() else "dir"
        except OSError:
            size = "?"
        lines.append(f"  {match}  ({size})")
    return "\n".join(lines)


@skill(category="files", danger=Danger.SAFE)
def directory_size(path: str = "home", top: int = 10) -> str:
    """Show what is taking up space in a directory.

    Args:
        path: Directory to measure.
        top: How many of the largest entries to list.
    """
    root = config.resolve_user_path(path)
    if not root.is_dir():
        return f"Not a directory: {root}"

    if shell.have("du"):
        result = shell.run(
            ["du", "-sh", "--", *[str(p) for p in sorted(root.iterdir())[:200]]],
            timeout=120,
        )
        if result.ok and result.stdout.strip():
            rows = []
            for line in result.stdout.splitlines():
                parts = line.split("\t", 1)
                if len(parts) == 2:
                    rows.append((parts[0].strip(), parts[1].strip()))

            def to_bytes(text: str) -> float:
                units = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
                try:
                    if text[-1] in units:
                        return float(text[:-1]) * units[text[-1]]
                    return float(text)
                except (ValueError, IndexError):
                    return 0.0

            rows.sort(key=lambda r: -to_bytes(r[0]))
            listed = "\n".join(f"  {size:>8}  {Path(name).name}" for size, name in rows[:top])
            return f"Largest entries in {root}:\n{listed}"

    return f"Could not measure {root} (du unavailable)."


@skill(category="files", danger=Danger.DESTRUCTIVE)
def organize_directory(
    path: str = "downloads",
    mode: str = "by_type",
    _source: str = "local",
    _confirm: bool = False,
) -> str:
    """Sort loose files in a directory into subfolders.

    Args:
        path: Directory to organise.
        mode: 'by_type' groups by file kind; 'by_date' groups by year-month.
    """
    root = config.resolve_user_path(path)
    if not root.is_dir():
        return f"Not a directory: {root}"

    try:
        loose = [
            p for p in root.iterdir()
            if p.is_file() and not p.name.startswith(".")
        ]
    except (OSError, PermissionError) as exc:
        return f"Could not read {root}: {exc}"

    if not loose:
        return f"{root} has no loose files to organise."

    verdict = Verdict(
        danger=Danger.DESTRUCTIVE,
        reasons=[f"moves {len(loose)} file(s) into subfolders of {root}"],
    )
    enforce(verdict, f"organise {len(loose)} file(s) in {root} ({mode})",
            skill="organize_directory", source=_source, pre_confirmed=_confirm)

    moved, skipped = [], []
    for item in loose:
        if mode == "by_date":
            folder = datetime.fromtimestamp(item.stat().st_mtime).strftime("%Y-%m")
        else:
            suffix = item.suffix.lower()
            folder = "Others"
            for category, extensions in FILE_TYPE_MAP.items():
                if suffix in extensions:
                    folder = category
                    break

        destination = root / folder
        destination.mkdir(exist_ok=True)
        new_path = destination / item.name

        if new_path.exists():
            skipped.append(item.name)
            continue
        try:
            shutil.move(str(item), str(new_path))
            moved.append(f"{item.name} → {folder}/")
        except OSError as exc:
            skipped.append(f"{item.name} ({exc})")

    out = [f"Organised {root} ({mode}): {len(moved)} file(s) moved."]
    out += [f"  {m}" for m in moved[:15]]
    if len(moved) > 15:
        out.append(f"  … and {len(moved) - 15} more")
    if skipped:
        out.append(f"{len(skipped)} skipped (name conflict or error).")
    return "\n".join(out)


@skill(category="files", danger=Danger.MODERATE, requires=("xdg-open",))
def open_path(path: str) -> str:
    """Open a file, folder or URL in the default application.

    Args:
        path: What to open.
    """
    target = config.resolve_user_path(path) if not path.startswith(("http://", "https://")) else Path(path)
    return apps.open_uri(str(target))
