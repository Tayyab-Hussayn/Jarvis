"""Safe subprocess execution.

Everything that shells out goes through here so that (a) every external
command lands in the audit log, and (b) argument lists are used by default
instead of ``shell=True``. The Windows build sprinkled ``subprocess.run`` and
``CREATE_NO_WINDOW`` across a dozen modules; this centralises it.
"""

from __future__ import annotations

import os
import secrets
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Sequence

from .. import audit

DEFAULT_TIMEOUT = 30

_which_cache: dict[str, str | None] = {}
_which_lock = threading.Lock()


@dataclass
class Result:
    """Outcome of an external command."""

    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def output(self) -> str:
        """stdout if there is any, otherwise stderr."""
        return self.stdout.strip() or self.stderr.strip()

    def __bool__(self) -> bool:
        return self.ok


def which(binary: str) -> str | None:
    """Cached ``shutil.which``."""
    with _which_lock:
        if binary not in _which_cache:
            _which_cache[binary] = shutil.which(binary)
        return _which_cache[binary]


def have(binary: str) -> bool:
    return which(binary) is not None


def clear_which_cache() -> None:
    with _which_lock:
        _which_cache.clear()


def _clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    # Force predictable, parseable output from CLI tools.
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    if extra:
        env.update(extra)
    return env


