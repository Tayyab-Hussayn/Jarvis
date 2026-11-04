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
from typing import Literal

from .. import config
from ..platform import apps, shell
from .. import safety
from ..agents import checkpoints
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


_LINE_CLIP = 2000


def _split_lines(text: str) -> list[str]:
    """Split on \\n only. str.splitlines() also splits on form feed, \\x85,
    U+2028..., so its numbers disagree with rg, editors and edit_file."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [l[:-1] if l.endswith("\r") else l for l in lines]


def _tool_state(context) -> dict | None:
    """The calling agent's per-run scratch dict, or None outside an agent.

    CLI ``kravein run`` has no agent, so the read-before-edit guard must not
    apply there; an agent without ``tool_state`` is treated the
    same way rather than blocking every edit.
    """
    state = getattr(context, "tool_state", None)
    return state if isinstance(state, dict) else None


def _stamp(target: Path) -> tuple[int, int] | None:
    try:
        st = target.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _fold_source(verdict: Verdict, src: Path, *, moves: bool) -> None:
    """Hold the *source* of a copy or move to the read rules too (#223 review).

    `read_file` refuses Kravein's own config and asks for secrets, but
    `copy_path` only looked at its destination: copying `api_keys.json` into the
    workspace and reading the copy got around it, and `move_path` could carry
    the owner's `KRAVEIN.md` out of the private tree. A copy reads the source's
    content and a move also removes it, so both go through `check_read`, and a
    move through `check_path(write=True)` as well.
    """
    extra = [safety.check_read(src)]
    if moves:
        extra.append(check_path(src, write=True))
    for other in extra:
        if other.blocked and not verdict.blocked:
            verdict.blocked, verdict.block_reason = True, other.block_reason
            verdict.danger = Danger.CRITICAL
        verdict.danger = max(verdict.danger, other.danger)
        verdict.reasons += [r for r in other.reasons if r not in verdict.reasons]
        verdict.secret_read = verdict.secret_read or other.secret_read


def _record_read(context, target: Path) -> None:
    state = _tool_state(context)
    stamp = _stamp(target)
    if state is not None and stamp is not None:
        state.setdefault("files_read", {})[str(target)] = stamp


def _require_fresh_read(context, target: Path) -> str | None:
    """Refusal text when an agent edits a file it has not (freshly) read.

    Editing from a stale picture of the file is how an agent overwrites a
    change the owner made in between; the stamp is (mtime_ns, size).
    """
    state = _tool_state(context)
    if state is None:
        return None
    seen = state.get("files_read", {}).get(str(target))
    if seen is None:
        return f"Refused: Read {target} first (read_file), then edit it. Nothing changed."
    if tuple(seen) != _stamp(target):
        return (f"Refused: {target} changed since you read it; read it again "
                "(read_file) before editing. Nothing changed.")
    return None


@skill(category="files", danger=Danger.SAFE)
def read_file(path: str, max_lines: int = 200, start_line: int = 1,
              _source: str = "local", _confirm: bool = False, _context=None) -> str:
    """Read a text file; every line is prefixed with its line number.

    Read a file with this before editing it with edit_file or overwriting it
    with write_file. For a long file read it in pieces with start_line.

    Args:
        path: File to read.
        max_lines: Maximum lines to return.
        start_line: 1-indexed line to start from; use the value the previous
            read printed after 'continue with start_line='.
    """
    target = safety.resolve_target(config.resolve_user_path(path))
    if not target.exists():
        return f"Not found: {target}"
    if target.is_dir():
        return f"{target} is a directory. Use list_files instead."

    # Called directly, never through getattr: a guard that silently vanishes
    # when the function is missing would fail open. A secret read asks every
    # time; Kravein's own config/state/data is refused outright.
    enforce(safety.check_read(target), f"read {target}", skill="read_file",
            source=_source, pre_confirmed=_confirm,
            targets=describe_targets(f"file:{target}"))

    try:
        if target.stat().st_size > 20 * 1024 * 1024:
            return f"{target} is larger than 20 MB; refusing to read it into memory."
        stamp_before = _stamp(target)
        with target.open("r", encoding="utf-8", errors="replace") as handle:
            lines = _split_lines(handle.read())
    except PermissionError:
        return f"Permission denied: {target}"
    except OSError as exc:
        return f"Could not read {target}: {exc}"

    start = max(1, int(start_line))
    end = start - 1 + max(1, int(max_lines))
    chunk = lines[start - 1: end]
    if not chunk:
        return f"{target} has {len(lines)} line(s); nothing at line {start}."

    width = len(str(start + len(chunk) - 1))
    body = []
    for number, line in enumerate(chunk, start):
        if len(line) > _LINE_CLIP:
            line = line[:_LINE_CLIP] + f"… [line cut, {len(line) - _LINE_CLIP} more chars]"
        body.append(f"{number:>{width}}\t{line}")
    suffix = ""
    remaining = len(lines) - (start - 1 + len(chunk))
    if remaining > 0:
        suffix = (f"\n… {remaining} more line(s); continue with "
                  f"start_line={start + len(chunk)}")
    # Recorded only after a successful read, with the stamp taken *before* the
    # read: a write that lands mid-read then makes the record stale, not fresh.
    state = _tool_state(_context)
    if state is not None and stamp_before is not None:
        state.setdefault("files_read", {})[str(target)] = stamp_before
    return (f"{target} (lines {start}-{start + len(chunk) - 1} of {len(lines)}):\n"
            + "\n".join(body) + suffix)


@skill(category="files", danger=Danger.MODERATE)
def write_file(
    path: str,
    content: str,
    append: bool = False,
    _source: str = "local",
    _confirm: bool = False,
    _context=None,
) -> str:
    """Create or overwrite a text file.

    Overwriting an existing file needs a read_file of it first; prefer
    edit_file for changing part of a file.

    Args:
        path: Destination file.
        content: Text to write.
        append: Append instead of replacing the file.
    """
    target = config.resolve_user_path(path)
    verdict = check_path(target, write=True)

    if target.is_file() and not append:
        stale = _require_fresh_read(_context, safety.resolve_target(target))
        if stale:
            return stale

    # Silently clobbering an existing file is destructive; creating is not.
    if target.exists() and not append:
        verdict.danger = max(verdict.danger, Danger.DESTRUCTIVE)
        verdict.reasons.append(f"overwrites the existing file {target}")

    enforce(
        verdict,
        f"{'append to' if append else 'write'} {target}",
        skill="write_file", source=_source, pre_confirmed=_confirm,
        targets=describe_targets(f"file:{target}"),
    )

    try:
        # Snapshot first, so undo_changes can put the old content (or "no such
        # file") back; a checkpoint problem is reported, never a blocker.
        with checkpoints.track(_context, target, kind="write") as ck:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a" if append else "w", encoding="utf-8") as handle:
                handle.write(content)
    except PermissionError:
        return f"Permission denied: {target}"
    except OSError as exc:
        return f"Could not write {target}: {exc}"

    if not append:
        _record_read(_context, safety.resolve_target(target))
    action = "Appended to" if append else "Wrote"
    return f"{action} {target} ({len(content):,} chars)." + ck.notice


_EDIT_MAX_BYTES = 5 * 1024 * 1024


def _clip(text: str, limit: int = 80) -> str:
    one = text.replace("\n", "\\n")
    return repr(one if len(one) <= limit else one[:limit] + "…")


_DIFF_MAX_LINES = 60
_DIFF_MAX_CHARS = 4000


def _capped_diff(before: str, after: str, name: str) -> str:
    import difflib
    a, b = _split_lines(before), _split_lines(after)
    # autojunk=False: unified_diff's default treats any line seen in >1% of a
    # 200+ line file as junk, which turns a two-word edit into a whole-file diff.
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    diff = [f"--- a/{name}", f"+++ b/{name}"]
    for group in matcher.get_grouped_opcodes(2):
        first, last = group[0], group[-1]
        diff.append(f"@@ -{first[1] + 1},{last[2] - first[1]} "
                    f"+{first[3] + 1},{last[4] - first[3]} @@")
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                diff += [" " + l for l in a[i1:i2]]
                continue
            diff += ["-" + l for l in a[i1:i2]]
            diff += ["+" + l for l in b[j1:j2]]
    text = "\n".join(l if len(l) <= 200 else l[:200] + "…" for l in diff[:_DIFF_MAX_LINES])
    cut = len(diff) > _DIFF_MAX_LINES
    if len(text) > _DIFF_MAX_CHARS:
        text, cut = text[:_DIFF_MAX_CHARS], True
    if cut:
        text += f"\n… diff truncated ({len(diff)} lines in total)"
    return text


def _match_lines(text: str, needle: str, limit: int = 5) -> list[int]:
    out, start = [], 0
    while len(out) < limit:
        idx = text.find(needle, start)
        if idx < 0:
            break
        out.append(text.count("\n", 0, idx) + 1)
        start = idx + len(needle)
    return out


@skill(category="files", danger=Danger.MODERATE)
def edit_file(
    path: str,
    old_text: str = "",
    new_text: str = "",
    replace_all: bool = False,
    edits: list[dict] | None = None,
    _source: str = "local",
    _confirm: bool = False,
    _context=None,
) -> str:
    """Replace exact text inside an existing text file and show the diff.

    Use this to change part of a file; use write_file only for new files or
    full rewrites. You must read_file the file first, and read it again if it
    changed since. An edit is refused when old_text is not found or appears
    more than once (unless replace_all is true) - include more surrounding
    lines to make it unique. For several changes to one file pass edits: they
    are applied in order and either all succeed or nothing is written.

    Args:
        path: The file to edit.
        old_text: Exact text to replace, including whitespace and newlines.
            Leave empty when you pass edits.
        new_text: Replacement text (may be empty to delete old_text).
        replace_all: Replace every occurrence of old_text instead of requiring
            it to be unique.
        edits: Several changes in order, each an object with old_text, new_text
            and optionally replace_all. Use instead of old_text/new_text.
    """
    if edits:
        if old_text or new_text:
            return "Refused: pass either old_text/new_text or edits, not both."
        steps = []
        for i, item in enumerate(edits, 1):
            if not isinstance(item, dict) or "old_text" not in item:
                return f"Refused: edit {i} of {len(edits)} needs an old_text. Nothing changed."
            steps.append((str(item["old_text"]), str(item.get("new_text", "")),
                          bool(item.get("replace_all", False))))
    else:
        steps = [(old_text, new_text, bool(replace_all))]
    multi = len(steps) > 1

    def fail(i: int, msg: str) -> str:
        who = f"edit {i} of {len(steps)}: " if multi else ""
        return f"Refused: {who}{msg} Nothing changed."

    for i, (old, new, _) in enumerate(steps, 1):
        if not old:
            return fail(i, "old_text is empty; say which text to replace.")
        if old == new:
            return (f"Nothing to do: old_text and new_text are identical." if not multi
                    else fail(i, "old_text and new_text are identical."))

    # Work on the real file. A symlinked path (stow-managed dotfiles) must keep
    # its link, and the path checked by safety must be the one written;
    # os.replace on the link itself would swap it for a regular file.
    target = safety.resolve_target(config.resolve_user_path(path))
    verdict = check_path(target, write=True)
    if verdict.blocked:
        # Refuse before reading: protected files are not even opened.
        enforce(verdict, f"edit {target}", skill="edit_file", source=_source,
                pre_confirmed=_confirm, targets=describe_targets(f"file:{target}"))

    if not target.is_file():
        return f"Not a file: {target}"
    stale = _require_fresh_read(_context, target)
    if stale:
        return stale
    try:
        if target.stat().st_size > _EDIT_MAX_BYTES:
            return f"Refused: {target} is larger than {_EDIT_MAX_BYTES // 1024 // 1024} MB."
        # newline="" keeps CRLF files CRLF; a text-mode round trip would
        # silently rewrite every line ending in the file.
        with target.open("r", encoding="utf-8", newline="") as handle:
            text = handle.read()
    except UnicodeDecodeError:
        return f"Refused: {target} is not UTF-8 text."
    except PermissionError:
        return f"Permission denied: {target}"
    except OSError as exc:
        return f"Could not read {target}: {exc}"

    # All edits run on an in-memory copy; the file is touched only if every
    # one applies, so a failure half-way cannot leave a half-edited file.
    updated = text
    replaced_chars = 0
    replacements = 0
    first_line = None
    for i, (old, new, every) in enumerate(steps, 1):
        count = updated.count(old)
        if count == 0:
            return fail(i, f"old_text was not found in {target}.")
        if count > 1 and not every:
            # Never take the first match: the model may mean another one.
            lines = _match_lines(updated, old)
            return fail(i, (
                f"old_text matches {count} places in {target} (lines "
                f"{', '.join(map(str, lines))}{'…' if count > len(lines) else ''}). "
                "Include more surrounding text so it matches exactly once, "
                "or set replace_all."))
        if first_line is None:
            first_line = updated.count("\n", 0, updated.find(old)) + 1
        n = count if every else 1
        updated = updated.replace(old, new) if every else updated.replace(old, new, 1)
        replaced_chars += len(old) * n
        replacements += n

    diff = _capped_diff(text, updated, target.name)
    # Replacing most of the file is a rewrite, and an overwrite of an existing
    # file is DESTRUCTIVE for write_file; edit_file must not be the quiet way
    # around that confirmation.
    if replaced_chars * 2 >= len(text):
        verdict.danger = max(verdict.danger, Danger.DESTRUCTIVE)
        verdict.reasons.append(
            f"replaces {replaced_chars * 100 // max(len(text), 1)}% of {target.name}"
        )
    # The prompt is the owner's only look at the change: show where and what.
    enforce(
        verdict,
        f"edit {target} at line {first_line} ({replacements} replacement(s)):\n{diff}",
        skill="edit_file", source=_source, pre_confirmed=_confirm,
        targets=describe_targets(f"file:{target}"),
    )

    tmp = target.with_name(f".{target.name}.kravein-edit")
    try:
        with checkpoints.track(_context, target, kind="edit") as ck:
            with tmp.open("w", encoding="utf-8", newline="") as handle:
                handle.write(updated)
            shutil.copymode(target, tmp)
            # Atomic swap: a crash mid-write must not leave a half-written file.
            os.replace(tmp, target)
    except PermissionError:
        tmp.unlink(missing_ok=True)
        return f"Permission denied: {target}"
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        return f"Could not write {target}: {exc}"
    # Our own write changed mtime; refresh so a follow-up edit needs no re-read.
    _record_read(_context, target)
    return (f"Edited {target} at line {first_line} ({replacements} replacement(s), "
            f"{len(updated) - len(text):+,} chars).\n{diff}" + ck.notice)


_SEARCH_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
                     ".mypy_cache", ".pytest_cache", "dist", "build", ".next"}
_SEARCH_MAX_FILE = 2 * 1024 * 1024
_SECRET_NAMES = ("*.pem", "*.key", "*.p12", "*.pfx", "id_*", "*api_keys*",
                 ".env", ".env.*", ".netrc", "*credentials*", "*secret*")


def _looks_secret(name: str) -> bool:
    import fnmatch
    low = name.lower()
    return any(fnmatch.fnmatch(low, pat) for pat in _SECRET_NAMES)


_SEARCH_MAX_MATCHES = 20000  # scan ceiling, so one query cannot read a whole disk into memory


def _search_key(root: Path, path: Path) -> tuple:
    """Stable order for results: a directory's files first, then its subdirs.

    rg searches in parallel and returns files in arbitrary order; sorting both
    engines by this key is what makes their output identical.
    """
    parts = path.relative_to(root).parts
    return tuple((1, part) for part in parts[:-1]) + ((0, parts[-1]),)


def _search_dir_ok(root: Path, path: Path) -> bool:
    """False when any directory between root and path is skipped for search."""
    cur = root
    for part in path.relative_to(root).parts[:-1]:
        cur = cur / part
        if part in _SEARCH_SKIP_DIRS or part.startswith(".") or safety.sensitive_kind(cur):
            return False
    return True


def _search_file_state(path: Path) -> str:
    """'ok', 'skip' (silently) or 'blocked' (skipped and counted in a note)."""
    # Hidden files: rg skips them by default; the fallback must too or the two
    # engines return different results.
    if path.name.startswith(".") or _looks_secret(path.name) or safety.sensitive_kind(path):
        return "skip"
    try:
        if path.is_symlink() or path.stat().st_size > _SEARCH_MAX_FILE:
            return "skip"
    except OSError:
        return "skip"
    verdict = safety.check_read(path)
    if verdict.blocked or verdict.danger > Danger.SAFE:
        return "blocked"
    return "ok"


def _python_search(root: Path, glob: str, matcher) -> dict[Path, list[tuple[int, str]]]:
    import fnmatch
    found: dict[Path, list[tuple[int, str]]] = {}
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune in place: skipping vendored/build trees is what keeps this
        # fast and keeps results about the user's own files.
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in _SEARCH_SKIP_DIRS and not d.startswith(".")
            and not safety.sensitive_kind(Path(dirpath) / d))
        for fname in sorted(filenames):
            if glob and not fnmatch.fnmatch(fname, glob):
                continue
            fpath = Path(dirpath) / fname
            try:
                with fpath.open("rb") as handle:
                    raw = handle.read(_SEARCH_MAX_FILE + 1)
            except OSError:
                continue
            if b"\0" in raw[:4096]:  # binary
                continue
            hits = [(n, line) for n, line in
                    enumerate(_split_lines(raw.decode("utf-8", "ignore")), 1)
                    if matcher.search(line)]
            if hits:
                found[fpath] = hits
                total += len(hits)
            if total >= _SEARCH_MAX_MATCHES:
                return found
    return found


def _rg_search(root: Path, glob: str, query: str, regex: bool):
    """ripgrep matches as {path: [(line, text)]}, or None to use the fallback."""
    import json
    cmd = ["rg", "--json", "--no-ignore", "--max-filesize", "2M", "-i"]
    if glob:
        cmd += ["-g", glob]
    for d in sorted(_SEARCH_SKIP_DIRS):
        cmd += ["-g", f"!{d}"]
    cmd += (["-e", query] if regex else ["-F", "-e", query]) + ["--", str(root)]
    result = shell.run(cmd, timeout=60)
    # rc 1 = no matches. Anything else (rc 2: a regex Rust rejects but Python
    # accepts, unreadable dir...) falls back rather than reporting nothing.
    if result.returncode not in (0, 1) or result.timed_out:
        return None
    found: dict[Path, list[tuple[int, str]]] = {}
    for raw in result.stdout.splitlines():
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if event.get("type") != "match":
            continue
        data = event["data"]
        if "text" not in data["path"] or "text" not in data["lines"]:
            continue
        found.setdefault(Path(data["path"]["text"]), []).append(
            (data["line_number"], data["lines"]["text"].rstrip("\r\n")))
    return found


def _clip_line(line: str) -> str:
    shown = line.strip()
    return shown[:200] + "…" if len(shown) > 200 else shown


@skill(category="files")
def search_text(
    query: str,
    directory: str = "home",
    glob: str = "",
    max_results: int = 40,
    regex: bool = False,
    context_lines: int = 0,
    output: Literal["content", "files", "count"] = "content",
    offset: int = 0,
) -> str:
    """Search inside file contents under a directory (like grep -rn / rg).

    Output is capped and paged; narrow it with glob or a more specific
    directory, or continue with offset. To find files by name use find_files.

    Args:
        query: Text to look for (case-insensitive). Plain text unless regex is true.
        directory: Directory to search under.
        glob: Only search files whose name matches, e.g. '*.py'.
        max_results: Maximum results per page (1-200): matching lines, or files
            for the files and count outputs.
        regex: Treat query as a regular expression.
        context_lines: Lines of context to show around each match (0-10).
        output: 'content' lists matching lines (default), 'files' lists only the
            files that match, 'count' gives the number of matches per file.
        offset: Skip this many results first; use the value the previous page
            printed after 'continue with offset='.
    """
    import re

    if not query:
        return "Give some text to search for."
    root = config.resolve_user_path(directory)
    if not root.is_dir():
        return f"Not a directory: {root}"
    # Results go to the model and from there to a cloud LLM, so secrets never
    # enter the search: not as a root, not found beneath a wider root.
    if safety.sensitive_kind(root):
        return (f"Refused: {root} holds keys or Kravein's own secrets and is "
                "not searched.")
    if output not in ("content", "files", "count"):
        return "output must be 'content', 'files' or 'count'."
    try:
        matcher = re.compile(query if regex else re.escape(query), re.IGNORECASE)
    except re.error as exc:
        return f"Invalid regex {query!r}: {exc}"

    limit = max(1, min(int(max_results), 200))
    offset = max(0, int(offset))
    context = max(0, min(int(context_lines), 10))

    raw = _rg_search(root, glob, query, regex) if shell.have("rg") else None
    if raw is None:
        raw = _python_search(root, glob, matcher)

    import fnmatch
    found: dict[Path, list[tuple[int, str]]] = {}
    blocked = 0
    for fpath in sorted(raw, key=lambda f: _search_key(root, f)):
        if glob and not fnmatch.fnmatch(fpath.name, glob):
            continue
        if not _search_dir_ok(root, fpath):
            continue
        state = _search_file_state(fpath)
        if state == "blocked":
            blocked += 1
        elif state == "ok":
            found[fpath] = raw[fpath]
    note = (f"\n({blocked} file(s) skipped: blocked or need confirmation to read.)"
            if blocked else "")

    if not found:
        return f"No matches for '{query}' under {root}.{note}"

    if output == "content":
        units = [(f, n, t) for f, hits in found.items() for n, t in hits]
    else:
        units = [(f, len(hits), "") for f, hits in found.items()]
    total = len(units)
    if offset >= total:
        return f"no results at offset {offset} (total {total}).{note}"
    page = units[offset: offset + limit]
    end = offset + len(page)

    if output == "files":
        body = [str(f) for f, _, _ in page]
    elif output == "count":
        body = [f"{f}: {n}" for f, n, _ in page]
    else:
        body = []
        page_matches: dict[Path, set[int]] = {}
        for f, n, _ in page:
            page_matches.setdefault(f, set()).add(n)
        shown: dict[Path, set[int]] = {}
        lines_cache: dict[Path, list[str]] = {}
        last: tuple[Path, int] | None = None
        for fpath, number, text in page:
            if context:
                if fpath not in lines_cache:
                    try:
                        lines_cache[fpath] = _split_lines(fpath.read_text(
                            encoding="utf-8", errors="ignore"))
                    except OSError:
                        lines_cache[fpath] = []
                all_lines = lines_cache[fpath]
                match_nums = page_matches[fpath]
                lo, hi = max(1, number - context), min(len(all_lines), number + context)
                span = list(range(lo, hi + 1))
            else:
                all_lines, match_nums, span = [], set(), [number]
            for ln in span:
                if ln in shown.setdefault(fpath, set()):
                    continue
                shown[fpath].add(ln)
                if last and (last[0] != fpath or ln != last[1] + 1) and context:
                    body.append("--")
                is_match = ln == number or ln in match_nums
                src = text if ln == number else (all_lines[ln - 1] if all_lines else "")
                body.append(f"{fpath}{':' if is_match else '-'}{ln}{':' if is_match else '-'} "
                            f"{_clip_line(src)}")
                last = (fpath, ln)

    unit = "match(es)" if output == "content" else "file(s)"
    tail = ""
    if end < total:
        tail = (f"\n… stopped at {end} of {total} {unit}; continue with offset={end} "
                "or narrow with glob or directory.")
    return "\n".join(body) + tail + note


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
    _context=None,
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

            with checkpoints.track(_context, target, kind="delete") as ck:
                send2trash.send2trash(str(target))
            return f"Moved to trash: {target}" + ck.notice
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
        with checkpoints.track(_context, target, kind="delete") as ck:
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
    except PermissionError:
        return f"Permission denied: {target}"
    except OSError as exc:
        return f"Could not delete {target}: {exc}"

    return f"Permanently deleted: {target}" + ck.notice


@skill(category="files", danger=Danger.DESTRUCTIVE)
def move_path(
    source: str,
    destination: str,
    _source: str = "local",
    _confirm: bool = False,
    _context=None,
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
    _fold_source(verdict, src, moves=True)
    if dst.exists():
        verdict.danger = Danger.CRITICAL
        verdict.reasons.append(f"{dst} already exists and would be replaced")

    enforce(verdict, f"move {src} to {dst}",
            skill="move_path", source=_source, pre_confirmed=_confirm)

    try:
        # Both ends: the source (restored on undo) and the destination (removed,
        # or put back if the move replaced something).
        with checkpoints.track(_context, src, dst, kind="move") as ck:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
    except PermissionError:
        return f"Permission denied moving {src}"
    except OSError as exc:
        return f"Could not move {src}: {exc}"
    return f"Moved {src} → {dst}" + ck.notice


@skill(category="files", danger=Danger.MODERATE)
def copy_path(source: str, destination: str, _source: str = "local", _confirm: bool = False,
              _context=None) -> str:
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
    _fold_source(verdict, src, moves=False)
    if dst.exists():
        verdict.danger = max(verdict.danger, Danger.DESTRUCTIVE)
        verdict.reasons.append(f"{dst} already exists and would be overwritten")

    enforce(verdict, f"copy {src} to {dst}",
            skill="copy_path", source=_source, pre_confirmed=_confirm)

    try:
        # The destination is what changes: absent (removed on undo) or
        # overwritten (restored on undo).
        with checkpoints.track(_context, dst, kind="copy") as ck:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
    except OSError as exc:
        return f"Could not copy {src}: {exc}"
    return f"Copied {src} → {dst}" + ck.notice


_FIND_SCAN_CAP = 5000


@skill(category="files", danger=Danger.SAFE)
def find_files(
    name: str,
    path: str = "home",
    max_results: int = 50,
    content: str = "",
) -> str:
    """Search for files by name, newest first, and optionally by their contents.

    Args:
        name: Filename or glob pattern, e.g. '*.pdf' or 'report'.
        path: Directory to search under.
        max_results: Maximum matches to return (1-500).
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
                if len(matches) >= _FIND_SCAN_CAP:
                    break
        except (OSError, PermissionError):
            pass

    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    # Newest first: "the report I just saved" is the usual question, and a
    # cap applied before sorting would drop exactly those files.
    matches = sorted(matches, key=_mtime, reverse=True)

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
                # One past the page is enough to know there is more; reading
                # every candidate for a count could mean gigabytes.
                if len(filtered) > max_results:
                    break
        matches = filtered

    total = len(matches)
    matches = matches[:max_results]
    if not matches:
        where = f" containing '{content}'" if content else ""
        return f"No files matching '{name}'{where} under {root}."

    lines = [f"{len(matches)} match(es) for '{name}' under {root} (newest first):"]
    for match in matches:
        try:
            size = _human_bytes(match.stat().st_size) if match.is_file() else "dir"
        except OSError:
            size = "?"
        lines.append(f"  {match}  ({size})")
    if total > len(matches):
        capped = " (scan limit reached)" if total >= _FIND_SCAN_CAP else ""
        lines.append(f"  … {total - len(matches)} more not shown{capped}; "
                     "narrow the pattern, path or content to see them.")
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
    _context=None,
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
    # Only the loose files are snapshotted (not the whole directory tree); the
    # folders and moved files the run creates are recorded one by one so that
    # undo_changes can remove them and put every file back.
    with checkpoints.track(_context, *loose, kind="organize") as ck:
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
            fresh = not destination.exists()
            destination.mkdir(exist_ok=True)
            if fresh:
                ck.created(destination)
            new_path = destination / item.name

            if new_path.exists():
                skipped.append(item.name)
                continue
            try:
                shutil.move(str(item), str(new_path))
                ck.created(new_path)
                moved.append(f"{item.name} → {folder}/")
            except OSError as exc:
                skipped.append(f"{item.name} ({exc})")

    out = [f"Organised {root} ({mode}): {len(moved)} file(s) moved."]
    out += [f"  {m}" for m in moved[:15]]
    if len(moved) > 15:
        out.append(f"  … and {len(moved) - 15} more")
    if skipped:
        out.append(f"{len(skipped)} skipped (name conflict or error).")
    return "\n".join(out) + ck.notice


def open_path(path: str) -> str:
    """Open a file, folder or URL in the default application.

    Args:
        path: What to open.
    """
    target = config.resolve_user_path(path) if not path.startswith(("http://", "https://")) else Path(path)
    return apps.open_uri(str(target))
