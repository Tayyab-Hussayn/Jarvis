"""File checkpoints and ``undo_changes`` (#223).

Claude Code snapshots a file before every edit and "rewinds" the files on undo.
This is the same idea, kept inside Kravein's own rules:

* **Files only.** A package, a service, a sent message or a deleted window
  cannot be rewound, and the skill says so. Nothing here pretends otherwise.
* **Before the change, with the after-state sealed.** :func:`track` snapshots
  each path (a file, a symlink, "did not exist", or a whole directory tree)
  before a file skill changes it, then records what the run *left* behind. That
  second fingerprint is what lets undo tell "the run's own result" from "the
  owner edited it again afterwards".
* **Undo asks.** ``undo_changes`` is DESTRUCTIVE: its card lists every path to be
  restored or removed and names the run. A file that changed after the run is
  skipped and listed; forcing it is a second, separate card. Every target goes
  through ``safety.check_path`` first, so an undo can never write to a protected
  or Kravein-private path.
* **Scoped by owner.** Runs live under a per-principal directory and are looked
  up only there, so one owner's undo cannot see, let alone touch, another's run.
* **A checkpoint failure never blocks the change.** The owner's card already
  approved it; the skill's result says the file was not checkpointed instead.

Layout under ``STATE_DIR/checkpoints/<sha256(principal)[:16]>/<run>/``:
``run.json`` (id, principal, start time), ``manifest.jsonl`` (append-only
``snap`` / ``seal`` / ``undone`` records) and ``blobs/`` (0600 file copies).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .. import audit, config, safety
from ..registry import skill
from ..safety import Danger, Verdict, describe_targets, enforce

RETENTION_DAYS = 7
KEEP_RUNS = 20
MAX_TREE_ENTRIES = 5000          # a directory bigger than this is not snapshotted
MAX_CARD_LINES = 150

_lock = threading.RLock()
_run_bytes: dict[str, int] = {}   # run dir -> blob bytes written so far
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


# ---------------------------------------------------------------------------
# Identity and layout
# ---------------------------------------------------------------------------

def principal_of(context: Any) -> str:
    """Whose run this is. ``run_principal`` is the delegating owner's principal;
    it is separate from ``Agent.principal`` on purpose - setting that on a
    sub-agent would switch on "Always" rules for it."""
    return str(getattr(context, "run_principal", "") or getattr(context, "principal", "") or "local")


def run_id_of(context: Any) -> str:
    """A delegated child's id is its delegation/task id; any other agent gets a
    per-agent token plus the turn number."""
    rid = str(getattr(context, "run_id", "") or "")
    if rid:
        return rid
    try:
        token = context.__dict__.setdefault("_ckpt_token", secrets.token_hex(3))
    except AttributeError:
        token = "x"
    return f"turn-{token}-{getattr(context, 'turn_no', 0)}"


def _root() -> Path:
    return Path(config.STATE_DIR) / "checkpoints"


def _principal_dir(principal: str) -> Path:
    return _root() / hashlib.sha256(principal.encode()).hexdigest()[:16]


def _run_dir(principal: str, run: str) -> Path:
    name = run if _SAFE_ID.match(run) else "h" + hashlib.sha256(run.encode()).hexdigest()[:16]
    return _principal_dir(principal) / name


def _mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _limits() -> tuple[int, int]:
    mb = 1024 * 1024
    return (int(float(config.get("agents.checkpoint_file_max_mb", 20)) * mb),
            int(float(config.get("agents.checkpoint_run_max_mb", 200)) * mb))


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------

def fingerprint(path: "str | os.PathLike") -> str:
    """A string that changes when the thing at ``path`` changes.

    ``absent``, ``dir``, ``link:<target>``, ``sha:<sha256>`` for a regular file
    (``big:<size>:<mtime_ns>`` past the file cap, where hashing is not worth it),
    or ``other``. A directory is just ``dir``: its contents are their own entries.
    """
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unreadable"
    if stat.S_ISLNK(st.st_mode):
        try:
            return "link:" + os.readlink(path)
        except OSError:
            return "unreadable"
    if stat.S_ISDIR(st.st_mode):
        return "dir"
    if not stat.S_ISREG(st.st_mode):
        return "other"
    if st.st_size > _limits()[0]:
        return f"big:{st.st_size}:{st.st_mtime_ns}"
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return "unreadable"
    return "sha:" + digest.hexdigest()


def real_path(path: "str | os.PathLike") -> str:
    """The path with its *parent* directories resolved. The last component is
    left alone: a symlink entry is the link itself, not what it points at."""
    parent, name = os.path.split(os.path.abspath(path))
    return os.path.join(os.path.realpath(parent), name)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def _append(run_dir: Path, record: dict[str, Any]) -> None:
    with _lock:
        with open(run_dir / "manifest.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")


def _read(run_dir: Path) -> tuple[dict[int, dict], dict[int, str], set[int]]:
    snaps: dict[int, dict] = {}
    seals: dict[int, str] = {}
    undone: set[int] = set()
    try:
        lines = (run_dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return snaps, seals, undone
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue                    # a torn last line must not hide the rest
        kind = rec.get("t")
        if kind == "snap" and isinstance(rec.get("seq"), int):
            snaps[rec["seq"]] = rec
        elif kind == "seal" and isinstance(rec.get("seq"), int):
            seals[rec["seq"]] = str(rec.get("after", ""))
        elif kind == "undone" and isinstance(rec.get("seq"), int):
            undone.add(rec["seq"])
    return snaps, seals, undone


def _meta(run_dir: Path) -> dict[str, Any]:
    try:
        return json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------

def prune(principal: str) -> int:
    """Drop runs older than RETENTION_DAYS and everything past the newest KEEP_RUNS."""
    base = _principal_dir(principal)
    try:
        runs = [d for d in base.iterdir() if d.is_dir()]
    except OSError:
        return 0
    stamped = sorted(((float(_meta(d).get("started") or 0), d) for d in runs),
                     key=lambda p: p[0], reverse=True)
    cutoff = time.time() - RETENTION_DAYS * 86400
    removed = 0
    for rank, (started, d) in enumerate(stamped):
        if rank >= KEEP_RUNS or started < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            _run_bytes.pop(str(d), None)
            removed += 1
    if removed:
        audit.record("checkpoint.pruned", runs=removed)
    return removed


def _runs(principal: str) -> list[tuple[float, str, Path]]:
    """``(started, run id, dir)`` newest first, for runs this principal owns."""
    base = _principal_dir(principal)
    out = []
    try:
        dirs = [d for d in base.iterdir() if d.is_dir()]
    except OSError:
        return []
    for d in dirs:
        meta = _meta(d)
        if meta.get("principal") == principal and meta.get("id"):
            out.append((float(meta.get("started") or 0), str(meta["id"]), d))
    return sorted(out, key=lambda r: r[0], reverse=True)


def find_run(principal: str, run: str = "") -> tuple[str, Path] | None:
    """The run called ``run`` (or the newest one) among ``principal``'s own."""
    runs = _runs(principal)
    if run:
        for _, rid, d in runs:
            if rid == run:
                return rid, d
        return None
    return (runs[0][1], runs[0][2]) if runs else None


