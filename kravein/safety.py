"""Safety layer: classify risk, then require explicit confirmation.

Two independent checks run before anything destructive happens:

1. **Static guards** — path protection and command pattern matching. These
   are hard blocks; no confirmation can override a protected system path.
2. **Confirmation** — the caller (REPL, daemon, Discord bridge) supplies a
   confirmer callback. Only a real "yes" from a human lets the action run.

Skills declare their own risk level via the ``@skill(danger=...)`` decorator;
this module supplies the enforcement and the heuristics for free-form input
like shell commands and filesystem paths.
"""

from __future__ import annotations

import os
import re
import shlex
import threading
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Callable, Sequence

from . import audit, config


class Danger(IntEnum):
    """How much damage an action can do."""

    SAFE = 0        # read-only: status, listing, search
    MODERATE = 1    # visible but reversible: launch app, set volume, write file
    DESTRUCTIVE = 2 # data loss or process death: delete, kill, overwrite
    CRITICAL = 3    # whole-system: shutdown, reboot, package removal, sudo


DANGER_LABELS = {
    Danger.SAFE: "safe",
    Danger.MODERATE: "moderate",
    Danger.DESTRUCTIVE: "destructive",
    Danger.CRITICAL: "critical",
}


class SafetyError(Exception):
    """Raised when an action is blocked outright."""


class ConfirmationRequired(Exception):
    """Raised when an action needs a human yes and none was available."""

    def __init__(self, prompt: str, detail: str = ""):
        super().__init__(prompt)
        self.prompt = prompt
        self.detail = detail


@dataclass
class Verdict:
    """Result of classifying a proposed action."""

    danger: Danger
    reasons: list[str] = field(default_factory=list)
    blocked: bool = False
    block_reason: str = ""

    @property
    def needs_confirmation(self) -> bool:
        return self.danger >= Danger.DESTRUCTIVE

    def summary(self) -> str:
        label = DANGER_LABELS[self.danger]
        if self.reasons:
            return f"{label}: " + "; ".join(self.reasons)
        return label


# ---------------------------------------------------------------------------
# Path protection
# ---------------------------------------------------------------------------

def _protected_roots() -> list[Path]:
    raw = config.get("safety.protected_paths", []) or []
    return [Path(p) for p in raw]


def _readonly_roots() -> list[Path]:
    raw = config.get("safety.readonly_paths", []) or []
    return [Path(p).expanduser() for p in raw]


# Directories that are catastrophic to delete even though they are the
# user's own. Deleting these is always blocked, never merely confirmed.
_NEVER_DELETE = {
    Path.home(),
    Path.home() / ".ssh",
    Path.home() / ".gnupg",
    Path("/"),
}


def resolve_target(raw: str | os.PathLike) -> Path:
    """Resolve a path without following a final symlink into a new location."""
    p = Path(os.path.expandvars(str(raw))).expanduser()
    try:
        # strict=False so non-existent targets (e.g. a file to create) work.
        return p.resolve(strict=False)
    except (OSError, RuntimeError):
        return p.absolute()


def check_path(raw: str | os.PathLike, write: bool = True) -> Verdict:
    """Classify a filesystem path the assistant is about to touch."""
    target = resolve_target(raw)
    verdict = Verdict(danger=Danger.SAFE if not write else Danger.MODERATE)

    if not write:
        return verdict

    for root in _protected_roots():
        try:
            if target == root or target.is_relative_to(root):
                verdict.blocked = True
                verdict.danger = Danger.CRITICAL
                verdict.block_reason = (
                    f"{target} is inside the protected system path {root}"
                )
                return verdict
        except (OSError, ValueError):
            continue

    for root in _readonly_roots():
        try:
            if target == root or target.is_relative_to(root):
                verdict.blocked = True
                verdict.danger = Danger.DESTRUCTIVE
                verdict.block_reason = f"{target} is inside a read-only path ({root})"
                return verdict
        except (OSError, ValueError):
            continue

    if target in _NEVER_DELETE:
        verdict.blocked = True
        verdict.danger = Danger.CRITICAL
        verdict.block_reason = f"{target} is never allowed as a bulk target"
        return verdict

    return verdict


