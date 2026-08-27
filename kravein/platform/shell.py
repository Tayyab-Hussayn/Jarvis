"""Safe subprocess execution.

Everything that shells out goes through here so that (a) every external
command lands in the audit log, and (b) argument lists are used by default
instead of ``shell=True``. The Windows build sprinkled ``subprocess.run`` and
``CREATE_NO_WINDOW`` across a dozen modules; this centralises it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
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


def first_available(*binaries: str) -> str | None:
    """Return the first binary from the list that exists on PATH."""
    for binary in binaries:
        if have(binary):
            return binary
    return None
