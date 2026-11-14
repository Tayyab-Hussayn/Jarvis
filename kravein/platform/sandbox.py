"""bubblewrap sandbox for ``run_command(sandbox=True)``.

The point of the sandbox is that a command can be *tried* or its effect
*inspected* without being able to change anything: the whole filesystem is
read-only, there is no network, no desktop/session bus, no host processes and
no privilege to gain. Nothing here decides *whether* to sandbox - that is the
skill's job - and nothing here ever falls back to running unsandboxed: if the
sandbox cannot be built, ``status()`` says why and the caller refuses.

Every spawn still goes through :mod:`kravein.platform.shell`.
"""

from __future__ import annotations

import fnmatch
import os
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .. import config
from . import shell

# Environment variables handed into the sandbox. A WHITELIST, not a blacklist:
# the list of variables that point at a socket or an agent is open-ended
# (DBUS_SESSION_BUS_ADDRESS, WAYLAND_DISPLAY, DISPLAY, XDG_RUNTIME_DIR,
# SSH_AUTH_SOCK, GPG_AGENT_INFO, NIRI_SOCKET, HYPRLAND_INSTANCE_SIGNATURE,
# SWAYSOCK, TMUX, DOCKER_HOST, ...) and so are API keys, so anything not named
# here simply does not exist inside.
ENV_KEEP = ("PATH", "TERM", "TZ", "USER", "LOGNAME", "SHELL", "HOME")

# Bounded scan of the places a pathname socket or a secret file can sit.
_SCAN_MAX_DEPTH = 6
_SCAN_MAX_ENTRIES = 80000
_SCAN_MAX_SECONDS = 2.5
_SCAN_MAX_MASKS = 300
_SCAN_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv"}
_SCAN_TTL = 30.0
# Outside $HOME and the working directory: world-writable or service-owned
# places where a socket can be left. /run and /tmp are replaced by tmpfs.
_EXTRA_ROOTS = ("/var/tmp", "/srv", "/mnt", "/media")

# Sizes of the throwaway tmpfs mounts, so a sandboxed command cannot fill host
# memory (an unsized tmpfs is half of RAM).
_TMP_BYTES = 256 * 1024 * 1024
_RUN_BYTES = 16 * 1024 * 1024
_RETRY_AFTER = 30.0       # a failed probe is retried after this long

# Secrets that opaque code (``python -c "print(open(...).read())"``) could read
# back to the model. An explicit read the classifier can see (``cat ~/.ssh/id``)
# is left visible: it asks for a card, and a card that then returns nothing
# would be a lie.
# Opaque code is SAFE inside the sandbox (no card) and its output reaches the
# model, so anything that holds a session, token or history must simply not
# exist in there. Kravein's own config/state/data dirs (the Playwright profile
# lives in the data dir) are masked separately in build_argv.
_SECRET_DIRS = (".ssh", ".gnupg", ".aws", ".azure", ".docker", ".kube",
                ".password-store", ".config/gh", ".config/gcloud",
                # browser profiles: cookies and saved logins
                ".mozilla", ".config/google-chrome", ".config/chromium",
                ".config/BraveSoftware", ".config/vivaldi", ".config/microsoft-edge",
                # keyrings, credential stores, messaging sessions
                ".local/share/keyrings", ".config/rclone",
                ".config/Signal", ".local/share/TelegramDesktop")
_SECRET_FILES = (".netrc", ".git-credentials", ".npmrc", ".pypirc",
                 ".bash_history", ".zsh_history", ".python_history",
                 ".local/share/fish/fish_history")
_SECRET_NAMES = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_*",
                 "*api_keys*", "*credentials*", "*secret*")


@dataclass(frozen=True)
class Status:
    available: bool
    reason: str = ""          # why not, in bwrap's own words when it has some
    path: str = ""
    version: str = ""

    def summary(self) -> str:
        if self.available:
            return f"available ({self.path}, {self.version})"
        return f"unavailable: {self.reason}"


_status: Status | None = None
_status_at = 0.0
_lock = threading.Lock()


def reset_cache() -> None:
    """Forget the probe and the scan (tests, and after installing bwrap)."""
    global _status
    with _lock:
        _status = None
    _scan_cache.clear()


def invalidate_scan() -> None:
    """Forget the socket scan. Called when an unsandboxed command starts: it may
    create a socket, and a stale scan would leave that one connectable."""
    _scan_cache.clear()


