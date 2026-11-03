"""Running arbitrary commands.

This is the sharpest tool in the box, so it carries the most safety
machinery: every command is classified by :mod:`kravein.safety` before it
runs, outright-dangerous patterns are refused, and anything destructive
needs a human yes.

It replaces the original ``agent/executor.py`` codegen-and-exec path, which
asked an LLM to write Python and ran it with no review at all.
"""

from __future__ import annotations

import atexit
import os
import secrets
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .. import audit, config
from .. import safety
from ..platform import commands
from ..platform import shell as shell_platform
from ..registry import skill
from ..safety import Danger, Verdict, classify_command, describe_targets, enforce

MAX_OUTPUT_CHARS = 8000          # inline cap per stream; more spills to a file
_PREVIEW_EACH = 3000             # head and tail shown when a stream spills
_KEEP_SPILLS = 50                # spill files kept in the workspace
MAX_JOBS = 3                     # running background jobs per principal
JOB_LIFETIME = 2 * 60 * 60       # a job is killed after this long
_TAIL_CHARS = 3000


# --- output files -----------------------------------------------------------------

def _outputs_dir() -> Path:
    """Where spilled output lives: the workspace, which check_read allows.

    STATE_DIR is blocked for reading (it holds the safety policy), so a spill
    there could not be opened with read_file - the whole point of the spill.
    """
    d = config.WORKSPACE_DIR / "outputs"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def _rotate() -> None:
    """Keep the newest _KEEP_SPILLS files; never delete a running job's."""
    protected = {p for j in list(_jobs.values()) if j.running for p in (j.out, j.err)}
    with _jobs_lock:
        protected |= _inflight
    try:
        files = sorted((f for f in _outputs_dir().iterdir() if f.is_file()),
                       key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError:
        return
    for old in files[_KEEP_SPILLS:]:
        if old not in protected:
            old.unlink(missing_ok=True)


def _line_count(path: Path) -> int:
    n, last = 0, b""
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            n += chunk.count(b"\n")
            last = chunk[-1:]
    return n + (1 if last and last != b"\n" else 0)


def _read_stream(path: Path) -> tuple[str, bool]:
    """(text to show, spilled). Over the cap: head + tail, the rest only in the file."""
    try:
        size = path.stat().st_size
        if size == 0:
            return "", False
        with path.open("rb") as handle:
            if size <= MAX_OUTPUT_CHARS:
                return handle.read().decode("utf-8", "replace").rstrip(), False
            head = handle.read(_PREVIEW_EACH).decode("utf-8", "replace")
            handle.seek(max(size - _PREVIEW_EACH, _PREVIEW_EACH))
            tail = handle.read().decode("utf-8", "replace")
    except OSError:
        return "", False
    hidden = size - _PREVIEW_EACH * 2
    return f"{head.rstrip()}\n… [{hidden:,} bytes not shown] …\n{tail.rstrip()}", True


def _head_capped(path: Path) -> str:
    """First MAX_OUTPUT_CHARS of a stream, as the old inline cap did."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_OUTPUT_CHARS + 1)
    except OSError:
        return ""
    text = raw.decode("utf-8", "replace").rstrip()
    if len(raw) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "\n… [output truncated]"
    return text


def _tail(path: Path, chars: int = _TAIL_CHARS) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - chars))
            return handle.read().decode("utf-8", "replace").rstrip()
    except OSError:
        return ""


# --- agent state: persistent cwd ----------------------------------------------------

def _state(context) -> dict | None:
    """The calling agent's per-run scratch, or None for the CLI (no persistence)."""
    state = getattr(context, "tool_state", None)
    return state if isinstance(state, dict) else None


def _principal(context) -> str:
    return getattr(context, "principal", "") or "local"


def cwd_after(command: str, cwd: Path) -> Path | None:
    """Where a leading ``cd X [&& cd Y ...]`` leaves the shell, or None.

    Parsed from safety's argv tokens, never with a regex on the raw text. None
    means "no change, or I cannot be sure" - a guessed directory would send the
    next command somewhere the model did not intend, so anything but the plain
    form is ignored: ``cd -``, options, ``;``, ``||``, pipes (a cd in a
    pipeline runs in a subshell), a later ``cd`` after another command, and
    any ``$var`` or substitution (safety refuses to tokenise those).
    """
    tokens = safety._shell_tokens(command)
    if not tokens or tokens[0] != "cd":
        return None
    current, i = cwd, 0
    while i < len(tokens) and tokens[i] == "cd":
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if nxt is None or nxt == "&&":
            target, i = Path.home(), i + 1           # bare `cd` goes home
        elif nxt.startswith("-") or nxt in (";", "||", "|", "&"):
            return None
        else:
            target, i = current / Path(nxt).expanduser(), i + 2
        try:
            target = target.resolve()
        except OSError:
            return None
        if not target.is_dir():
            return None
        current = target
        if i < len(tokens):
            if tokens[i] != "&&":
                return None
            i += 1
    # A later `cd` after some other command (`cd a && make && cd ..`) moves the
    # shell again and we did not follow it: report unknown rather than `a`.
    if "cd" in tokens[i:]:
        return None
    return current


# --- background jobs ------------------------------------------------------------------

@dataclass
class _Job:
    id: str
    principal: str
    command: str
    cwd: str
    proc: object
    out: Path
    err: Path
    started: float = field(default_factory=time.time)
    stop_reason: str = ""     # "" | "stopped" | "lifetime" | "shutdown"
    thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        # A job whose shell exited but whose children (`server &`) live on is
        # still running: it holds a slot and must be stoppable.
        return shell_platform.group_alive(self.proc)


_jobs: dict[str, _Job] = {}
_jobs_lock = threading.Lock()
# Slots promised to a run_command that is still being confirmed or started.
# Checking the count and registering later was racy: enforce() can wait minutes
# on an owner card, and six callers all saw "2 running".
_reserved: dict[str, int] = {}
# Output files of foreground commands in flight: _rotate must not delete the
# files of a silent long-running command just because they are old.
_inflight: set[Path] = set()


def _running_for(principal: str) -> list[_Job]:
    with _jobs_lock:
        return [j for j in _jobs.values() if j.principal == principal and j.running]


def _reserve(principal: str) -> bool:
    """Take a job slot, atomically with the count. False if none is free."""
    with _jobs_lock:
        running = sum(1 for j in _jobs.values() if j.principal == principal and j.running)
        if running + _reserved.get(principal, 0) >= MAX_JOBS:
            return False
        _reserved[principal] = _reserved.get(principal, 0) + 1
        return True


def _release(principal: str) -> None:
    with _jobs_lock:
        _reserved[principal] = max(0, _reserved.get(principal, 0) - 1)


def _watch(job: _Job) -> None:
    """Enforce the lifetime and write the closing audit entry."""
    deadline = job.started + JOB_LIFETIME
    shell_platform.wait(job.proc, max(0.0, deadline - time.time()))
    # The shell may be gone while its children are not; the lifetime covers them.
    while job.running:
        if time.time() >= deadline:
            job.stop_reason = job.stop_reason or "lifetime"
            shell_platform.kill_group(job.proc)
            break
        time.sleep(0.5)
    rc = job.proc.poll()
    audit.record("shell.job_end", job=job.id, principal=job.principal,
                 command=job.command[:200], returncode=rc,
                 runtime_s=round(time.time() - job.started, 1),
                 reason=job.stop_reason or "exit")


def _register(principal: str, command: str, cwd: Path, proc, out: Path, err: Path) -> _Job:
    """Record a started command as a job. The caller holds a reserved slot."""
    job = _Job(id="job-" + secrets.token_hex(3), principal=principal, command=command,
               cwd=str(cwd), proc=proc, out=out, err=err)
    with _jobs_lock:
        _jobs[job.id] = job
        _reserved[principal] = max(0, _reserved.get(principal, 0) - 1)
        # Finished jobs stay readable for a while, but not forever.
        done = sorted((j for j in _jobs.values() if not j.running), key=lambda j: j.started)
        for old in done[:-20]:
            _jobs.pop(old.id, None)
    audit.record("shell.job_start", job=job.id, principal=principal,
                 command=command[:200], cwd=str(cwd), pid=proc.pid)
    job.thread = threading.Thread(target=_watch, args=(job,), name=job.id, daemon=True)
    job.thread.start()
    _rotate()
    return job


def kill_all_jobs() -> None:
    """Kill every running job. Runs at exit: a job must not outlive Kravein."""
    for job in list(_jobs.values()):
        if job.running:
            job.stop_reason = job.stop_reason or "shutdown"
            shell_platform.kill_group(job.proc)


atexit.register(kill_all_jobs)


def _job_for(job_id: str, context) -> _Job | str:
    job = _jobs.get((job_id or "").strip())
    # Another principal's job is "not found", not "forbidden": its existence
    # is not this caller's business.
    if job is None or job.principal != _principal(context):
        mine = ", ".join(j.id for j in _jobs.values() if j.principal == _principal(context))
        return f"No background job '{job_id}'." + (f" Yours: {mine}." if mine else "")
    return job


def _job_report(job: _Job) -> str:
    runtime = int(time.time() - job.started)
    rc = job.proc.poll()
    if job.running:
        state = "running" if rc is None else (
            f"running (the shell exited with code {rc}, its child processes are still running)")
    elif job.stop_reason == "stopped":
        state = "killed (stopped by request)"
    elif job.stop_reason == "lifetime":
        state = f"killed (reached the {JOB_LIFETIME // 3600} h limit)"
    elif job.stop_reason == "shutdown":
        state = "killed (Kravein exited)"
    elif rc is not None and rc < 0:
        state = f"killed by signal {-rc}"
    else:
        state = f"exited with code {rc}"
    lines = [f"{job.id}: {state} after {runtime}s", f"$ {job.command}", f"cwd: {job.cwd}"]
    for label, path in (("stdout", job.out), ("stderr", job.err)):
        tail = _tail(path)
        if tail:
            lines.append(f"[{label} tail]\n{tail}")
    lines.append(f"full output: {job.out} (stderr: {job.err}); read it with read_file or search_text")
    return "\n".join(lines)


@skill(
    category="shell", danger=Danger.MODERATE,
    # Two names, one function (keeps the tool count down). The preset, not the
    # caller, decides the action: command_status can never stop a job.
    aliases={"command_status": {"action": "status"}, "stop_command": {"action": "stop"}},
)
def command_job(
    job_id: str,
    action: Literal["status", "stop"] = "status",
    _source: str = "local",
    _confirm: bool = False,
    _context=None,
) -> str:
    """Check or stop a background command started by run_command.

    status shows running or finished, the exit code, the runtime and the end of
    the output. stop kills the command and everything it started. Use the job
    id that run_command returned when it started the command in the background
    or moved it there after a timeout.

    Args:
        job_id: The job id, like 'job-1a2b3c'.
        action: status (default) to look, stop to kill it.
    """
    job = _job_for(job_id, _context)
    if isinstance(job, str):
        return job
    if action != "stop" or not job.running:
        return _job_report(job)
    enforce(
        Verdict(danger=Danger.MODERATE),
        f"stop background job {job.id}: `{job.command[:120]}`",
        skill="stop_command", source=_source, pre_confirmed=_confirm,
        targets=describe_targets(f"job:{job.id}"),
    )
    job.stop_reason = "stopped"
    shell_platform.kill_group(job.proc)
    if job.thread:
        job.thread.join(timeout=5)
    return _job_report(job)


# --- run_command -------------------------------------------------------------------------

def _cwd_line(state, cwd: Path) -> str:
    return f"\n[cwd: {cwd}]" if state is not None else ""


def _render(command: str, out: Path, err: Path, secret: bool, rc: int, note: str,
            keep: list[Path]) -> str:
    """Format a finished command; files that hold a spill are added to ``keep``."""
    parts = [f"$ {command}"]
    if note:
        parts.append(note)
    shown = False
    for label, path in (("stdout", out), ("stderr", err)):
        if secret:
            # Never a file for a secret read: cap it inline.
            text, spilled = _head_capped(path), False
        else:
            text, spilled = _read_stream(path)
        if not text:
            continue
        shown = True
        parts.append(text if label == "stdout" else f"[stderr]\n{text}")
        if spilled:
            keep.append(path)
            parts.append(f"[full {label}: {path} ({_line_count(path):,} lines); "
                         "read it with read_file or search_text]")
    if not shown:
        parts.append("(no output)")
    parts.append(f"[exit {rc}]")
    return "\n".join(parts)


@skill(category="shell", danger=Danger.DESTRUCTIVE)
def run_command(
    command: str,
    working_directory: str = "",
    timeout_seconds: int = 60,
    background: bool = False,
    _source: str = "local",
    _confirm: bool = False,
    _context=None,
) -> str:
    """Run a shell command and return its output.

    The command is risk-classified first. Read-only commands run straight
    away; anything that deletes, kills, installs or needs root asks you to
    confirm. Outright dangerous patterns are refused. The working directory
    persists between calls: after 'cd build' the next command starts in build,
    and every result ends with '[cwd: ...]'. Output longer than a few thousand
    characters is kept in a file; the result shows the start and end and names
    the file to open with read_file or search_text. A command that has not
    finished at its timeout keeps running as a background job; check it with
    command_status and stop it with stop_command.

    Args:
        command: The command line to run.
        working_directory: Directory to run it in. Defaults to the directory
            of the previous command, or your home at the start.
        timeout_seconds: Wait this long for the result (1-600). Then the
            command moves to a background job instead of being killed.
        background: Start the command and return a job id at once, without
            waiting. Use for servers, watchers, builds and anything that
            takes minutes; at most 3 can run at a time.
    """
    command = (command or "").strip()
    if not command:
        return "No command given."

    # Unknown program: fail fast with candidates, not "command not found" (#200).
    command, problem = commands.check_command_line(command)
    if problem:
        return problem

    state = _state(_context)
    principal = _principal(_context)

    # The directory comes first: classification needs it to tell whether a
    # relative target (`cp a b`) already exists, and a missing directory
    # needs no confirmation because nothing will run.
    if working_directory:
        cwd = config.resolve_user_path(working_directory)
        # A relative path means relative to where the previous command left the
        # shell (the docstring promises the cwd persists), not to Kravein's own
        # process directory, which is why "sub" after `cd proj` was "not found".
        base = Path(state["cwd"]) if state is not None and state.get("cwd") else None
        wd = Path(working_directory).expanduser()
        if base is not None and not wd.is_absolute() and (base / wd).is_dir():
            cwd = (base / wd).resolve()
    elif state is not None and state.get("cwd") and Path(state["cwd"]).is_dir():
        cwd = Path(state["cwd"])
    else:
        cwd = Path.home()
    if not cwd.is_dir():
        return f"Working directory does not exist: {cwd}"

    verdict = classify_command(command, cwd=cwd)
    secret = verdict.secret_read

    if background and secret:
        # A background job's output lives in a file; a secret must never be
        # written anywhere the model can read it back from.
        return ("Refused: this command reads a secret file, so it cannot run in "
                "the background (its output would be saved to a file). Run it "
                "in the foreground.")
    # Reserve the job slot *before* enforce(): the card can wait minutes, and
    # checking the count first and registering afterwards let six concurrent
    # callers all pass a limit of three.
    reserved = False
    if background:
        reserved = _reserve(principal)
        if not reserved:
            running = _running_for(principal)
            return (f"Refused: {MAX_JOBS} background jobs are already running "
                    f"({', '.join(j.id for j in running)}). Check or stop one first "
                    "with command_status / stop_command.")
    spill_dir = out = err = None
    proc = None
    keep: list[Path] = []
    try:
        enforce(
            verdict, f"run `{command}`" + (" in the background" if background else ""),
            skill="run_command", source=_source, pre_confirmed=_confirm,
        )

        if state is not None and working_directory:
            state["cwd"] = str(cwd)

        timeout = max(1, min(int(timeout_seconds), 600))
        tag = f"cmd-{int(time.time())}-{secrets.token_hex(3)}"
        # A secret read gets a private throwaway directory that is deleted below;
        # everything else goes to the workspace so a spill can be read back.
        spill_dir = Path(tempfile.mkdtemp(prefix="kravein-secret-")) if secret else _outputs_dir()
        out, err = spill_dir / f"{tag}.out", spill_dir / f"{tag}.err"
        with _jobs_lock:
            _inflight.update((out, err))

        proc = shell_platform.start_group(command, cwd=cwd, stdout_path=out, stderr_path=err,
                                          audit_event="shell.user_command")
        if background:
            job = _register(principal, command, cwd, proc, out, err)
            reserved = False                      # _register consumed it
            keep += [out, err]
            return (f"Started background job {job.id} (pid {proc.pid}): `{command}`\n"
                    f"Check it with command_status('{job.id}'), stop it with "
                    f"stop_command('{job.id}'). Output: {out}")

        rc = shell_platform.wait(proc, timeout)
        if rc is None:
            audit.record("shell.timeout", command=command, timeout=timeout)
            if not secret and _reserve(principal):
                job = _register(principal, command, cwd, proc, out, err)
                keep += [out, err]
                return (f"$ {command}\n[did not finish in {timeout}s; moved to "
                        f"background job {job.id}; check with command_status]\n"
                        f"{_tail(out) or '(no output yet)'}" + _cwd_line(state, cwd))
            shell_platform.kill_group(proc)
            why = ("it reads a secret file" if secret
                   else f"{MAX_JOBS} background jobs are already running")
            note = (f"[timed out after {timeout}s and was killed; it could not move "
                    f"to the background because {why}]")
            text = _render(command, out, err, secret, 124, note, keep)
            return text + _cwd_line(state, cwd)

        if rc != 0:
            # shell.run() used to record these; the audit trail must not lose them.
            audit.record("shell.failed", command=command, returncode=rc,
                         stderr="" if secret else _tail(err, 500))
        new_cwd = cwd_after(command, cwd) if rc == 0 else None
        if state is not None:
            state["cwd"] = str(new_cwd or cwd)
        text = _render(command, out, err, secret, rc, "", keep)
        return text + _cwd_line(state, new_cwd or cwd)
    except BaseException:
        # Cancelled or crashed mid-run: leave no orphan behind. (A command that
        # became a job is registered and must not be killed by a later error.)
        if proc is not None and not keep:
            shell_platform.kill_group(proc)
        raise
    finally:
        if reserved:
            _release(principal)
        if out is not None:
            with _jobs_lock:
                _inflight.difference_update((out, err))
            if secret:
                # Nothing a secret command printed stays on disk.
                shutil.rmtree(spill_dir, ignore_errors=True)
            else:
                for path in (out, err):
                    if path not in keep:
                        path.unlink(missing_ok=True)
                if keep:
                    _rotate()


@skill(category="shell", danger=Danger.SAFE)
def check_command(command: str) -> str:
    """Explain what a command would do and how risky it is, without running it.

    Args:
        command: The command line to analyse.
    """
    command = (command or "").strip()
    if not command:
        return "No command given."

    verdict = classify_command(command)
    from ..safety import DANGER_LABELS

    lines = [f"$ {command}", f"Risk: {DANGER_LABELS[verdict.danger]}"]

    if verdict.blocked:
        lines.append(f"BLOCKED — {verdict.block_reason}")
        lines.append("This will be refused even if you confirm it.")
        return "\n".join(lines)

    if verdict.reasons:
        lines.append("Because it:")
        lines += [f"  - {r}" for r in verdict.reasons]

    if verdict.needs_confirmation:
        lines.append("Running this would ask you to confirm first.")
    else:
        lines.append("This would run without a confirmation prompt.")

    binary = command.split()[0] if command.split() else ""
    resolved = shell_platform.which(binary)
    lines.append(f"Resolves to: {resolved}" if resolved else f"'{binary}' is not on PATH.")

    return "\n".join(lines)


@skill(category="shell", danger=Danger.SAFE)
def which_command(name: str) -> str:
    """Find where a command lives on PATH.

    Args:
        name: Command name.
    """
    resolved = shell_platform.which(name)
    if not resolved:
        from ..platform import packages

        hint = packages.which_package(name)
        return f"'{name}' is not on PATH.\n{hint}"

    lines = [f"{name} → {resolved}"]
    real = os.path.realpath(resolved)
    if real != resolved:
        lines.append(f"  (symlink to {real})")

    from ..platform import packages

    owner = packages.which_package(resolved)
    if owner and "No package" not in owner:
        lines.append(f"  {owner}")
    return "\n".join(lines)


@skill(category="shell", danger=Danger.SAFE)
def read_audit_log(limit: int = 25, event_filter: str = "") -> str:
    """Show what the assistant has recently done.

    Every skill call, shell command and confirmation decision is recorded.

    Args:
        limit: How many entries to show.
        event_filter: Only show events starting with this prefix,
            e.g. 'shell', 'safety' or 'skill'.
    """
    from .. import audit

    entries = audit.tail(limit=max(1, min(int(limit), 200)), event_prefix=event_filter)
    if not entries:
        return f"No audit entries yet. Log lives at {config.AUDIT_LOG}"

    lines = [f"Last {len(entries)} audit entries ({config.AUDIT_LOG}):"]
    for entry in entries:
        stamp = str(entry.get("ts", ""))[11:19]
        event = entry.get("event", "?")
        detail = ""
        if "skill" in entry:
            detail = str(entry["skill"])
        elif "command" in entry:
            detail = str(entry["command"])[:80]
        elif "action" in entry:
            detail = str(entry["action"])[:80]
        lines.append(f"  {stamp}  {event:<26} {detail}")
    return "\n".join(lines)