def check_delete(raw: str | os.PathLike) -> Verdict:
    """Classify a delete. Directories with contents escalate to destructive."""
    verdict = check_path(raw, write=True)
    if verdict.blocked:
        return verdict

    target = resolve_target(raw)
    verdict.danger = Danger.DESTRUCTIVE
    verdict.reasons.append(f"deletes {target}")

    if target.is_dir() and not target.is_symlink():
        try:
            count = sum(1 for _ in target.rglob("*"))
        except (OSError, PermissionError):
            count = -1
        if count > 0:
            verdict.reasons.append(f"directory contains {count} item(s)")
        if count > 100:
            verdict.danger = Danger.CRITICAL
    return verdict


# ---------------------------------------------------------------------------
# Shell command classification
# ---------------------------------------------------------------------------

# Patterns that are refused outright — no confirmation path.
_FORBIDDEN = [
    (r"\brm\s+(-[a-zA-Z]*\s+)*/(\s|$)", "rm targeting the filesystem root"),
    (r":\(\)\s*\{.*\}\s*;?\s*:", "fork bomb"),
    (r"\bmkfs(\.\w+)?\b", "filesystem creation"),
    (r"\bdd\b[^|;\n]*\bof=/dev/(sd|nvme|vd|mmcblk)", "raw write to a block device"),
    (r">\s*/dev/(sd|nvme|vd|mmcblk)\w*", "redirect onto a block device"),
    (r"\bchmod\s+(-[a-zA-Z]*\s+)*777\s+/(\s|$)", "chmod 777 on root"),
    (r"\b(shred|wipefs)\b.*\/dev\/", "device wipe"),
    (r"\bcurl\b[^|;\n]*\|\s*(sudo\s+)?(ba|z|k)?sh", "piping a download straight into a shell"),
    (r"\bwget\b[^|;\n]*\|\s*(sudo\s+)?(ba|z|k)?sh", "piping a download straight into a shell"),
    (r"\bhistory\s+-c\b", "clearing shell history"),
    (r"\b(iptables|nft)\b.*\s(-F|flush)\b", "flushing firewall rules"),
]

# Patterns that require confirmation.
_CRITICAL_PATTERNS = [
    (r"\bsudo\b", "runs with root privileges"),
    (r"\bpkexec\b", "runs with root privileges"),
    (r"\bsystemctl\s+(?!.*--user)(start|stop|restart|disable|enable|mask)\b", "changes a system service"),
    (r"\b(poweroff|reboot|halt|shutdown)\b", "powers off or reboots the machine"),
    (r"\bpacman\s+-[A-Za-z]*R", "removes packages"),
    (r"\bpacman\s+-[A-Za-z]*S", "installs or upgrades packages"),
    (r"\b(yay|paru)\b", "AUR package operation"),
    (r"\b(useradd|userdel|usermod|groupadd|passwd)\b", "modifies user accounts"),
    (r"\b(mount|umount)\b", "changes mounted filesystems"),
    (r"\b(fdisk|parted|cfdisk|sgdisk)\b", "modifies partitions"),
]

_DESTRUCTIVE_PATTERNS = [
    (r"\brm\b(\s+-[a-zA-Z]*)*\s", "removes files"),
    # `unlink` was missing here until a model, having had four delete attempts
    # denied in a row, found it and deleted the file unconfirmed. The others on
    # this block are the rest of that same family — every ordinary way to
    # remove a file without typing "rm".
    (r"\bunlink\b", "removes files"),
    (r"\bfind\b[^|;\n]*\s-delete\b", "deletes files it finds"),
    (r"\bfind\b[^|;\n]*-exec\s+(rm|unlink|shred)\b", "deletes files it finds"),
    (r"\bxargs\b[^|;\n]*\b(rm|unlink|shred)\b", "removes files"),
    (r"\brsync\b[^|;\n]*--delete\b", "deletes files at the destination"),
    (r"\bbusybox\s+(rm|unlink)\b", "removes files"),
    (r"\b(gio|kioclient5?)\s+(rm|remove|trash)\b", "removes files"),
    (r"\btrash(-put|_put)?\b", "removes files"),
    (r"\bsed\b[^|;\n]*\s-[a-zA-Z]*i\b", "edits files in place"),
    # Interpreter one-liners are the obvious next workaround once the shell
    # verbs are covered.
    (r"\b(python3?|perl|ruby|node)\b[^|;\n]*\b(unlink|rmtree|remove|rmdir)\b",
     "removes files through an interpreter"),
    (r"\bshred\b", "irrecoverably shreds files"),
    (r"\b(kill|pkill|killall)\b", "terminates processes"),
    (r"\bmv\b", "moves or renames files"),
    (r"\btruncate\b", "truncates files"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f|push\s+.*--force)", "discards git work"),
    (r"\bdd\b", "raw block copy"),
    (r"\bchown\b|\bchmod\b", "changes ownership or permissions"),
    (r">\s*[^>|&\s]", "overwrites a file via redirect"),
    (r"\bdrop\s+(table|database)\b", "drops database objects"),
]