def change_count(principal: str, run: str) -> int:
    """How many paths of this run can still be undone (0 when none)."""
    found = find_run(principal, run)
    if found is None:
        return 0
    snaps, seals, undone = _read(found[1])
    return len({s["path"] for q, s in snaps.items()
                if q not in undone and s.get("state") != "skipped"
                and seals.get(q, "?") != s.get("fp")})


# ---------------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------------

class Handle:
    """What :func:`track` yields. ``notice`` is "" or a line for the skill's result."""

    def __init__(self) -> None:
        self.active = False
        self.notices: list[str] = []
        self._run_dir: Path | None = None
        self._seqs: list[int] = []
        self._known: set[str] = set()
        self._tops: list[str] = []
        self._truncated: set[str] = set()
        self._seq = 0

    @property
    def notice(self) -> str:
        if not self.notices:
            return ""
        return "\nNot checkpointed (undo_changes cannot restore it): " + "; ".join(self.notices)

    # -- begin ----------------------------------------------------------

    def begin(self, context: Any, paths: tuple, kind: str) -> None:
        if context is None:
            return
        principal, run = principal_of(context), run_id_of(context)
        run_dir = _run_dir(principal, run)
        with _lock:
            fresh = not (run_dir / "run.json").exists()
            # Every level private: mkdir(parents=True) would leave the run and
            # principal directories at the umask default.
            for level in (_root(), _principal_dir(principal), run_dir, run_dir / "blobs"):
                _mkdir(level)
            if fresh:
                (run_dir / "run.json").write_text(json.dumps({
                    "id": run, "principal": principal, "started": time.time(),
                    "agent": getattr(getattr(context, "profile", None), "name", "") or "orchestrator",
                }), encoding="utf-8")
                os.chmod(run_dir / "run.json", 0o600)
                prune(principal)
            snaps, _, _ = _read(run_dir)
            self._seq = max(snaps, default=0)
        self._run_dir = run_dir
        self.active = True
        for raw in paths:
            if raw is None:
                continue
            p = os.path.abspath(os.path.expanduser(str(raw)))
            self._tops.append(p)
            fp = fingerprint(p)
            if fp == "dir":
                self._snap_tree(p, kind)
            else:
                self._snap_one(p, fp, kind)

    def _record(self, rec: dict[str, Any]) -> int:
        with _lock:
            self._seq += 1
            # Where the path really is, parent symlinks resolved: undo refuses
            # an entry whose parent has since become a link elsewhere.
            rec = {"t": "snap", "seq": self._seq, "rp": real_path(rec["path"]), **rec}
            _append(self._run_dir, rec)       # type: ignore[arg-type]
        self._seqs.append(self._seq)
        self._known.add(rec["path"])
        return self._seq

    def created(self, path: "str | os.PathLike") -> None:
        """Record that the operation just created ``path`` (it did not exist
        before), for operations whose new paths are not known up front. Call it
        right after creating a folder, and before the files inside it, so undo
        removes the files first."""
        if not self.active or self._run_dir is None:
            return
        try:
            p = os.path.abspath(os.path.expanduser(str(path)))
            if p in self._known:
                return
            seq = self._record({"path": p, "kind": "created", "fp": "absent", "state": "absent"})
            _append(self._run_dir, {"t": "seal", "seq": seq, "after": fingerprint(p)})
        except Exception as exc:
            audit.record("checkpoint.error", stage="created", error=f"{type(exc).__name__}: {exc}")

    def _snap_one(self, path: str, fp: str, kind: str) -> None:
        rec: dict[str, Any] = {"path": path, "kind": kind, "fp": fp}
        if fp == "absent":
            rec["state"] = "absent"
        elif fp.startswith("link:"):
            rec["state"], rec["link"] = "link", fp[5:]
        elif fp == "dir":
            rec["state"] = "dir"
            try:
                rec["mode"] = stat.S_IMODE(os.lstat(path).st_mode)
            except OSError:
                pass
        elif fp.startswith("sha:") or fp.startswith("big:"):
            self._snap_file(path, fp, rec)
        else:
            rec["state"], rec["reason"] = "skipped", "not a regular file"
            self.notices.append(f"{os.path.basename(path)} (not a regular file)")
        self._record(rec)

    def _snap_file(self, path: str, fp: str, rec: dict[str, Any]) -> None:
        file_cap, run_cap = _limits()
        try:
            st = os.lstat(path)
            size = st.st_size
            rec["mode"] = stat.S_IMODE(st.st_mode)
        except OSError:
            rec["state"], rec["reason"] = "skipped", "unreadable"
            self.notices.append(f"{os.path.basename(path)} (unreadable)")
            return
        key = str(self._run_dir)
        if size > file_cap:
            rec["state"], rec["reason"] = "skipped", "too large"
            self.notices.append(
                f"{os.path.basename(path)} is {size // (1024 * 1024)} MB, over the "
                f"{file_cap // (1024 * 1024)} MB per-file limit")
            return
        with _lock:
            used = _run_bytes.get(key)
            if used is None:
                used = sum(f.stat().st_size for f in (self._run_dir / "blobs").iterdir())  # type: ignore[operator]
            if used + size > run_cap:
                rec["state"], rec["reason"] = "skipped", "run limit"
                _run_bytes[key] = used
                self.notices.append(
                    f"{os.path.basename(path)} (this run already holds "
                    f"{used // (1024 * 1024)} MB of checkpoints, the limit is "
                    f"{run_cap // (1024 * 1024)} MB)")
                return
            blob = f"b{self._seq + 1}"
            dest = self._run_dir / "blobs" / blob       # type: ignore[operator]
            try:
                fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as out, open(path, "rb") as src:
                    shutil.copyfileobj(src, out)
            except OSError as exc:
                dest.unlink(missing_ok=True)
                rec["state"], rec["reason"] = "skipped", f"copy failed: {exc}"
                self.notices.append(f"{os.path.basename(path)} (could not be copied)")
                return
            _run_bytes[key] = used + size
        rec["state"], rec["blob"] = "file", blob
        # What was copied must be what the fingerprint says: a file that moved
        # under us between the two reads is not restorable to a known state.
        if fingerprint(path) != fp:
            rec["state"], rec["reason"] = "skipped", "changed while being copied"
            self.notices.append(f"{os.path.basename(path)} (changed while being copied)")

    def _snap_tree(self, root: str, kind: str) -> None:
        entries = 0
        self._snap_one(root, "dir", kind)
        for here, dirs, files in os.walk(root, followlinks=False):
            for name in sorted(dirs) + sorted(files):
                entries += 1
                if entries > MAX_TREE_ENTRIES:
                    # The rest of this tree was never listed, so "not in the
                    # snapshot" no longer means "created by the run": seal()
                    # must not treat those files as new, or undo would delete
                    # the owner's own.
                    self._truncated.add(root)
                    self.notices.append(
                        f"{root} has more than {MAX_TREE_ENTRIES} entries; only the first "
                        f"{MAX_TREE_ENTRIES} are checkpointed")
                    return
                path = os.path.join(here, name)
                self._snap_one(path, fingerprint(path), kind)
            dirs.sort()

    # -- seal -----------------------------------------------------------

    def seal(self) -> None:
        if not self.active or self._run_dir is None:
            return
        for seq, path in self._pairs():
            _append(self._run_dir, {"t": "seal", "seq": seq, "after": fingerprint(path)})
        # Anything the operation created under a tracked path (a moved-in
        # directory, an organised subfolder) is recorded as "was absent", so
        # undo removes it again.
        count = 0
        for top in self._tops:
            if top in self._truncated or not os.path.isdir(top) or os.path.islink(top):
                continue
            for here, dirs, files in os.walk(top, followlinks=False):
                dirs.sort()
                for name in dirs + sorted(files):
                    path = os.path.join(here, name)
                    if path in self._known:
                        continue
                    count += 1
                    if count > MAX_TREE_ENTRIES:
                        self.notices.append("more new entries than can be tracked")
                        return
                    fp = fingerprint(path)
                    seq = self._record({"path": path, "kind": "created", "fp": "absent",
                                        "state": "absent"})
                    _append(self._run_dir, {"t": "seal", "seq": seq, "after": fp})

    def _pairs(self) -> list[tuple[int, str]]:
        snaps, seals, _ = _read(self._run_dir)           # type: ignore[arg-type]
        return [(s, snaps[s]["path"]) for s in self._seqs if s in snaps and s not in seals]