def status() -> Status:
    """Is a working sandbox possible here? Probed by really building one.

    ``bwrap`` on PATH is not enough: with user namespaces disabled (a hardened
    kernel, a container, some distro defaults) it exists and fails at setup.
    Running a trivial sandbox that uses every feature the real one does (the
    sized tmpfs included) is the only honest test, and its stderr is the reason
    the caller should show. A success is cached; a failure is retried after
    ``_RETRY_AFTER`` so installing bwrap does not need a restart.
    """
    global _status, _status_at
    with _lock:
        if _status is not None and (_status.available
                                    or time.monotonic() - _status_at < _RETRY_AFTER):
            return _status
        _status_at = time.monotonic()
        binary = shell.which("bwrap")
        if not binary:
            _status = Status(False, "bubblewrap (bwrap) is not installed")
            return _status
        probe = shell.run(
            [binary, "--unshare-all", "--ro-bind", "/", "/", "--dev", "/dev",
             "--proc", "/proc", "--size", "1048576", "--tmpfs", "/tmp", "/bin/true"],
            timeout=10, audit_event="shell.sandbox_probe",
        )
        if not probe.ok:
            why = (probe.stderr.strip() or probe.stdout.strip()
                   or f"exit {probe.returncode}").splitlines()[0]
            _status = Status(False, f"bwrap cannot create a sandbox here ({why})", binary)
            return _status
        ver = shell.run([binary, "--version"], timeout=5, audit_event="shell.sandbox_probe")
        _status = Status(True, "", binary, ver.output.splitlines()[0] if ver.output else "")
        return _status


# --- the working directory -----------------------------------------------------------------

def check_cwd(cwd: Path) -> str:
    """Why ``cwd`` cannot be the sandbox's working directory, or "".

    The working directory is bound back into the sandbox *from the host*, so a
    cwd that contains or is part of the places the sandbox hides brings them
    straight back: ``/`` re-exposes the whole host (``/run/user/<uid>/bus``
    and the compositor socket included, found by review - a full escape),
    ``/run/...`` is the runtime dir itself, and ``/tmp`` itself holds the X11,
    tmux and ssh-agent sockets. The socket scan cannot be the defence for
    those: it is bounded.
    """
    real = Path(os.path.realpath(cwd))
    if str(real) == "/":
        return "the root directory contains everything the sandbox hides"
    for hidden in ("/run", "/var/run"):
        h = Path(os.path.realpath(hidden))
        if real == h or h in real.parents:
            return f"{real} is inside {h}, where the session bus and compositor sockets live"
    if str(real) == "/tmp":
        return "/tmp itself holds X11, tmux and ssh-agent sockets"
    return ""


# --- sockets and secrets --------------------------------------------------------------------
#
# A unix socket on a read-only bind mount can still be connect()ed to - read-only
# stops writes to the file, not the connection (found by Claude Code's sandbox
# too). /run and /tmp are replaced by tmpfs, which removes the Wayland socket,
# the session bus, X11, docker, tmux and the ssh-agent. What is left are sockets
# living in the home directory, the working directory and a few shared places
# (gpg-agent in ~/.gnupg, ssh ControlPath sockets, browser and IDE sockets,
# Kravein's own). Each one found is covered with /dev/null, which is not a
# socket, so connect() fails. The scan is bounded; when it runs out of budget it
# says so (``complete``) and the skill tells the caller.

_scan_cache: dict[str, tuple[float, list[str], list[str], bool]] = {}


def _is_secret_name(name: str) -> bool:
    low = name.lower()
    if low.endswith(".pub"):
        return False
    return any(fnmatch.fnmatch(low, pat) for pat in _SECRET_NAMES)