_MODERATE_PATTERNS = [
    (r"\b(pip|npm|cargo|go)\s+install\b", "installs packages"),
    (r"\b(tee|cp|install)\b", "writes files"),
    (r">>", "appends to a file"),
    (r"\bsystemctl\s+--user\s+(start|stop|restart)\b", "changes a user service"),
    (r"\bgit\s+(commit|push|checkout|merge|rebase)\b", "changes repository state"),
]


def classify_command(command: str) -> Verdict:
    """Classify a free-form shell command string."""
    text = (command or "").strip()
    verdict = Verdict(danger=Danger.SAFE)
    if not text:
        return verdict

    for pattern, why in _FORBIDDEN:
        if re.search(pattern, text, re.IGNORECASE):
            verdict.blocked = True
            verdict.danger = Danger.CRITICAL
            verdict.block_reason = why
            return verdict

    matched: list[tuple[Danger, str]] = []
    for level, patterns in (
        (Danger.CRITICAL, _CRITICAL_PATTERNS),
        (Danger.DESTRUCTIVE, _DESTRUCTIVE_PATTERNS),
        (Danger.MODERATE, _MODERATE_PATTERNS),
    ):
        for pattern, why in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                matched.append((level, why))

    if matched:
        verdict.danger = max(level for level, _ in matched)
        seen: set[str] = set()
        for _, why in matched:
            if why not in seen:
                seen.add(why)
                verdict.reasons.append(why)

    # A command touching a protected path escalates regardless of the verb.
    for token in _candidate_paths(text):
        path_verdict = check_path(token, write=True)
        if path_verdict.blocked and verdict.danger >= Danger.DESTRUCTIVE:
            verdict.blocked = True
            verdict.block_reason = path_verdict.block_reason
            verdict.danger = Danger.CRITICAL
            return verdict

    return verdict


def _candidate_paths(command: str) -> list[str]:
    """Pull absolute-looking path arguments out of a command string."""
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError:
        tokens = command.split()
    return [t for t in tokens if t.startswith("/") and len(t) > 1]


# ---------------------------------------------------------------------------
# Process protection
# ---------------------------------------------------------------------------

# Killing these takes the session or the machine down with it.
CRITICAL_PROCESSES = {
    "systemd", "init", "kthreadd", "kernel", "dbus-daemon", "dbus-broker",
    "systemd-logind", "systemd-journald", "systemd-udevd", "polkitd",
    "Hyprland", "sway", "gnome-shell", "plasmashell", "Xorg", "Xwayland",
    "pipewire", "wireplumber", "pulseaudio", "sshd", "NetworkManager",
    "login", "agetty", "greetd", "sddm", "gdm", "lightdm",
}


def own_pids() -> set[int]:
    """This process and its ancestors — the terminal Kravein is running in.

    Killing an ancestor kills the assistant. `check_process` only ever guarded
    `os.getpid()`, so the parent terminal running `kravein serve` was fair game:
    exactly the mistake that closed the wrong window, one layer down.
    """
    pids: set[int] = {os.getpid()}
    try:
        import psutil

        for parent in psutil.Process().parents():
            pids.add(parent.pid)
    except Exception:
        pass
    return pids