@contextmanager
def track(context: Any, *paths: "str | os.PathLike | None", kind: str = "change") -> Iterator[Handle]:
    """Snapshot ``paths`` before a file skill changes them, seal what it left after.

    ``context`` is the skill's ``_context`` (the calling Agent); without one
    (the CLI's ``kravein run``) this does nothing, like the read-before-edit
    guard. Any failure here is audited and swallowed: a broken checkpoint must
    not stop a change the owner already approved, and the handle's ``notice``
    tells the skill to say the file was not checkpointed.
    """
    handle = Handle()
    try:
        handle.begin(context, paths, kind)
    except Exception as exc:
        handle.active = False
        handle.notices.append("the checkpoint could not be written")
        audit.record("checkpoint.error", stage="begin", error=f"{type(exc).__name__}: {exc}")
    try:
        yield handle
    finally:
        try:
            handle.seal()
        except Exception as exc:
            audit.record("checkpoint.error", stage="seal", error=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------

class Action:
    __slots__ = ("seq", "path", "op", "status", "note", "expect", "snap")

    def __init__(self, seq: int, path: str, op: str, status: str, note: str,
                 expect: str, snap: dict[str, Any]) -> None:
        self.seq, self.path, self.op, self.status = seq, path, op, status
        self.note, self.expect, self.snap = note, expect, snap


def _plan(run_dir: Path) -> list[Action]:
    """What undoing the run would do, newest change first.

    ``expect`` is the fingerprint a path must still have for the action to be
    safe: what the run left behind. After an action is planned for a path the
    simulated state becomes the entry's own before-state, so an earlier entry of
    the same path is judged against what the later undo will have produced.
    """
    snaps, seals, undone = _read(run_dir)
    state: dict[str, str] = {}
    actions: list[Action] = []
    for seq in sorted(snaps, reverse=True):
        snap = snaps[seq]
        if seq in undone:
            continue
        path, pre = snap["path"], snap.get("fp", "")
        now = state.setdefault(path, fingerprint(path))
        if now == pre:
            continue                                       # already as it was
        if snap.get("state") == "skipped":
            actions.append(Action(seq, path, "none", "unrestorable",
                                  snap.get("reason", "not checkpointed"), "", snap))
            continue
        after = seals.get(seq)
        op = "remove" if snap["state"] == "absent" else "restore"
        v = safety.check_path(path, write=True)
        if v.blocked:
            actions.append(Action(seq, path, op, "blocked", v.block_reason, "", snap))
            continue
        if snap.get("rp") and real_path(path) != snap["rp"]:
            # A folder above it became a link (or was replaced): writing here
            # would land somewhere the run never touched.
            actions.append(Action(seq, path, op, "blocked",
                                  "a folder above it now points somewhere else", "", snap))
            continue
        if after is None or now != after:
            note = "changed since the run" if after is not None else "the run did not finish recording it"
            actions.append(Action(seq, path, op, "changed", note, after or "", snap))
            continue
        actions.append(Action(seq, path, op, "ok", "", after, snap))
        state[path] = pre
    return actions


def _restore(run_dir: Path, a: Action) -> str:
    """Do one action; returns "" or why it was not done. Re-checks the state
    first: a file edited between the card and now is never overwritten."""
    if a.status != "changed" and fingerprint(a.path) != a.expect:
        return "changed while undoing"
    snap, path = a.snap, a.path
    try:
        if a.op == "remove":
            if os.path.isdir(path) and not os.path.islink(path):
                os.rmdir(path)                         # only if empty: never rmtree
            elif os.path.lexists(path):
                os.unlink(path)
            return ""
        state = snap["state"]
        if state == "dir":
            os.makedirs(path, exist_ok=True)
            if "mode" in snap:
                os.chmod(path, snap["mode"])
            return ""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Build the old content beside the target and swap it in. Restoring in
        # place would leave a half-copied file if the copy failed (and a second
        # undo could then no longer repair it), and a private file would be
        # readable between the copy and the chmod. The temp file is created 0600.
        tmp = os.path.join(os.path.dirname(path),
                           f".{os.path.basename(path)}.kravein-undo-{secrets.token_hex(3)}")
        try:
            if state == "link":
                os.symlink(snap["link"], tmp)
            elif state == "file":
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as out, open(run_dir / "blobs" / snap["blob"], "rb") as src:
                    shutil.copyfileobj(src, out)
                if "mode" in snap:
                    os.chmod(tmp, snap["mode"])
            else:
                return f"nothing to restore for state {state!r}"
            os.replace(tmp, path)
        finally:
            if os.path.lexists(tmp):
                os.unlink(tmp)
        return ""
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


def _lines(actions: list[Action], verb: dict[str, str]) -> list[str]:
    rows = [f"{verb[a.op]} {a.path}" + (f" ({a.note})" if a.note else "") for a in actions]
    if len(rows) > MAX_CARD_LINES:
        rows = rows[:MAX_CARD_LINES] + [f"... and {len(rows) - MAX_CARD_LINES} more"]
    return rows


_VERB = {"restore": "restore", "remove": "remove (created by the run)", "none": "cannot restore"}


@skill(name="undo_changes", category="agents", danger=Danger.DESTRUCTIVE)
def undo_changes(task: str = "", force: bool = False, _source: str = "local",
                 _context: Any = None) -> str:
    """Undo the file changes a run made: restore what it edited, moved or deleted, remove what it created.

    Files only. Packages, services, messages and anything outside the file
    tools cannot be undone. The owner confirms on a card that lists every path.
    A file edited again after the run is skipped and listed; only set force
    when the owner asks to overwrite those too (a second card asks again).

    Args:
        task: The run or task id (shown as "undo available: <id>"). Empty means this run's own changes, else the newest run.
        force: Also overwrite files that changed after the run. Only when the owner asked for it.
    """
    principal = principal_of(_context)
    run = (task or "").strip().lstrip("#")
    if not run and change_count(principal, run_id_of(_context)):
        run = run_id_of(_context)
    found = find_run(principal, run)
    if found is None:
        return (f"No checkpointed run {run!r} of yours." if run
                else "There are no checkpointed file changes to undo.")
    rid, run_dir = found
    actions = _plan(run_dir)
    ok = [a for a in actions if a.status == "ok"]
    changed = [a for a in actions if a.status == "changed"]
    blocked = [a for a in actions if a.status == "blocked"]
    lost = [a for a in actions if a.status == "unrestorable"]
    if not ok and not (force and changed):
        text = [f"Nothing to undo in run {rid}."] if not (changed or blocked or lost) else []
        return "\n".join(text + _report(rid, [], changed, blocked, lost, [], force))

    done: list[Action] = []
    failed: list[tuple[Action, str]] = []
    if ok:
        verdict = Verdict(
            danger=Danger.DESTRUCTIVE,
            reasons=_lines(ok, _VERB) + ([f"{len(changed)} file(s) changed since the run and will be skipped"]
                                         if changed else []),
        )
        enforce(
            verdict,
            f"undo run {rid}: restore or remove {len(ok)} path(s)",
            skill="undo_changes", source=_source,
            targets=describe_targets(f"undo:{rid}"),
        )
        done, failed = _run(run_dir, ok)
    if force and changed:
        # A second card, for what the owner has edited since: overwriting newer
        # work is a different decision from undoing the run's own result.
        verdict = Verdict(
            danger=Danger.DESTRUCTIVE,
            reasons=_lines(changed, _VERB) + ["these files were changed after the run; "
                                              "their newer content will be lost"],
        )
        enforce(
            verdict,
            f"undo run {rid} over newer changes: overwrite {len(changed)} path(s)",
            skill="undo_changes", source=_source,
            targets=describe_targets(f"undo-force:{rid}"),
        )
        d2, f2 = _run(run_dir, changed)
        done, failed = done + d2, failed + f2
        changed = []
    audit.record("checkpoint.undone", run=rid, restored=len(done), failed=len(failed),
                 skipped=len(changed), principal_hash=_principal_dir(principal).name)
    return "\n".join(_report(rid, done, changed, blocked, lost, failed, force))


def _run(run_dir: Path, actions: list[Action]) -> tuple[list[Action], list[tuple[Action, str]]]:
    done, failed = [], []
    for a in actions:
        why = _restore(run_dir, a)
        if why:
            failed.append((a, why))
        else:
            done.append(a)
            _append(run_dir, {"t": "undone", "seq": a.seq})
    return done, failed


def _report(rid: str, done: list[Action], changed: list[Action], blocked: list[Action],
            lost: list[Action], failed: list[tuple[Action, str]], force: bool) -> list[str]:
    out = []
    if done:
        restored = [a for a in done if a.op == "restore" and a.snap["state"] != "dir"]
        removed = [a for a in done if a.op == "remove"]
        out.append(f"Undid run {rid}: restored {len(restored)} file(s), removed {len(removed)} "
                   "created by it. Files only: packages, services and messages are not undone.")
        out += [f"  restored {a.path}" for a in restored[:15]] + [f"  removed {a.path}" for a in removed[:15]]
    if changed:
        out.append(f"Skipped {len(changed)} file(s) changed since the run (not overwritten):")
        out += [f"  {a.path}" for a in changed[:20]]
        if not force:
            out.append(f"To overwrite them too, only if the owner asks: undo_changes(task=\"{rid}\", "
                       "force=True) (a second card asks again).")
    if blocked:
        out.append(f"Refused {len(blocked)} protected path(s):")
        out += [f"  {a.path}: {a.note}" for a in blocked[:10]]
    if lost:
        out.append(f"{len(lost)} path(s) were never checkpointed and cannot be restored:")
        out += [f"  {a.path} ({a.note})" for a in lost[:10]]
    if failed:
        out.append(f"{len(failed)} could not be restored:")
        out += [f"  {a.path}: {why}" for a, why in failed[:10]]
    return out