def run(
    args: Sequence[str] | str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    check: bool = False,
    input_text: str | None = None,
    cwd: str | os.PathLike | None = None,
    env: dict[str, str] | None = None,
    shell: bool = False,
    audit_event: str = "shell.run",
) -> Result:
    """Run a command and capture its output.

    ``shell=True`` is supported for the explicit run-a-command skill, but
    callers inside the platform layer should always pass an argument list.
    """
    if shell:
        arg_list = [args] if isinstance(args, str) else list(args)
        display = arg_list[0] if arg_list else ""
    else:
        arg_list = [args] if isinstance(args, str) else [str(a) for a in args]
        display = " ".join(arg_list)

    audit.record(audit_event, command=display, cwd=str(cwd) if cwd else None, shell=shell)

    try:
        proc = subprocess.run(
            args if shell else arg_list,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            input=input_text,
            cwd=str(cwd) if cwd else None,
            env=_clean_env(env),
            shell=shell,
            # Never let a child inherit the terminal and prompt for a password.
            stdin=None if input_text is not None else subprocess.DEVNULL,
        )
    except FileNotFoundError:
        result = Result(arg_list, 127, "", f"command not found: {display}")
        audit.record("shell.not_found", command=display)
        return result
    except subprocess.TimeoutExpired as exc:
        result = Result(
            arg_list, 124,
            (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            f"timed out after {timeout}s",
            timed_out=True,
        )
        audit.record("shell.timeout", command=display, timeout=timeout)
        return result
    except PermissionError as exc:
        return Result(arg_list, 126, "", str(exc))

    result = Result(arg_list, proc.returncode, proc.stdout or "", proc.stderr or "")

    if not result.ok:
        audit.record(
            "shell.failed",
            command=display,
            returncode=result.returncode,
            stderr=result.stderr[:500],
        )

    if check and not result.ok:
        raise RuntimeError(f"`{display}` failed ({result.returncode}): {result.stderr.strip()}")

    return result


def spawn(
    args: Sequence[str],
    *,
    cwd: str | os.PathLike | None = None,
    env: dict[str, str] | None = None,
    detach: bool = True,
) -> int:
    """Start a process and return immediately.

    ``detach=True`` puts the child in its own session so it survives the
    assistant exiting — the Linux equivalent of the Windows build's
    ``CREATE_NO_WINDOW`` + ``Popen`` combination for launching GUI apps.
    """
    arg_list = [str(a) for a in args]
    audit.record("shell.spawn", command=" ".join(arg_list), detach=detach)

    try:
        proc = subprocess.Popen(
            arg_list,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(cwd) if cwd else None,
            env=_clean_env(env) if env else None,
            start_new_session=detach,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"command not found: {arg_list[0]}") from exc

    return proc.pid


def start_group(
    command: str,
    *,
    cwd: str | os.PathLike | None,
    stdout_path: str | os.PathLike,
    stderr_path: str | os.PathLike,
    env: dict[str, str] | None = None,
    audit_event: str = "shell.user_command",
) -> subprocess.Popen:
    """Start ``command`` through the shell as the leader of its own process group.

    The environment carries a random ``KRAVEIN_GROUP_TAG``. After the leader is
    reaped its pid (the group id) can be recycled, so a bare ``killpg`` could hit
    a stranger; the tag is how ``kill_group`` proves a surviving process is ours.

    * ``start_new_session``: the shell and everything it forks share one
      process group, so a timeout or stop can kill the whole tree. Killing only
      the direct child left grandchildren (``sh -c 'sleep 999 &'``) running
      forever.
    * Output goes to **files**, not pipes. With pipes a surviving grandchild
      kept the write end open and ``communicate()`` hung past the timeout; with
      files nothing waits on a descriptor, big output can be kept in full, and
      a command that outlives its timeout can be adopted as a background job
      without losing what it already printed.
    * stdin is /dev/null so nothing can prompt for a password on the terminal.
    """
    audit.record(audit_event, command=command, cwd=str(cwd) if cwd else None, shell=True)
    tag = secrets.token_hex(8)
    fds = []
    try:
        for path in (stdout_path, stderr_path):
            # 0600: the output of a command can hold anything the owner can read.
            fds.append(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=str(cwd) if cwd else None,
            env=_clean_env({**(env or {}), "KRAVEIN_GROUP_TAG": tag}),
            stdin=subprocess.DEVNULL,
            stdout=fds[0],
            stderr=fds[1],
            start_new_session=True,
        )
        proc.kravein_tag = tag
        return proc
    finally:
        # The child has its own copies; keeping ours open would leak two
        # descriptors per command.
        for fd in fds:
            os.close(fd)


def wait(proc: subprocess.Popen, timeout: float | None) -> int | None:
    """Return the exit code, or None if ``proc`` is still running after ``timeout``."""
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def _survivors(proc: subprocess.Popen) -> list[int]:
    """Pids still in ``proc``'s group that carry its tag (leader excluded).

    /proc is the only way to see children that outlived their shell
    (``server &``): they are reparented to init, so no Popen knows them.
    """
    tag = getattr(proc, "kravein_tag", "")
    if not tag:
        return []
    needle = b"KRAVEIN_GROUP_TAG=" + tag.encode()
    found = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    for entry in entries:
        if not entry.isdigit() or int(entry) == proc.pid:
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as handle:
                fields = handle.read().rsplit(b")", 1)[1].split()
            # fields: state ppid pgrp ...; zombies are already dead.
            if fields[0] == b"Z" or int(fields[2]) != proc.pid:
                continue
            with open(f"/proc/{entry}/environ", "rb") as handle:
                if needle in handle.read():
                    found.append(int(entry))
        except (OSError, IndexError, ValueError):
            continue
    return found


def group_alive(proc: subprocess.Popen) -> bool:
    """True while the leader runs or any tagged member of its group does."""
    return proc.poll() is None or bool(_survivors(proc))


def kill_group(proc: subprocess.Popen, grace: float = 2.0) -> bool:
    """TERM the whole process group of ``proc``, then KILL what is left.

    While the leader is unreaped its pid is still the group id, so ``killpg``
    is safe. Once the leader is gone the pgid may have been recycled for a
    stranger, so survivors are found by their tag and signalled one by one.
    Returns whether a signal was sent.
    """
    pgid = proc.pid  # start_new_session makes the leader's pid the group id

    def signal_all(sig: int) -> bool:
        sent = False
        if proc.poll() is None:
            try:
                os.killpg(pgid, sig)
                sent = True
            except ProcessLookupError:
                pass
        for pid in _survivors(proc):
            try:
                os.kill(pid, sig)
                sent = True
            except ProcessLookupError:
                pass
        return sent

    if not signal_all(signal.SIGTERM):
        return False
    audit.record("shell.kill_group", pgid=pgid)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not group_alive(proc):
            return True
        time.sleep(0.05)
    signal_all(signal.SIGKILL)
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    return True


def first_available(*binaries: str) -> str | None:
    """Return the first binary from the list that exists on PATH."""
    for binary in binaries:
        if have(binary):
            return binary
    return None