def check_process(name: str, pid: int | None = None) -> Verdict:
    """Classify killing a process."""
    verdict = Verdict(danger=Danger.DESTRUCTIVE, reasons=[f"terminates '{name}'"])
    lowered = (name or "").lower()

    if pid is not None and pid <= 1:
        verdict.blocked = True
        verdict.danger = Danger.CRITICAL
        verdict.block_reason = f"PID {pid} is the init system"
        return verdict

    if pid is not None and pid in own_pids():
        verdict.blocked = True
        verdict.block_reason = (
            "that PID is the assistant itself or the terminal running it"
            if pid != os.getpid()
            else "that PID is the assistant itself"
        )
        return verdict

    for critical in CRITICAL_PROCESSES:
        if lowered == critical.lower():
            verdict.blocked = True
            verdict.danger = Danger.CRITICAL
            verdict.block_reason = (
                f"'{name}' is a critical session or system process; "
                "killing it would take down your desktop"
            )
            return verdict

    return verdict


# ---------------------------------------------------------------------------
# Confirmation broker
# ---------------------------------------------------------------------------

# Signature: (prompt, detail, danger) -> bool
Confirmer = Callable[[str, str, Danger], bool]

_local = threading.local()


def _deny(prompt: str, detail: str, danger: Danger) -> bool:
    return False


def get_confirmer() -> Confirmer:
    return getattr(_local, "confirmer", None) or _deny


def set_confirmer(fn: Confirmer | None) -> None:
    """Install the confirmation callback for the current thread."""
    _local.confirmer = fn


def _denied_targets() -> set[str]:
    targets = getattr(_local, "denied_targets", None)
    if targets is None:
        targets = set()
        _local.denied_targets = targets
    return targets


def _target_keys(action_desc: str) -> set[str]:
    """Identify *what* an action acts on, independent of the verb used.

    Verb-based classification is what a determined model probes around: it
    tried ``delete_path``, ``delete_path permanent``, ``rm -f``, ``rm`` and
    then ``unlink`` on the same file. Keying on the target instead means the
    first denial covers every later spelling of the same request.

    This is the *fallback*. Reading the target back out of a sentence written
    for a human is guesswork, and it silently produced no keys at all for
    anything that was not a path or a pid — windows, apps, services, packages —
    so declining those recorded nothing and the model could retry forever.
    Skills should pass ``targets=`` to :func:`enforce` instead; see
    :func:`describe_targets`.
    """
    # Action descriptions wrap shell commands in backticks ("run `rm -f /x`"),
    # so a raw token can arrive as "/x`" and silently fail to match the key
    # recorded from a plainer phrasing. Strip the wrapping punctuation.
    keys = {
        p.strip("`\"'(),;:").rstrip("/").lower()
        for p in _candidate_paths(action_desc)
    }
    keys |= {f"pid:{pid}" for pid in re.findall(r"\bpid\s*(\d+)", action_desc, re.I)}
    # Named targets are conventionally quoted in these descriptions —
    # "close window 'foot'", "stop service 'nginx'".
    keys |= {
        name.strip().lower()
        for name in re.findall(r"['\"]([^'\"]{1,80})['\"]", action_desc)
        if name.strip()
    }
    return {k for k in keys if k and k != "/"}


class confirmer_scope:
    """Context manager installing a confirmer for the enclosing block.

    Also scopes the denial memory. The agent wraps one whole request in this,
    so "the user said no to that" lasts exactly as long as the turn it was
    said in and never leaks into the next one.
    """

    def __init__(self, fn: Confirmer | None):
        self._fn = fn
        self._previous: Confirmer | None = None
        self._previous_denials: set[str] | None = None

    def __enter__(self) -> "confirmer_scope":
        self._previous = getattr(_local, "confirmer", None)
        self._previous_denials = getattr(_local, "denied_targets", None)
        _local.confirmer = self._fn
        _local.denied_targets = set()
        return self

    def __exit__(self, *exc_info) -> None:
        _local.confirmer = self._previous
        _local.denied_targets = self._previous_denials