def _scan(root: Path, deadline: float, budget: list[int]) -> tuple[list[str], list[str], bool]:
    socks: list[str] = []
    secrets_found: list[str] = []
    stack = [(str(root), 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    budget[0] -= 1
                    if budget[0] <= 0 or time.monotonic() > deadline:
                        return socks, secrets_found, False
                    try:
                        mode = entry.stat(follow_symlinks=False).st_mode
                    except OSError:
                        continue
                    if stat.S_ISSOCK(mode):
                        socks.append(entry.path)
                    elif stat.S_ISREG(mode) and _is_secret_name(entry.name):
                        secrets_found.append(entry.path)
                    elif (stat.S_ISDIR(mode) and entry.name not in _SCAN_SKIP
                          and depth < _SCAN_MAX_DEPTH):
                        # Deeper than _SCAN_MAX_DEPTH is a known, documented
                        # limit and not reported as "incomplete" (every real
                        # home has such trees); running out of entries or time is.
                        stack.append((entry.path, depth + 1))
        except OSError:
            continue
        if len(socks) + len(secrets_found) >= _SCAN_MAX_MASKS * 2:
            return socks, secrets_found, False
    return socks, secrets_found, True


def scan(home: Path, cwd: Path) -> tuple[list[str], list[str], bool]:
    """(socket files, secret-named files, scan complete) under home, cwd and shared dirs."""
    key = f"{home}|{cwd}"
    now = time.monotonic()
    hit = _scan_cache.get(key)
    if hit and now - hit[0] < _SCAN_TTL:
        return hit[1], hit[2], hit[3]
    socks: list[str] = []
    secrets_found: list[str] = []
    complete = True
    deadline = now + _SCAN_MAX_SECONDS
    budget = [_SCAN_MAX_ENTRIES]
    roots = [home, cwd] + [Path(p) for p in _EXTRA_ROOTS if os.path.isdir(p)]
    for root in dict.fromkeys(roots):
        s, f, ok = _scan(root, deadline, budget)
        complete = complete and ok
        socks += [p for p in s if p not in socks]
        secrets_found += [p for p in f if p not in secrets_found]
    _scan_cache[key] = (now, socks[:_SCAN_MAX_MASKS], secrets_found[:_SCAN_MAX_MASKS], complete)
    return _scan_cache[key][1], _scan_cache[key][2], complete


def scan_complete(home: Path, cwd: Path) -> bool:
    return scan(home, cwd)[2]


# --- argv ---------------------------------------------------------------------------------

def build_argv(command: str, cwd: Path, tag: str, binary: str | None = None,
               hide_secrets: bool = True) -> list[str]:
    """The bwrap command line that runs ``command`` under ``/bin/sh -c``.

    Order matters: later mounts sit on top of earlier ones. The working
    directory goes in after /tmp (it may live under /tmp) but before /run, so
    even a cwd that somehow contained the runtime dir could not bring it back;
    the secret and socket masks come last.

    ``hide_secrets=False`` is for a command the classifier already recognised
    as a secret read: it asks the owner for a card, and approving it must show
    the real content.
    """
    binary = binary or status().path or "bwrap"
    home = Path(os.environ.get("HOME") or Path.home())
    # Kravein's own dirs are hidden BEFORE the cwd is bound back, so a cwd inside
    # them (the default workspace lives under DATA_DIR) is bound on top of the
    # tmpfs and only that subtree shows. Skipping the mask for such a cwd (the
    # earlier string-prefix test) left memory.json etc. readable to opaque code.
    kdirs: list[str] = []
    kdirs_again: list[str] = []
    if hide_secrets:
        for d in (config.CONFIG_DIR, config.STATE_DIR, config.DATA_DIR):
            if d.is_dir() and not d.is_symlink():
                kdirs += ["--size", str(_RUN_BYTES), "--tmpfs", str(d)]
                # A cwd that CONTAINS the dir (cwd = $HOME) is bound back over
                # the mask and would bring it back: mask it again on top. Not
                # when cwd is inside it, or the cwd itself would vanish.
                real_d, real_cwd = Path(os.path.realpath(d)), Path(os.path.realpath(cwd))
                if real_d != real_cwd and real_d not in real_cwd.parents:
                    kdirs_again += ["--size", str(_RUN_BYTES), "--tmpfs", str(d)]
    argv = [
        binary,
        # net, ipc, pid, uts, user and cgroup (where the kernel has them): the
        # command sees an empty network (no host sockets, abstract ones
        # included), no host processes, and cannot signal or ptrace anything.
        "--unshare-all",
        # The sandbox dies with Kravein; a stopped job leaves nothing behind.
        "--die-with-parent",
        # A new terminal session: no TIOCSTI keystroke injection into ours.
        "--new-session",
        # No capabilities, and bwrap sets no_new_privs, so a setuid binary
        # (sudo, su) cannot elevate.
        "--cap-drop", "ALL",
        "--ro-bind", "/", "/",
        # A minimal /dev and a /proc of the new pid namespace: host pids are
        # invisible. /dev/shm is a private tmpfs inside --dev.
        "--dev", "/dev",
        "--proc", "/proc",
        # Throwaway writable space, size-capped. /tmp covers X11, tmux and
        # ssh-agent sockets.
        "--size", str(_TMP_BYTES), "--tmpfs", "/tmp",
        *kdirs,
        "--ro-bind", str(cwd), str(cwd),
        *kdirs_again,
        "--chdir", str(cwd),
        # /run covers /run/user/<uid> (Wayland, the session bus, the keyring and
        # gpg sockets). After the cwd bind on purpose, see above.
        "--size", str(_RUN_BYTES), "--tmpfs", "/run",
    ]
    # /var/run is normally a symlink to /run (already covered); a real
    # directory would be a second place for sockets.
    if os.path.isdir("/var/run") and not os.path.islink("/var/run"):
        argv += ["--size", str(_RUN_BYTES), "--tmpfs", "/var/run"]
    socks, secret_files, _ = scan(home, cwd)
    if hide_secrets:
        # Opaque code can read what no classifier saw coming, and its output
        # goes to the model. Known secret locations simply are not there.
        for rel in _SECRET_DIRS:
            d = home / rel
            # A dotfile manager (stow, chezmoi) makes ~/.ssh a symlink: hide
            # the real directory it points at, not just a name-matched file.
            if d.is_symlink():
                d = Path(os.path.realpath(d))
            if d.is_dir():
                argv += ["--size", str(_RUN_BYTES), "--tmpfs", str(d)]
        for rel in _SECRET_FILES:
            f = home / rel
            if f.is_file() and not f.is_symlink():
                argv += ["--ro-bind", "/dev/null", str(f)]
        for f in secret_files:
            argv += ["--ro-bind", "/dev/null", f]
    argv.append("--clearenv")
    env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.setdefault("HOME", str(home))
    env.update({"LC_ALL": "C", "LANG": "C", "KRAVEIN_SANDBOX": "1", "KRAVEIN_GROUP_TAG": tag})
    for key, value in env.items():
        argv += ["--setenv", key, value]
    for sock in socks:
        argv += ["--ro-bind", "/dev/null", sock]
    return argv + ["--", "/bin/sh", "-c", command]