def describe_targets(*parts: str) -> list[str]:
    """Build denial-memory keys, dropping blanks.

    Skills use this to say exactly what they are about to act on:

        enforce(verdict, "close window 'foot'", skill="close_window",
                targets=describe_targets(f"window:{w.id}", f"app:{w.app}"))

    A skill that resolves its target *implicitly* — ``close_window()`` with no
    query acts on whatever happens to be focused — should also pass a marker
    like ``"close_window:implicit"``. Declining such an action means the user
    rejected the guess, and the next guess may well land on a different window
    entirely, so the marker stops the retry rather than re-prompting about
    something the user never mentioned.
    """
    return [p.strip().lower() for p in parts if p and p.strip()]


def enforce(
    verdict: Verdict,
    action_desc: str,
    *,
    skill: str = "",
    source: str = "local",
    pre_confirmed: bool = False,
    targets: Sequence[str] | None = None,
) -> None:
    """Apply a verdict: block, confirm, or allow. Raises on refusal.

    ``targets`` identifies what is being acted on, for the per-turn denial
    memory. Pass it wherever the skill knows — parsing the target back out of
    ``action_desc`` is a fallback, not a design.
    """
    if verdict.blocked:
        audit.record(
            "safety.blocked",
            skill=skill,
            source=source,
            action=action_desc,
            danger=DANGER_LABELS[verdict.danger],
            reason=verdict.block_reason,
        )
        raise SafetyError(
            f"Refused: {action_desc}. {verdict.block_reason}."
        )

    if not verdict.needs_confirmation:
        return

    if not config.get("safety.allow_destructive", True):
        audit.record(
            "safety.denied_by_policy",
            skill=skill, source=source, action=action_desc,
        )
        raise SafetyError(
            f"Refused: {action_desc}. Destructive actions are disabled "
            "in settings (safety.allow_destructive)."
        )

    if pre_confirmed:
        audit.record(
            "safety.pre_confirmed",
            skill=skill, source=source, action=action_desc,
            danger=DANGER_LABELS[verdict.danger],
        )
        return

    # Already refused for this target in this turn? Refuse outright rather
    # than asking again. Re-asking is how a model grinds a user into approving
    # by attrition, and re-asking with a *different verb* is how it slips past
    # a gap in the pattern list.
    # Prefer what the skill declared; fall back to reading the sentence.
    keys = set(targets) if targets else _target_keys(action_desc)
    already_denied = keys & _denied_targets()
    if already_denied:
        audit.record(
            "safety.bypass_attempt",
            skill=skill, source=source, action=action_desc,
            danger=DANGER_LABELS[verdict.danger],
            denied_target=sorted(already_denied)[0],
        )
        raise SafetyError(
            f"Refused: {action_desc}. You already declined this in the current "
            f"request (target: {sorted(already_denied)[0]}). Do not retry it "
            "with different arguments, a different tool, or more force — stop "
            "and tell the user it was cancelled."
        )

    confirmer = get_confirmer()
    detail = verdict.summary()
    audit.record(
        "safety.confirm_requested",
        skill=skill, source=source, action=action_desc,
        danger=DANGER_LABELS[verdict.danger], detail=detail,
    )

    if confirmer is _deny:
        # Record it in the denial memory before refusing. Without this the
        # unattended path had no memory at all: a scheduled job refused on
        # `delete_path` came straight back with `run_command "rm -f …"` for the
        # same file, and the mechanism built precisely to catch a retry under a
        # different verb never saw the first refusal.
        _denied_targets().update(keys)
        audit.record(
            "safety.unattended_refused",
            skill=skill, source=source, action=action_desc,
            danger=DANGER_LABELS[verdict.danger],
        )
        raise ConfirmationRequired(
            f"'{action_desc}' needs confirmation and this session has no way "
            "to ask for one, so it was refused. Tell the user it could not be "
            "done unattended — do not retry it with another tool.",
            detail,
        )

    approved = False
    try:
        approved = bool(confirmer(action_desc, detail, verdict.danger))
    except Exception as exc:
        audit.record("safety.confirm_error", action=action_desc, error=str(exc))
        approved = False

    audit.record(
        "safety.confirm_result",
        skill=skill, source=source, action=action_desc,
        danger=DANGER_LABELS[verdict.danger], approved=approved,
    )

    if not approved:
        # Remember the refusal so a differently-spelled retry at the same
        # target is refused outright instead of asking again.
        _denied_targets().update(keys)
        raise ConfirmationRequired(f"Cancelled: {action_desc}.", detail)
