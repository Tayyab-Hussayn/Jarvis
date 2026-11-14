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

import fnmatch
import hashlib
import json
import os
import re
import shlex
import stat as stat_mod
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Sequence

from . import audit, config


class Danger(IntEnum):
    """How much damage an action can do."""

    SAFE = 0        # read-only: status, listing, search
    MODERATE = 1    # visible but reversible: launch app, set volume, write file
    # Risky but not data loss (#237): outward messages, spending, acting on an
    # account, an unknown external tool. Asks like DESTRUCTIVE, but the owner's
    # "Always allow" creates a scoped, revocable grant. Compared by name or
    # ordering only: nothing may depend on the numeric values.
    GUARDED = 2
    DESTRUCTIVE = 3 # data loss or process death: delete, kill, overwrite
    CRITICAL = 4    # whole-system: shutdown, reboot, package removal, sudo


DANGER_LABELS = {
    Danger.SAFE: "safe",
    Danger.MODERATE: "moderate",
    Danger.GUARDED: "guarded",
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
    # "Always allow" signature (#199). Set only by classify_command, only for a
    # command in an owner-approved family; "" means Always is never offered.
    signature: str = ""
    # Set when the action would put a secret file's content into the model's
    # context (#211). enforce() never lets auto-approve or an Always rule answer
    # such a card: only a live owner click may.
    secret_read: bool = False
    # Generalised (#223): a card only a live owner click may answer, whatever the
    # reason - e.g. undo_changes(force=True), which overwrites the owner's newer
    # work and is otherwise stopped only by its prompt. Same rule as secret_read.
    owner_only: bool = False

    @property
    def no_auto(self) -> bool:
        """True when neither auto-approve nor an "Always" rule may answer this."""
        return self.secret_read or self.owner_only

    @property
    def needs_confirmation(self) -> bool:
        return self.danger >= Danger.GUARDED

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
    # The WhatsApp device session. Not merely inconvenient to lose: the file
    # *is* a full WhatsApp login, so it sits with the ssh and gpg keys rather
    # than with ordinary application data. Copying it is impersonating the
    # linked device, and no confirmation makes that a reasonable thing for a
    # model to be doing on its own initiative.
    config.DATA_DIR / "whatsapp",
}


def resolve_target(raw: str | os.PathLike) -> Path:
    """Resolve a path without following a final symlink into a new location."""
    p = Path(os.path.expandvars(str(raw))).expanduser()
    try:
        # strict=False so non-existent targets (e.g. a file to create) work.
        return p.resolve(strict=False)
    except (OSError, RuntimeError):
        return p.absolute()


def _kravein_private_roots() -> list[Path]:
    """Kravein's own config, state and data dirs: keys, pairings, tokens, policy.

    The workspace under the data dir is the one place the assistant is meant
    to write, so it is carved out.
    """
    return [config.CONFIG_DIR, config.STATE_DIR, config.DATA_DIR]


def _login_surface_paths() -> list[Path]:
    """Files and dirs where a silent edit grants access or runs code later.

    ssh/gpg keys, shell startup files, user systemd units and autostart
    entries all execute or authenticate without the owner being present, so a
    one-line unconfirmed edit there is a persistent foothold.
    """
    home = Path.home()
    names = [".ssh", ".gnupg", ".bashrc", ".bash_profile", ".bash_login",
             ".bash_logout", ".zshrc", ".zshenv", ".zprofile", ".zlogin",
             ".profile", ".config/fish", ".config/systemd/user",
             ".config/autostart"]
    return [home / n for n in names]


def _inside(target: Path, root: Path) -> bool:
    try:
        return target == root or target.is_relative_to(root)
    except (OSError, ValueError):
        return False


def sensitive_kind(raw: str | os.PathLike) -> str:
    """Classify a path for secrecy/persistence: ``"blocked"``, ``"confirm"`` or ``""``.

    ``blocked``: Kravein's own config/state/data (API keys, phone pairings, web
    tokens, the safety policy). ``confirm``: login/startup surfaces. Shared by
    ``check_path`` (writes) and by skills that must not read secrets into the
    model context (``search_text``).
    """
    target = resolve_target(raw)
    workspace = resolve_target(config.WORKSPACE_DIR)
    if not _inside(target, workspace):
        for root in _kravein_private_roots():
            if _inside(target, resolve_target(root)):
                return "blocked"
    for root in _login_surface_paths():
        if _inside(target, resolve_target(root)):
            return "confirm"
    return ""


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

    # The settings file holds the safety policy itself (protected paths, the
    # confirmation mode). A plain file write is only MODERATE, so without this
    # the model could loosen its own guardrails with one unconfirmed write_file.
    if target == resolve_target(config.settings_path()):
        verdict.blocked = True
        verdict.danger = Danger.CRITICAL
        verdict.block_reason = (
            f"{target} is Kravein's own settings file; the assistant may not "
            "change its own safety settings"
        )
        return verdict

    kind = sensitive_kind(target)
    if kind == "blocked":
        verdict.blocked = True
        verdict.danger = Danger.CRITICAL
        verdict.block_reason = (
            f"{target} is inside Kravein's own config/state/data, which holds "
            "keys, pairings and its safety policy; the assistant may not write there"
        )
        return verdict
    if kind == "confirm":
        verdict.danger = max(verdict.danger, Danger.DESTRUCTIVE)
        verdict.reasons.append(
            f"{target} is a login/startup location (ssh, gpg, shell startup, "
            "systemd user, autostart)"
        )

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


# -- reading secrets (#211) ------------------------------------------------------
#
# Everything a skill or a shell command reads goes back into the model's
# context, which is a cloud provider. `read_file` had no guard and
# `run_command cat ~/.config/kravein/api_keys.json` was SAFE, so a prompt
# injection (or a plain curious model) could ship the owner's keys off the
# machine without a card. `sensitive_kind` already covered the *write* side;
# this is the read side: Kravein's own dirs are refused, a curated list of
# secret locations asks, and the card says the content goes to the model.

_SSH_PUBLIC = frozenset({"known_hosts", "config"})
# Template files hold names, not values; asking on them every time trains the
# owner to click through the cards that matter.
_ENV_TEMPLATE_SUFFIXES = frozenset({"example", "sample", "template", "dist", "tmpl"})


def _secret_locations() -> list[Path]:
    home = Path.home()
    return [home / ".ssh", home / ".gnupg", home / ".password-store", home / ".netrc",
            home / ".aws" / "credentials", home / ".config" / "gh" / "hosts.yml",
            home / ".docker" / "config.json", home / ".kube" / "config"]


def _either_inside(candidates: Sequence[Path], root: Path) -> bool:
    """`root` is checked as written and resolved, so a symlinked home dir or a
    symlinked secret location is matched whichever side the link is on."""
    roots = {root, resolve_target(root)}
    return any(_inside(c, r) for c in candidates for r in roots)


def _read_candidates(raw: str | os.PathLike) -> list[Path]:
    """The path as written (normalised) and where its symlinks lead. Matching
    only the resolved side misses `~/.ssh/id -> /tmp/x`; only the lexical side
    misses `/tmp/innocent -> ~/.ssh/id`."""
    lexical = Path(os.path.expandvars(str(raw))).expanduser()
    if not lexical.is_absolute():
        lexical = Path.cwd() / lexical
    lexical = Path(os.path.normpath(lexical))
    resolved = resolve_target(raw)
    return [lexical] if lexical == resolved else [lexical, resolved]


def _blocked_private(cands: Sequence[Path]) -> bool:
    workspace = resolve_target(config.WORKSPACE_DIR)
    if any(_inside(c, workspace) for c in cands):
        return False
    return any(_either_inside(cands, root) for root in _kravein_private_roots())


def _secret_reason(cands: Sequence[Path]) -> str:
    """Why reading this would hand a secret to the model, or ""."""
    for p in cands:
        if p.name.endswith(".pub"):
            return ""                       # public keys are public
    ssh = Path.home() / ".ssh"
    for p in cands:
        if p.parent in (ssh, resolve_target(ssh)) and p.name in _SSH_PUBLIC:
            return ""
    for root in _secret_locations():
        if _either_inside(cands, root):
            return f"{root} holds credentials or private keys"
    for p in cands:
        name = p.name
        if name == ".env" or (name.startswith(".env.")
                              and name.rsplit(".", 1)[-1] not in _ENV_TEMPLATE_SUFFIXES):
            return f"{name} is an environment file that usually holds secrets"
        if name.endswith((".pem", ".key")) or name.startswith("id_"):
            return f"{name} looks like a private key"
    return ""


def check_read(raw: str | os.PathLike) -> Verdict:
    """Classify reading a file into the model's context.

    Blocked: Kravein's own config/state/data. DESTRUCTIVE (a card): a curated
    list of secret locations, ``.env`` files and private-key files. SAFE:
    everything else. Symlinks are followed before matching. The verdict never
    carries an "Always" signature - a secret read is asked every time.
    """
    cands = _read_candidates(raw)
    target = cands[-1]
    if _blocked_private(cands):
        return Verdict(
            danger=Danger.CRITICAL, blocked=True,
            block_reason=(
                f"{target} is inside Kravein's own config/state/data, which holds "
                "keys, pairings and its safety policy; the assistant may not read it"
            ),
        )
    why = _secret_reason(cands)
    if why:
        return Verdict(
            danger=Danger.DESTRUCTIVE,
            reasons=[f"reads {target}: {why}; its content will be sent to the model"],
            secret_read=True,
        )
    return Verdict(danger=Danger.SAFE)


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
    # Owner rule: no autostart. These create a unit, a timer or a cron entry that
    # runs later without the owner there; restart/status/start/stop stay as they are.
    (r"\bsystemctl\b(?=[^|;\n]*--user)[^|;\n]*\b(enable|reenable|link|preset|"
     r"add-wants|add-requires|revert|edit|set-default)\b", "creates or enables a user unit"),
    (r"\bsystemd-run\b", "starts a transient unit or timer"),
    (r"\bcrontab\b(?!\s+-l\b)", "changes scheduled jobs"),
    (r"\bloginctl\s+enable-linger\b", "keeps user services running after logout"),
]

_MODERATE_PATTERNS = [
    (r"\b(pip|npm|cargo|go)\s+install\b", "installs packages"),
    (r"\b(tee|cp|install)\b", "writes files"),
    (r">>", "appends to a file"),
    (r"\bsystemctl\s+--user\s+(start|stop|restart)\b", "changes a user service"),
    (r"\bgit\s+(commit|push|checkout|merge|rebase)\b", "changes repository state"),
]


# Words that can rewrite a file without a redirect, a verb the pattern lists
# above classify, or any visible "write" at all (`vim`, `python -c`, `ln -sf`).
_REWRITER = re.compile(
    r"\b(python3?|perl|ruby|node|php|lua|awk|gawk|vi|vim|nvim|nano|emacs|ed|ex|"
    r"ln|patch|install|dd|eval|source|rsync|scp|tar|unzip|cpio|curl|wget)\b",
    re.IGNORECASE,
)


def _mentions_settings_file(command: str) -> bool:
    """See :func:`_mentions_file`; the target is Kravein's settings file."""
    return _mentions_file(command, config.settings_path())


_PLAIN_READERS = frozenset({"cat", "head", "tail", "less", "more", "grep", "rg", "wc",
                            "jq", "stat", "ls", "file", "diff", "cmp", "sha256sum", "md5sum"})


def _mentions_mcp_config(command: str) -> bool:
    """Our mcp.json (or its lock/tmp, or the config dir) by path or bare name.
    Not a substring: `.mcp.json` / `.vscode/mcp.json` in the owner's own
    projects are other files and must stay editable."""
    base = Path(config.CONFIG_DIR)
    return any(_mentions_file(command, base / name)
               for name in ("mcp.json", "mcp.json.lock", "mcp.json.tmp"))


def _plain_read(command: str) -> bool:
    """Every segment is a known reader with no redirect (nothing written)."""
    segments = _segments(command)
    if not segments:
        return False
    for argv, redirects in segments:
        if redirects or not argv or argv[0] not in _PLAIN_READERS:
            return False
    return True


def _mentions_file(command: str, target_path: "str | os.PathLike") -> bool:
    """Does a shell command name Kravein's own settings file?

    check_path() already refuses write_file/copy/move onto it, but the same
    write through `run_command` ("sed -i", "> file", "cp x file", "tee") only
    scored DESTRUCTIVE, which the owner can approve - and auto-approve (#35)
    would do so unasked. So the shell path gets the same hard refusal.

    A glob that matches the file, or the file's directory, counts too.
    Text matching cannot see a path assembled at run time (`f=sett; ${f}ings.json`);
    this closes the ordinary spellings, not a model set on obfuscation. A bare
    `settings.json` is treated as ours because the working directory is not
    known here and the safe error is to refuse.
    """
    target = resolve_target(target_path)
    candidates: list[str] = []
    try:
        candidates += shlex.split(command, comments=True)
    except ValueError:
        candidates += command.split()
    # Redirects and operators glue paths to tokens: `>~/x`, `of=/x`, `cmd;/x`.
    for token in list(candidates):
        candidates += [t for t in re.split(r"[;&|<>()=,:'\"]", token) if t]
    for token in candidates:
        if token == target.name:
            return True
        expanded = os.path.expandvars(token)
        if expanded.startswith("~"):
            expanded = os.path.expanduser(expanded)
        # Brace expansion (`safety_r{ules,x}.json`) is a glob for our purposes.
        expanded = re.sub(r"\{[^{}]*\}", "*", expanded)
        if any(c in expanded for c in "*?["):
            # `~/.config/kravein/*.json` names the file without spelling it.
            # Only a glob with a directory part can be placed without knowing
            # the working directory.
            if "/" in expanded and fnmatch.fnmatchcase(
                str(target), os.path.normpath(expanded)
            ):
                return True
            continue
        if not expanded.startswith(("/", ".")) and "/" not in expanded:
            continue
        resolved = resolve_target(expanded)
        # The directory itself: `cp -r x dir/`, `rsync x dir/`, or
        # `cd dir && sed -i … *` all rewrite the file without naming it.
        if resolved == target or resolved == target.parent:
            return True
    return False


def _classify_level(text: str) -> Danger:
    """Highest pattern level a command matches (no path checks)."""
    level = Danger.SAFE
    for lvl, patterns in (
        (Danger.CRITICAL, _CRITICAL_PATTERNS),
        (Danger.DESTRUCTIVE, _DESTRUCTIVE_PATTERNS),
        (Danger.MODERATE, _MODERATE_PATTERNS),
    ):
        if any(re.search(p, text, re.IGNORECASE) for p, _ in patterns):
            level = max(level, lvl)
    return level


def classify_command(
    command: str, cwd: "str | os.PathLike | None" = None, *, _depth: int = 0
) -> Verdict:
    """Classify a free-form shell command string.

    ``cwd`` is the directory the command will run in. Without it a relative
    path cannot be resolved, so relative targets are never treated as "new"
    (the old classification stands) and never get an "Always" signature.
    """
    text = (command or "").strip()
    verdict = Verdict(danger=Danger.SAFE)
    if not text:
        return verdict
    where = Path(cwd) if cwd is not None else None

    for pattern, why in _FORBIDDEN:
        if re.search(pattern, text, re.IGNORECASE):
            verdict.blocked = True
            verdict.danger = Danger.CRITICAL
            verdict.block_reason = why
            return verdict

    # A command this long is not typed by anyone and the analysis below is not
    # free: ask instead of parsing it (a 100 KB wrapper chain stalled the worker).
    if len(text) > _MAX_COMMAND_CHARS:
        verdict.danger = Danger.DESTRUCTIVE
        verdict.reasons.append("command is unusually long")
        return verdict

    # Writing Kravein's own settings through the shell is refused outright, as
    # check_path() does for file skills. Reading it (cat, grep) stays allowed.
    if _mentions_settings_file(text) and (
        _classify_level(text) >= Danger.MODERATE or _REWRITER.search(text)
    ):
        verdict.blocked = True
        verdict.danger = Danger.CRITICAL
        verdict.block_reason = (
            f"{config.settings_path()} is Kravein's own settings file; the "
            "assistant may not change its own safety settings"
        )
        return verdict

    # The MCP server config (#243) holds the owner's approvals and pins: whoever
    # writes it decides which outside tools run. Stricter than the settings
    # file: any command naming it is refused unless it is provably a plain
    # read (an inline `python3 -c "open(...,'w')"` only scored DESTRUCTIVE).
    # Only when the FILE is named: a command on the config directory itself
    # (`du -sh ~/.config/kravein`) is judged by the ordinary rules, and writes
    # there are refused by check_path anyway.
    if "mcp.json" in text and _mentions_mcp_config(text) and not _plain_read(text):
        verdict.blocked = True
        verdict.danger = Danger.CRITICAL
        verdict.block_reason = (
            "the MCP server config is the owner's: servers and their tools are "
            "added and approved only with `kravein mcp`"
        )
        return verdict

    # The Always-rule store is the owner's, like the settings file: only a card
    # click may add to it. Reading it stays allowed.
    if (RULES_FILENAME in text or _mentions_file(text, _rules_path())) and (
        _classify_level(text) >= Danger.MODERATE or _REWRITER.search(text)
    ):
        verdict.blocked = True
        verdict.danger = Danger.CRITICAL
        verdict.block_reason = (
            f"{RULES_FILENAME} holds the owner's \"Always approve\" rules; the "
            "assistant may not change them"
        )
        return verdict

    # A secret read is refused (Kravein's own dirs) or asks (ssh keys, .env, ...)
    # whatever the program: `cat` is in the safe set, which is exactly why this
    # runs before it. A secret read never gets an "Always" signature.
    read_blocked, secret_reason = _read_findings(text, where)
    if read_blocked:
        verdict.blocked = True
        verdict.danger = Danger.CRITICAL
        verdict.block_reason = read_blocked
        return verdict

    # Provably read-only (or creating only new files): no card. Runs after the
    # hard refusals above, and never lowers a CRITICAL pattern hit.
    if (not secret_reason and _classify_level(text) < Danger.CRITICAL
            and _argv_safe(text, where)):
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
    if _overwrites_existing(text, where):
        matched.append((Danger.DESTRUCTIVE, "overwrites an existing file"))
    interp_blocked, interp_danger, interp_reason, _ = _interpreter_findings(text, where, _depth)
    if interp_blocked:
        verdict.blocked = True
        verdict.danger = Danger.CRITICAL
        verdict.block_reason = interp_blocked
        return verdict
    if interp_danger >= Danger.MODERATE:
        matched.append((interp_danger, interp_reason or _INLINE_REASON))
    blocked_reason, startup_reason = _write_findings(text, where)
    if blocked_reason:
        verdict.blocked = True
        verdict.danger = Danger.CRITICAL
        verdict.block_reason = blocked_reason
        return verdict
    if startup_reason:
        matched.append((Danger.DESTRUCTIVE, startup_reason))
    if secret_reason:
        matched.append((Danger.DESTRUCTIVE, secret_reason))

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

    # Never "Always" for anything that touches a startup surface or creates a unit.
    if secret_reason:
        verdict.secret_read = True
    if verdict.danger == Danger.DESTRUCTIVE and not startup_reason and not secret_reason:
        verdict.signature = command_signature(text, where)
    return verdict


def _candidate_paths(command: str) -> list[str]:
    """Pull absolute-looking path arguments out of a command string."""
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError:
        tokens = command.split()
    return [t for t in tokens if t.startswith("/") and len(t) > 1]


# ---------------------------------------------------------------------------
# argv-parsed shell analysis (#199): the safe set and "Always" signatures
# ---------------------------------------------------------------------------
#
# The regex classifier above over-asks (`ls 2>/dev/null` was DESTRUCTIVE, and
# `ls 2> /dev/null` was refused outright) and cannot say "this really is just a
# read". This parser answers the second question from the argv. It only ever
# *lowers* a verdict, so every rule is default-deny: anything it does not fully
# understand (substitution, variables, heredocs, newlines, an unknown program,
# a relative path whose directory it cannot know) is "not safe" and the old
# classification stands unchanged.

_SAFE_PROGRAMS = frozenset({
    "ls", "cd", "pwd", "cat", "head", "tail", "less", "wc", "find", "grep", "rg",
    "which", "whereis", "type", "file", "stat", "du", "df", "ps", "pgrep", "echo",
    "printf", "date", "uname", "whoami", "id", "env", "tree", "realpath",
    "basename", "dirname", "sort", "uniq", "diff", "git",
})
_GIT_SAFE_SUBCOMMANDS = frozenset({"status", "log", "diff", "show"})
# Per-program flags that turn a read into an execution or a write.
_FIND_UNSAFE = frozenset({
    "-delete", "-exec", "-execdir", "-ok", "-okdir",
    "-fprint", "-fprint0", "-fprintf", "-fls",
})
_OPERATORS = frozenset({";", "&&", "||", "|"})
_REDIRECTS = frozenset({">", ">>", "&>", "&>>", ">&", "<"})
_GLOB_CHARS = set("*?[]{}")


def _shell_tokens(command: str) -> list[str] | None:
    """Split into words and operators, or None if it is not plainly parseable."""
    # `$` and backtick: substitution/variables hide the real argv. Newline would
    # be whitespace to shlex but a command separator to the shell.
    if any(c in command for c in "\n\r\x00$`"):
        return None
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    # No comments: shlex would cut `echo a#b; rm x` at the `#`, hiding the
    # `rm` the shell goes on to run.
    lex.commenters = ""
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:
        return None


def shell_tokens(command: str) -> list[str] | None:
    """Public form of the classifier's tokenizer, for skills that must read a
    command the same way ``classify_command`` does (``cd`` tracking in
    ``run_command``). Words and operators (``;`` ``&&`` ``|`` ``>`` ...), or None
    when the command is not plainly parseable (``$VAR``, backticks, newlines,
    unbalanced quotes) - treat None as "cannot tell", never as "safe"."""
    return _shell_tokens(command)


def _segments(command: str) -> list[tuple[list[str], list[tuple[str, str]]]] | None:
    """`[(argv, [(redirect_op, target), ...]), ...]`, one per simple command."""
    tokens = _shell_tokens(command)
    if not tokens:
        return None
    segments: list[tuple[list[str], list[tuple[str, str]]]] = []
    argv: list[str] = []
    redirects: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _OPERATORS:
            if not argv:
                return None
            segments.append((argv, redirects))
            argv, redirects = [], []
        elif tok in _REDIRECTS:
            if i + 1 >= len(tokens) or tokens[i + 1] in _OPERATORS | _REDIRECTS:
                return None
            redirects.append((tok, tokens[i + 1]))
            i += 1
        elif tok and all(c in "();<>|&" for c in tok):
            return None            # subshell, heredoc, background, `>|`, ...
        else:
            argv.append(tok)
        i += 1
    if argv:
        segments.append((argv, redirects))
    elif redirects or tokens[-1] != ";":
        return None                 # a bare redirect, or a dangling `|` / `&&` / `||`
    return segments or None


def _literal_path(raw: str, cwd: Path | None) -> Path | None:
    """The absolute path a literal argument names, or None if it cannot be
    known here (glob, or relative with an unknown working directory)."""
    if not raw or any(c in _GLOB_CHARS for c in raw):
        return None
    expanded = os.path.expanduser(raw) if raw.startswith("~") else raw
    if expanded.startswith("~"):
        return None
    path = Path(expanded)
    if not path.is_absolute():
        if cwd is None:
            return None
        path = cwd / path
    return Path(os.path.normpath(path))


def _writable_ok(path: Path) -> bool:
    """check_path says an ordinary write there needs no confirmation."""
    v = check_path(path, write=True)
    return not v.blocked and v.danger < Danger.DESTRUCTIVE


def _risky_flag(args: list[str], short: str, long_names: tuple[str, ...]) -> bool:
    """Does any argument carry one of these flags, however it is spelled?

    GNU tools accept bundled short flags (`tree -ao FILE`) and any unambiguous
    prefix of a long option (`sort --out=FILE`), so an exact-string veto misses
    both. Conservative on purpose: a short cluster that merely contains the
    letter, or a long option that is a prefix of the dangerous one, counts.
    """
    for a in args:
        if a.startswith("--"):
            name = a.split("=", 1)[0]
            if len(name) >= 3 and any(n.startswith(name) for n in long_names):
                return True
        elif a.startswith("-") and len(a) > 1 and any(ch in a[1:] for ch in short):
            return True
    return False


# Programs whose flags can execute or write: an unquoted glob that starts a
# word could expand into one (a file literally named `-delete` or `--pre=sh`).
_FLAG_SENSITIVE = frozenset({"find", "rg", "sort", "tree", "less", "git", "file", "date"})


def _flags_ok(prog: str, args: list[str]) -> bool:
    """Per-program veto of flags that execute or write."""
    # Brace expansion builds flags out of thin air: `find . {-delete,x}`.
    if any("{" in a and ("," in a or ".." in a) for a in args):
        return False
    if prog in _FLAG_SENSITIVE and any(a[:1] in ("*", "?", "[") for a in args):
        return False
    if prog == "find":
        return not any(a in _FIND_UNSAFE for a in args)
    if prog == "sort":
        return not _risky_flag(args, "o", ("--output", "--compress-program", "--files0-from"))
    if prog == "rg":
        return not any(a.startswith(("--pre", "--hostname-bin")) for a in args)
    if prog == "less":
        return not any(a.startswith("+") for a in args) and not _risky_flag(
            args, "oO", ("--log-file", "--LOG-FILE")
        )
    if prog == "tree":
        return not _risky_flag(args, "o", ("--output",))
    if prog == "date":
        return not _risky_flag(args, "s", ("--set",))
    if prog == "file":
        return not _risky_flag(args, "C", ("--compile",))
    if prog == "env":
        return not args                       # `env CMD` runs CMD
    if prog == "uniq":
        return len([a for a in args if not a.startswith("-")]) <= 1  # 2nd = output file
    if prog == "git":
        i = 0
        while i < len(args) and args[i].startswith("-"):
            if args[i] != "-C" or i + 1 >= len(args):
                return False                  # -c, --exec-path, ... change what runs
            i += 2
        if i >= len(args) or args[i] not in _GIT_SAFE_SUBCOMMANDS:
            return False
        return not _risky_flag(args[i + 1:], "", ("--output", "--ext-diff", "--open-files-in-pager"))
    return True


_CP_FLAGS = frozenset({"-v", "-n", "-r", "-R", "-a", "-p", "--verbose",
                       "--no-clobber", "--recursive", "--archive"})
_MV_FLAGS = frozenset({"-v", "-n", "--verbose", "--no-clobber"})


def _copy_move_state(prog: str, args: list[str], cwd: Path | None) -> str:
    """"new" (nothing is overwritten), "exists" (an existing target would be
    overwritten) or "unknown" (cannot tell: stays under the old classification)."""
    allowed = _CP_FLAGS if prog == "cp" else _MV_FLAGS
    if any(a.startswith("-") and a not in allowed for a in args):
        return "unknown"
    pos = [a for a in args if not a.startswith("-")]
    if len(pos) < 2:
        return "unknown"
    paths = [_literal_path(p, cwd) for p in pos]
    if any(p is None for p in paths):
        return "unknown"
    *srcs, dest = paths                                    # type: ignore[misc]
    if not _writable_ok(dest) or not all(
        (_writable_ok(s) if prog == "mv" else not check_path(s, write=False).blocked)
        for s in srcs
    ):
        return "unknown"
    if "-n" in args or "--no-clobber" in args:
        return "new"
    if os.path.lexists(dest):
        if dest.is_dir() and not dest.is_symlink():
            effective = [dest / s.name for s in srcs]
        else:
            effective = [dest]
        return "exists" if any(os.path.lexists(e) for e in effective) else "new"
    return "new" if len(srcs) == 1 else "unknown"


def _redirects_ok(redirects: list[tuple[str, str]], cwd: Path | None) -> bool:
    for op, target in redirects:
        if op == "<":
            continue
        if op == ">&" and (target.isdigit() or target == "-"):
            continue                                       # 2>&1: no file involved
        if target == "/dev/null":
            continue
        path = _literal_path(target, cwd)
        # Only a file that does not exist yet: an existing one is overwritten
        # (or appended to), which is exactly what must still ask.
        if path is None or os.path.lexists(path) or not _writable_ok(path):
            return False
    return True


def _argv_safe(command: str, cwd: Path | None) -> bool:
    """True only if every part of the command is provably read-only or creates
    only files that do not exist yet."""
    segments = _segments(command)
    if segments is None:
        return False
    for argv, redirects in segments:
        prog = argv[0]
        if "/" in prog or "=" in prog:
            return False
        if prog in _SHELLS:
            kind, inner = _shell_inline(argv[1:])
            if kind != "c" or inner is None or argv[1:] != ["-c", inner] or not _argv_safe(inner, cwd):
                return False
        elif prog in ("cp", "mv"):
            if _copy_move_state(prog, argv[1:], cwd) != "new":
                return False
        elif prog in _SAFE_PROGRAMS:
            if not _flags_ok(prog, argv[1:]):
                return False
        else:
            return False
        if not _redirects_ok(redirects, cwd):
            return False
        if prog == "cd":
            cwd = None          # later relative paths are relative to somewhere else
    return True


def _overwrites_existing(command: str, cwd: Path | None) -> bool:
    """`cp` onto an existing target: the regex classifier calls cp MODERATE."""
    segments = _segments(command)
    if segments is None:
        return False
    for argv, _ in segments:
        if argv[0] == "cp" and _copy_move_state("cp", argv[1:], cwd) == "exists":
            return True
        if argv[0] == "cd":
            cwd = None
    return False



# -- write targets (#199 review): every shell write goes through check_path ----
#
# The regex classifier only looked at verbs, so `cp x ~/.config/systemd/user/e.service`
# (MODERATE) landed a persistent user unit with no card - the class #136 put into
# sensitive_kind(). Every path a command writes to now goes through check_path,
# whatever the verb and whether or not the target exists: a login/startup
# surface means at least DESTRUCTIVE (a card, never "Always"); Kravein's own
# dirs and protected system paths are refused.

_WRAPPERS = frozenset({"env", "nice", "nohup", "time", "command", "exec", "busybox",
                       "ionice", "setsid", "stdbuf", "timeout"})
_DEST_LAST = frozenset({"cp", "install", "ln", "rsync", "scp"})
_ALL_WRITTEN = frozenset({"tee", "touch", "mkdir", "mkfifo", "mknod", "truncate", "mv"})
_DEV_OK = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"})


def _surface_fragments() -> tuple[list[str], list[str]]:
    """(login/startup fragments, Kravein-private fragments), home-relative, for
    commands whose paths cannot be resolved (globs, braces, relative, `$VAR`)."""
    home = Path.home()
    login = [str(p.relative_to(home)) for p in _login_surface_paths()]
    private: list[str] = []
    for root in _kravein_private_roots():
        try:
            private.append(str(Path(root).relative_to(home)))
        except ValueError:
            private.append(str(root))
    return login, private


def _surface_mention(text: str) -> tuple[str, str]:
    """(blocked reason, confirm reason) for a string that names a sensitive place."""
    login, private = _surface_fragments()
    for frag in private:
        if frag and frag in text:
            return (f"{frag} is inside Kravein's own config/state/data; the "
                    "assistant may not write there"), ""
    for frag in login:
        if frag in text or (frag.endswith("systemd/user") and "systemd/user" in text) or (
                frag.endswith("autostart") and "autostart" in text):
            return "", f"writes to a login/startup location ({frag})"
    return "", ""


def _glob_surface_hit(raw: str) -> tuple[str, str]:
    """`~/.config/sys*/user/x`: could this glob'd directory part be a sensitive place?

    Matched segment by segment against every login surface and Kravein dir; a
    glob that is shallower than the surface (`~/*/`) counts too. Over-asking on
    a glob destination is the safe error.
    """
    expanded = os.path.expanduser(raw) if raw.startswith("~") else raw
    if not any(c in expanded for c in "*?[") or "/" not in expanded:
        return "", ""
    parts = expanded.rsplit("/", 1)[0].split("/")
    if not parts or parts[0] != "":
        return "", ""                    # relative glob: the textual fragments decide
    def hits(root: Path) -> bool:
        rparts = str(resolve_target(root)).split("/")
        return all(fnmatch.fnmatchcase(rp, p) for rp, p in zip(rparts, parts))
    if any(hits(r) for r in _kravein_private_roots()):
        return "that glob can reach Kravein's own config/state/data", ""
    if any(hits(r) for r in _login_surface_paths()):
        return "", "writes to a login/startup location (glob)"
    return "", ""


def _segment_write_targets(argv: list[str], redirects: list[tuple[str, str]]) -> list[str]:
    out = [
        t for op, t in redirects
        if op != "<" and not (op == ">&" and (t.isdigit() or t == "-")) and t not in _DEV_OK
    ]
    args = list(argv)
    while args and os.path.basename(args[0]) in _WRAPPERS:
        args = args[1:]
        while args and ("=" in args[0] or args[0].startswith("-")):
            args = args[1:]
    if not args:
        return out
    prog, rest = os.path.basename(args[0]), args[1:]
    pos = [a for a in rest if not a.startswith("-")]
    for i, a in enumerate(rest):
        if a in ("-t", "--target-directory") and i + 1 < len(rest):
            out.append(rest[i + 1])
        elif a.startswith("--target-directory="):
            out.append(a.split("=", 1)[1])
    if prog in _DEST_LAST:
        out += pos[-1:]
    elif prog in _ALL_WRITTEN:
        out += pos
    elif prog == "dd":
        out += [a[3:] for a in rest if a.startswith("of=")]
    elif prog in ("curl", "wget"):
        for i, a in enumerate(rest):
            if a in ("-o", "-O", "--output", "--output-document", "-P", "--directory-prefix"):
                if i + 1 < len(rest):
                    out.append(rest[i + 1])
            elif a.startswith(("--output=", "--output-document=", "--directory-prefix=")):
                out.append(a.split("=", 1)[1])
    return [t for t in out if t not in _DEV_OK]


def _write_findings(text: str, cwd: Path | None) -> tuple[str, str]:
    """(blocked reason, confirm reason); ("", "") when every write target is ordinary."""
    segments = _segments(text)
    if segments is None:
        # Cannot see the argv ($VAR, substitution, heredoc, newline...). Only a
        # command that looks like it writes is held to the textual check.
        writes = _classify_level(text) >= Danger.MODERATE or bool(_REWRITER.search(text)) or ">" in text
        return _surface_mention(text) if writes else ("", "")
    tainted = False
    for argv, redirects in segments:
        if os.path.basename(argv[0]) == "cd":
            where = argv[1] if len(argv) > 1 else "~"
            resolved = _literal_path(where, cwd)
            if resolved is None:
                tainted = tainted or any(_surface_mention(where))
                cwd = None
            else:
                cwd = resolved
        for raw in _segment_write_targets(argv, redirects):
            path = _literal_path(raw, cwd)
            if path is None:
                blocked, confirm = _surface_mention(raw)
                if not (blocked or confirm):
                    blocked, confirm = _glob_surface_hit(raw)
                if blocked:
                    return blocked, ""
                if confirm or (tainted and not raw.startswith(("/", "~"))):
                    return "", confirm or "writes under a login/startup location"
                continue
            verdict = check_path(path, write=True)
            if verdict.blocked:
                return verdict.block_reason, ""
            if verdict.danger >= Danger.DESTRUCTIVE:
                return "", "; ".join(verdict.reasons) or "writes to a login/startup location"
    return "", ""


# -- secret reads in shell commands (#211) -------------------------------------
#
# `cat`, `head`, `base64`, `grep -r`, `cp ~/.ssh/id /tmp/x && cat /tmp/x`,
# `< secret`: any program that is handed a secret path can put its content in
# the output the model sees, so every argv token of every program is checked,
# not only the verbs we think of as readers. Programs that only list or stat
# (ls, find without -exec, ...) and `rm`-style programs never print content.

_NAME_ONLY = frozenset({
    "ls", "stat", "du", "df", "tree", "realpath", "basename", "dirname", "wc",
    "pwd", "cd", "test", "[", "which", "whereis", "type", "echo", "printf", "find",
    "touch", "mkdir", "rmdir", "rm", "unlink", "chmod", "chown", "chgrp",
})
# Recursive readers: handed a directory that *contains* a secret location they
# read it too, so `grep -r token ~/.config` is the same read as `cat` of the key.
_RECURSIVE_READERS = frozenset({
    "grep", "egrep", "fgrep", "rg", "ag", "ack", "tar", "zip", "rsync", "cp", "scp",
})
_SECRET_FRAGMENTS = (".ssh", ".gnupg", ".password-store", ".netrc", ".aws/credentials",
                     "gh/hosts.yml", ".docker/config.json", ".kube/config")
_MAX_READ_CANDIDATES = 200


def _brace_expand(s: str, limit: int = 64) -> list[str]:
    """Minimal `a{b,c}d` expansion (no nesting); a bad pattern comes back whole."""
    m = re.search(r"\{([^{}]*,[^{}]*)\}", s)
    if not m:
        return [s]
    out: list[str] = []
    for alt in m.group(1).split(","):
        out.extend(_brace_expand(s[:m.start()] + alt + s[m.end():], limit))
        if len(out) >= limit:
            break
    return out[:limit]


def _read_paths(tok: str, cwd: Path | None) -> list[Path]:
    """Existing paths this argument can name (globs and braces expanded), plus
    - when the working directory is unknown - the bare relative name, so a
    `.env` or `id_rsa` is still recognised by its name."""
    strings = [tok]
    if tok[:1] == "@":                      # curl -d @FILE reads FILE
        strings = [tok[1:]]
    if "=" in tok:                          # --file=PATH, if=PATH, of=PATH
        strings.append(tok.split("=", 1)[1])
    elif tok.startswith("-"):
        return []
    found: list[Path] = []
    for s in strings:
        for variant in _brace_expand(s):
            if variant.startswith("@"):
                variant = variant[1:]
            if not variant or variant.startswith("-"):
                continue
            expanded = os.path.expanduser(variant) if variant.startswith("~") else variant
            if expanded.startswith("~"):
                continue
            path = Path(expanded)
            if not path.is_absolute():
                if cwd is None:
                    # A word with spaces is a command string, not a file name
                    # (`sh -c 'cat x'`): its inner string is scanned on its own.
                    if not any(c.isspace() for c in variant):
                        found.append(path)
                    continue
                path = cwd / path
            path = Path(os.path.normpath(path))
            if any(c in _GLOB_CHARS for c in str(path)):
                import glob
                try:
                    hits = glob.glob(str(path))[:_MAX_READ_CANDIDATES]
                except OSError:
                    hits = []
                found.extend(Path(h) for h in hits)
            elif os.path.lexists(path):
                found.append(path)
    return found


def _read_finding(path: Path, recursive: bool) -> tuple[str, str]:
    """(blocked reason, confirm reason) for one path an argv token names."""
    if path.is_absolute():
        v = check_read(path)
        if v.blocked:
            return v.block_reason, ""
        if v.reasons:
            return "", "; ".join(v.reasons)
        if recursive and path.is_dir():
            cands = _read_candidates(path)
            roots = [*_kravein_private_roots(), *_secret_locations()]
            for root in roots:
                for r in {root, resolve_target(root)}:
                    if os.path.lexists(r) and any(r != c and r.is_relative_to(c) for c in cands):
                        return "", (f"{path} contains {root}, whose content would be "
                                    "sent to the model")
        return "", ""
    # Relative name with no known directory: only the name patterns can apply.
    why = _secret_reason([path])
    return "", (f"reads {path}: {why}; its content will be sent to the model" if why else "")


def _read_findings(text: str, cwd: Path | None, _depth: int = 0) -> tuple[str, str]:
    """(blocked reason, confirm reason) for secrets a command would read."""
    segments = _segments(text)
    confirm = ""
    if segments is None:
        # Substitution, a variable or a heredoc hides the argv: check the
        # tokens we can see (with $VAR expanded), then the bare fragments.
        try:
            words = shlex.split(text)
        except ValueError:
            words = text.split()
        for word in words:
            word = os.path.expandvars(word).strip("<>|&;()`")
            for path in _read_paths(word, cwd):
                blocked, why = _read_finding(path, True)
                if blocked:
                    return blocked, ""
                confirm = confirm or why
        if confirm:
            return "", confirm
        for frag in _surface_fragments()[1]:
            if frag and frag in text:
                return "", f"mentions {frag}, which holds Kravein's keys and pairings"
        for frag in _SECRET_FRAGMENTS:
            if frag in text:
                return "", f"mentions {frag}; reading it would send secrets to the model"
        return "", ""
    # `echo ~/.netrc | xargs cat`: the name-only program hands its output to a
    # reader, so nothing in a pipeline with xargs/parallel is name-only.
    feeds_reader = any(
        os.path.basename(a[0]) in ("xargs", "parallel") for a, _ in segments)
    for argv, redirects in segments:
        if os.path.basename(argv[0]) == "cd":
            where = argv[1] if len(argv) > 1 else "~"
            cwd = _literal_path(where, cwd)
        rest, _ = _strip_wrappers(list(argv))
        prog = os.path.basename(rest[0]) if rest else ""
        if prog in _SHELLS:
            kind, inner = _shell_inline(rest[1:])
            if kind == "c" and inner is not None and _depth < _MAX_DEPTH:
                blocked, why = _read_findings(inner, cwd, _depth + 1)
                if blocked:
                    return blocked, ""
                confirm = confirm or why
        names_only = (prog in _NAME_ONLY and not feeds_reader and not (
            prog == "find" and any(a in _FIND_UNSAFE for a in rest[1:])))
        tokens = [] if names_only else list(argv)
        tokens += [t for op, t in redirects if op == "<"]
        for tok in tokens:
            for path in _read_paths(tok, cwd):
                blocked, why = _read_finding(path, prog in _RECURSIVE_READERS)
                if blocked:
                    return blocked, ""
                confirm = confirm or why
    return "", confirm


# -- inline interpreter code (#206) -------------------------------------------
#
# `python3 -c 'import shutil; shutil.rmtree(...)'` classified SAFE and ran with
# no card: the verb list only knew shell tools, and hard-won-safety.md names
# interpreter one-liners as the obvious next workaround after a declined `rm`.
# Kravein cannot prove what arbitrary code does and does not try to parse per
# API: inline code (a -c/-e/-r flag, code on stdin, a heredoc, eval) is
# DESTRUCTIVE at minimum and never gets "Always". `bash -c STRING` is
# classified by its inner string. Running a script *file* keeps its old
# verdict - a residue: the file's contents are not read.

_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "ash", "fish", "xonsh", "csh", "tcsh"})
_INTERP_RE = re.compile(
    r"^(i?python[\d.]*|pythonw[\d.]*|pypy[\d.]*|node(?:js)?[\d.]*|deno|bun|ruby[\d.]*|irb|pry|"
    r"perl[\d.]*|php[\d.]*|lua[\d.]*|luajit[\d.]*|julia|groovy|jshell|Rscript|"
    r"awk|gawk|mawk|nawk|tclsh[\d.]*|osascript)$"
)
_INTERP_WRAPPERS = _WRAPPERS | {"xargs", "sudo", "doas"}
# Wrapper options that take a value, so the value is not mistaken for the program.
_WRAPPER_VALUED = {
    "nice": {"-n", "--adjustment"}, "ionice": {"-c", "-n", "-p", "--class", "--classdata"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"}, "stdbuf": {"-i", "-o", "-e"},
    "xargs": {"-I", "-i", "-n", "-L", "-P", "-s", "-d", "-E", "-a"},
    "env": {"-u", "-C", "--unset", "--chdir"},
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U"},
    "doas": {"-u", "-C"},
    "exec": {"-a"},
}
_CODE_MODULES = frozenset({"runpy", "code", "timeit", "cProfile", "profile", "trace", "pdb",
                           "idlelib", "pydoc", "IPython"})
_HELP_FLAGS = frozenset({"-V", "--version", "-h", "--help", "-version", "-v"})
_AWK_SIDE_EFFECTS = re.compile(r"system\s*\(|getline|\||>>?\s*[\"(A-Za-z_]|\bclose\s*\(|\bfflush\b")
_INLINE_REASON = "runs inline interpreter code"
_MAX_DEPTH = 3
_MAX_COMMAND_CHARS = 20000


_CONTROL_WORDS = frozenset({"if", "then", "else", "elif", "while", "until", "do", "!", "{",
                            "coproc", "time"})
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
_MAX_WRAPPERS = 40          # more than this is not a real command line: treat as inline code


def _strip_wrappers(args: list[str]) -> tuple[list[str], bool]:
    """Drop everything in front of the real program: `NAME=value` words, shell
    control words (`if/then/do/{/!`), and env/nohup/timeout/xargs/... with
    their options. Returns (rest, inline) - `env -S 'prog args'` is inline by
    construction. Index-based and capped: a 100 KB `xargs -I{} xargs ...` chain
    used to take over a minute (quadratic re-slicing)."""
    i, n, hops = 0, len(args), 0
    while i < n:
        hops += 1
        if hops > _MAX_WRAPPERS:
            return args[i:], True
        word = args[i]
        name = os.path.basename(word)
        if _ASSIGN_RE.match(word) or word in _CONTROL_WORDS:
            i += 1                                   # FOO=1 python3 ...  /  then python3 ...
            continue
        if name not in _INTERP_WRAPPERS:
            break
        i += 1
        while i < n and (args[i].startswith("-") or (name == "env" and "=" in args[i])):
            flag = args[i]
            i += 1
            if name == "command" and flag in ("-v", "-V"):
                return [], False                     # `command -v python3` looks, it does not run
            if name == "env" and (flag == "-S" or flag.startswith("--split-string")):
                return args[i:], True
            if flag in _WRAPPER_VALUED.get(name, ()) and i < n:
                i += 1
        if name == "timeout" and i < n:
            i += 1                                   # the duration
    return args[i:], False


def _scan_interpreter(prog: str, rest: list[str]) -> bool:
    """True if this interpreter invocation carries inline code or reads code
    from stdin. False for a script file, a module, or --version/--help."""
    if rest and all(a in _HELP_FLAGS for a in rest):
        return False
    base = re.sub(r"[\d.]+$", "", prog)
    i = 0
    if base.endswith("python") or base.startswith(("python", "pypy")):
        while i < len(rest):
            a = rest[i]
            if a == "-":
                return True
            if a.startswith("--"):
                i += 1
                continue
            if a.startswith("-"):
                if "m" in a[1:] and "c" not in a[1:]:
                    # -m MODULE is a script by our rule, except the modules whose
                    # whole job is to run code you hand them.
                    module = (rest[i + 1] if a.endswith("m") and i + 1 < len(rest)
                              else a[a.index("m") + 1:])
                    return module.split(".")[0] in _CODE_MODULES
                if "c" in a[1:]:
                    return True
                i += 2 if a in ("-W", "-X") else 1
                continue
            return False                              # a script file
        return True                                   # no script: reads stdin
    if base in ("node", "nodejs"):
        valued = {"-r", "--require", "--import", "--loader", "--experimental-loader"}
        while i < len(rest):
            a = rest[i]
            if a.startswith(("--eval", "--print")):
                return True
            if a in valued:
                i += 2
                continue
            if a.startswith("--"):
                i += 1
                continue
            if a.startswith("-") and len(a) > 1:
                if any(ch in a[1:] for ch in "ep"):
                    return True
                i += 1
                continue
            return a == "-"
        return True
    if base in ("deno", "bun"):
        return any(a in ("eval", "-e", "--eval", "-p", "--print") for a in rest) or not any(
            not a.startswith("-") for a in rest)
    if base in ("ruby", "irb", "perl", "php", "lua", "luajit", "tclsh", "osascript", "pry",
                "julia", "groovy", "jshell", "Rscript"):
        letters = {"ruby": "e", "irb": "", "perl": "eE", "php": "rRBE", "lua": "e",
                   "luajit": "e", "tclsh": "", "osascript": "e", "pry": "",
                   "julia": "eE", "groovy": "e", "jshell": "", "Rscript": "e"}[base]
        # Options that carry a value (attached or as the next word) and so can
        # contain the letter by accident: `perl -Mfeature`, `ruby -rjson`.
        attached = {"perl": ("-M", "-m", "-I", "-d", "-D", "-C"), "ruby": ("-I", "-r", "-C", "-x", "-K"),
                    "php": ("-d", "-c", "-z", "-f"), "lua": ("-l",), "luajit": ("-l",)}.get(base, ())
        separate = {"-I", "-r", "-d", "-c", "-z", "-l"} if base != "perl" else set()
        while i < len(rest):
            a = rest[i]
            if a == "-":
                return True
            if a.startswith("--"):
                if a.startswith(("--eval", "--run", "--exec")):
                    return True
                i += 1
                continue
            if a == "-f" and base == "php":
                return False                          # a script file
            if a.startswith("-") and len(a) > 1:
                if a.startswith(attached) and not (base == "php" and a.startswith("-r")):
                    i += 2 if a in separate else 1
                    continue
                if letters and any(ch in a[1:] for ch in letters):
                    return True
                i += 1
                continue
            return False                              # a script file
        return base != "osascript"                    # no script given: reads stdin
    if base in ("awk", "gawk", "mawk", "nawk"):
        program = None
        while i < len(rest):
            a = rest[i]
            if a in ("-f", "--file") or a.startswith("--file="):
                target = rest[i + 1] if a in ("-f", "--file") and i + 1 < len(rest) else a.partition("=")[2]
                return target in ("-", "") or target.startswith("/dev/")   # a file, unless stdin
            if a in ("-i", "--include") or a.startswith(("-i", "--include=")) and a != "-":
                return True                           # gawk -i inplace / includes
            if a in ("-F", "-v", "-e", "--source") and i + 1 < len(rest):
                if a in ("-e", "--source"):
                    program = rest[i + 1]
                    break
                i += 2
                continue
            if a.startswith("-"):
                i += 1
                continue
            program = a
            break
        return bool(program and (_AWK_SIDE_EFFECTS.search(program) or "@include" in program
                                 or "@load" in program))
    return False


def _shell_inline(rest: list[str]) -> tuple[str, str | None]:
    """For a shell: ("c", inner) for `-c STRING`, ("stdin", None) when it reads
    its script from stdin, ("script", None) for a script file or --version."""
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in _HELP_FLAGS:
            return "script", None
        if a == "--":
            i += 1
            break
        if a in ("-o", "+o"):
            i += 2
            continue
        if a.startswith("-") and len(a) > 1 and not a.startswith("--"):
            if "c" in a[1:]:
                inner = next((x for x in rest[i + 1:] if not x.startswith("-")), None)
                return ("c", inner) if inner is not None else ("stdin", None)
            if a == "-s" or a == "-":
                return "stdin", None
            i += 1
            continue
        if a.startswith("--"):
            i += 1
            continue
        return "script", None
    return ("stdin", None) if i >= len(rest) else ("script", None)


def _interpreter_findings(
    text: str, cwd: Path | None, depth: int = 0
) -> tuple[str, Danger, str, bool]:
    """(blocked reason, danger, reason, all_inner_safe) for interpreter use.

    `all_inner_safe` is only for argv_safe(): every interpreter segment is a
    `sh -c STRING` whose inner string is itself in the safe set."""
    segments = _segments(text)
    if segments is None:
        return _unparsed_interpreter(text)
    danger, reason = Danger.SAFE, ""
    for argv, redirects in segments:
        rest, inline = _strip_wrappers(list(argv))
        if not rest:
            continue
        prog = os.path.basename(rest[0])
        args = rest[1:]
        if prog == "eval":
            inline = True
        elif prog in _SHELLS:
            kind, inner = _shell_inline(args)
            if kind == "c" and inner is not None:
                if depth >= _MAX_DEPTH:
                    inline = True
                else:
                    sub = classify_command(inner, cwd, _depth=depth + 1)
                    if sub.blocked:
                        return sub.block_reason, Danger.CRITICAL, "", False
                    if sub.danger > danger:
                        danger, reason = sub.danger, sub.summary()
            elif kind == "stdin":
                inline = True
        elif _INTERP_RE.match(prog):
            inline = inline or _scan_interpreter(prog, args)
        if inline:
            danger, reason = max(danger, Danger.DESTRUCTIVE), _INLINE_REASON
    return "", danger, reason, True


_UNPARSED_INTERP = re.compile(
    r"(?:^|[;&|(\n`{]|\$\()\s*"
    r"(?:(?:env|nohup|nice|time|command|exec|stdbuf|timeout|xargs|sudo|doas|busybox)\s+"
    r"(?:-\S+\s+|\w+=\S*\s+|\d+\s+)*)*"
    r"(?:\S*/)?(?P<p>python[\d.]*|pypy[\d.]*|node(?:js)?|deno|bun|ruby|irb|perl|php[\d.]*|"
    r"lua[\d.]*|luajit|awk|gawk|mawk|nawk|tclsh|osascript|bash|sh|zsh|dash|ksh|ash|eval)\b"
    r"(?P<rest>[^\n;&|]*)"
)
_UNPARSED_INLINE = re.compile(
    r"(?:^|\s)-[A-Za-z]*[cerEpRB][A-Za-z]*(?:\s|=|$)|--eval|--print|<<|<\s*\S|(?:^|\s)-(?:\s|$)"
)


def _unparsed_interpreter(text: str) -> tuple[str, Danger, str, bool]:
    """Fallback when the argv cannot be read ($VAR, heredoc, newline): an
    interpreter in command position with an inline-code marker, or none of
    the usual script arguments, is treated as inline code."""
    for m in _UNPARSED_INTERP.finditer(text):
        prog, rest = m.group("p"), m.group("rest")
        if prog in ("awk", "gawk", "mawk", "nawk"):
            if _AWK_SIDE_EFFECTS.search(rest):
                return "", Danger.DESTRUCTIVE, _INLINE_REASON, False
            continue
        if prog == "eval" or not rest.strip() or _UNPARSED_INLINE.search(rest):
            if any(h in rest for h in ("--version", "--help")) or rest.strip() in ("-V", "-h"):
                continue
            return "", Danger.DESTRUCTIVE, _INLINE_REASON, False
    return "", Danger.SAFE, "", False

# -- "Always" signatures -------------------------------------------------------
#
# A signature names a *family* of commands the owner may pre-approve. It is an
# allow-list, not a deny-list: only the three families the owner chose
# (2026-11-02) can produce one, and only for commands that really are a single
# simple command. Everything else - the rm family, sudo, sed -i, recursive
# chmod/chown, dd, git reset --hard, compound commands, redirects - yields "".

_PKILL_FLAGS = frozenset({"-1", "-2", "-9", "-15", "-HUP", "-INT", "-KILL", "-TERM", "-x"})
# Names a pkill must never be pre-approved for: the session, the display
# stack and Kravein itself.
_PKILL_PROTECTED = frozenset({
    "init", "systemd", "dbus-daemon", "dbus-broker", "sshd", "login", "pipewire",
    "wireplumber", "hyprland", "niri", "sway", "river", "xorg", "x", "xwayland",
    "gdm", "sddm", "lightdm", "bash", "zsh", "fish", "sh", "dash", "tmux", "screen",
    "ydotoold", "ydotool",
})
# No `.`/`+`/`*`: pkill takes a regex, and matches it anywhere in a name.
_PKILL_SELF = frozenset({"kravein", "python", "python3", "uvicorn", "systemd-logind"})
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,59}$")
_MODE_RE = re.compile(
    r"^(0?[0-7]{3}|[ugoa]*[-+=][rwxX]+(,[ugoa]*[-+=][rwxX]+)*)$"
)


def _owner_file(path: Path | None) -> bool:
    """An existing, non-symlink file or directory the owner owns under $HOME,
    outside Kravein's own dirs and every login/startup location."""
    if path is None:
        return False
    home = Path.home().resolve()
    try:
        st = path.lstat()
        resolved = path.resolve()
    except OSError:
        return False
    if stat_mod.S_ISLNK(st.st_mode) or st.st_uid != os.getuid():
        return False
    if not resolved.is_relative_to(home) or resolved == home:
        return False
    return _writable_ok(resolved)


def command_signature(command: str, cwd: Path | None = None) -> str:
    """The "Always allow" signature of a command, or "" if it is not eligible.

    Call it only for a command that asks at DESTRUCTIVE. Eligible families:
    ``pkill|killall [-sig] NAME`` (the name stays in the signature),
    ``mv * DEST`` over an existing target inside $HOME, and non-recursive
    ``chmod MODE *`` on the owner's own files.
    """
    segments = _segments(command)
    if segments is None or len(segments) != 1:
        return ""
    argv, redirects = segments[0]
    if redirects:
        return ""
    prog, args = argv[0], argv[1:]

    if prog in ("pkill", "killall"):
        flags = [a for a in args if a.startswith("-")]
        names = [a for a in args if not a.startswith("-")]
        if len(names) != 1 or any(f not in _PKILL_FLAGS for f in flags):
            return ""
        name = names[0]
        if not _NAME_RE.match(name):
            return ""
        low = name.lower()
        # pkill matches a *substring*: `pkill nir` hits niri, `pkill pyth` hits
        # Kravein itself. Refuse any pattern that could match a protected name.
        if any(low in p for p in _PKILL_PROTECTED | _PKILL_SELF) or any(
            low.startswith(p) for p in _PKILL_SELF
        ):  # ... and `kravein-web`, `python3.14`: longer names that are us
            return ""
        return " ".join([prog, *sorted(flags), name])

    if prog == "mv":
        if any(a.startswith("-") and a not in ("-f", "-v") for a in args):
            return ""
        pos = [a for a in args if not a.startswith("-")]
        if len(pos) != 2:
            return ""
        src, dest = (_literal_path(p, cwd) for p in pos)
        if src is None or dest is None:
            return ""
        if not (_owner_file(src) and (_owner_file(dest) or _owner_file(dest.parent))):
            return ""
        return f"mv * {dest}"

    if prog == "chmod":
        pos = [a for a in args if not a.startswith("-")]
        # Any flag at all: -R/-r/--recursive, --reference, -c ... not eligible.
        if len(pos) < 2 or len(pos) != len(args) or not _MODE_RE.match(pos[0]):
            return ""
        if not all(_owner_file(_literal_path(p, cwd)) for p in pos[1:]):
            return ""
        return f"chmod {pos[0]} *"

    return ""


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


def _attended(confirmer: Any) -> bool:
    """False when the confirmer says nobody is there to be asked.

    A background task's confirmer routes to the owner's live sessions and
    carries ``attended()``: with none attached, auto-approve must not answer in
    their place ("live owner sources only", 2026-10-23). Confirmers without the
    attribute (every foreground one) are attended by definition.
    """
    check = getattr(confirmer, "attended", None)
    if check is None:
        return True
    try:
        return bool(check())
    except Exception:
        return False


def get_confirmer() -> Confirmer:
    return getattr(_local, "confirmer", None) or _deny


def set_confirmer(fn: Confirmer | None) -> None:
    """Install the confirmation callback for the current thread."""
    _local.confirmer = fn


def enforce_calls() -> int:
    """How many times :func:`enforce` has run on this thread.

    ``registry.dispatch`` reads it before and after a skill to notice a
    destructive skill that finished without ever consulting safety. It is
    only a tripwire: the registry cannot enforce for the skill, because only
    the skill knows what its arguments mean.
    """
    return getattr(_local, "enforce_calls", 0)


class TurnMemory:
    """What the owner has refused during one request.

    ``targets`` is the per-target denial memory; a decline never locks out
    *other* targets ("delete A, keep B" must work). ``retried_declined`` is set
    when a call is refused because its target was already declined: the model
    is working around a "no", so the agent loop ends the turn rather than
    spend more rounds on it. Live voice runs each tool call in its own scope,
    so it holds one of these across the model turn and passes it to
    ``confirmer_scope``.
    """

    def __init__(self) -> None:
        self.targets: set[str] = set()
        self.retried_declined = False
        # Taint (#237): where untrusted content entered this run ("tool:web_fetch",
        # "mcp:leads", "task:t3"). A sub-agent sharing this memory taints its
        # parent by construction. Only ever grows within a run.
        self.taint: set[str] = set()


def mark_tainted(source: str) -> None:
    """Untrusted content (a web page, an MCP result, a tainted report) has
    entered this run's context. Outward grants stop applying (see enforce)."""
    source = str(source or "untrusted").strip() or "untrusted"
    memory = _memory()
    if source not in memory.taint:
        memory.taint.add(source)
        audit.record("safety.tainted", source=source)


def taint_sources() -> frozenset[str]:
    return frozenset(_memory().taint)


# -- the call being dispatched (#237) -----------------------------------------
#
# A grant's scope is computed by CODE from the arguments of the call that is
# actually running, never from anything a caller passes to enforce(). The
# registry pushes this context around every skill call; with none (a direct
# call outside dispatch) no grant is offered or used: the card always shows.

@dataclass(frozen=True)
class CallContext:
    tool: str
    scope: tuple[tuple[str, str], ...]   # (arg name, canonical JSON value), sorted
    outward: bool = False
    origin: str = ""                     # "mcp:<server>" / "plugin:<id>"; "" = core


def _call_stack() -> list[CallContext]:
    stack = getattr(_local, "calls", None)
    if stack is None:
        stack = _local.calls = []
    return stack


class call_context:
    """Context manager: ``registry.dispatch`` only."""

    def __init__(self, ctx: CallContext) -> None:
        self._ctx = ctx

    def __enter__(self) -> CallContext:
        _call_stack().append(self._ctx)
        return self._ctx

    def __exit__(self, *exc_info) -> None:
        stack = _call_stack()
        if stack and stack[-1] is self._ctx:
            stack.pop()


def current_call() -> CallContext | None:
    stack = _call_stack()
    return stack[-1] if stack else None


def _memory() -> TurnMemory:
    memory = getattr(_local, "memory", None)
    if memory is None:
        memory = TurnMemory()
        _local.memory = memory
    return memory


def current_memory() -> TurnMemory:
    """This thread's denial memory, for a nested agent to share (``delegate``).

    A sub-agent runs inside the caller's request, so a "no" the owner gave in
    either must hold in both; a fresh memory per agent would let a declined
    target be asked about again under another name.
    """
    return _memory()


def _denied_targets() -> set[str]:
    return _memory().targets


def retried_declined_target() -> bool:
    """True once a call was refused for retrying a target declined this turn."""
    return _memory().retried_declined


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
    # A path named inside inline code (`os.remove("/home/u/f")`) is not a
    # separate word, so it was never a key: a declined `rm` could be retried
    # through `python3 -c` (#206).
    for raw in re.findall(r"(?<![\w/.~-])((?:~|/)[\w.+@%~/-]*[\w+@%~-])", action_desc):
        keys.add(os.path.normpath(os.path.expanduser(raw)).rstrip("/").lower())
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

    def __init__(
        self,
        fn: Confirmer | None,
        memory: TurnMemory | None = None,
        principal: str | None = None,
        grant_principal: str | None = None,
    ):
        self._fn = fn
        self._memory = memory if memory is not None else TurnMemory()
        # Whose "Always approve" rules apply in this scope (#199). None keeps the
        # enclosing scope's (a delegated sub-agent acts for the same owner);
        # "" means nobody's: no rule applies and none is offered.
        self._principal = principal
        # Whose GUARDED grants apply (#237). Separate from `principal` on
        # purpose: owner ruling D4 lets grants act in delegated and scheduled
        # runs, while the DESTRUCTIVE rules of #199 must stay out of them.
        # None keeps the enclosing scope's.
        self._grant_principal = grant_principal
        self._previous: Confirmer | None = None
        self._previous_memory: TurnMemory | None = None
        self._previous_principal: str = ""
        self._previous_grant: str = ""

    def __enter__(self) -> "confirmer_scope":
        self._previous = getattr(_local, "confirmer", None)
        self._previous_memory = getattr(_local, "memory", None)
        self._previous_principal = getattr(_local, "principal", "")
        self._previous_grant = getattr(_local, "grant_principal", "")
        _local.confirmer = self._fn
        _local.memory = self._memory
        if self._principal is not None:
            _local.principal = self._principal
        if self._grant_principal is not None:
            _local.grant_principal = self._grant_principal
        return self

    def __exit__(self, *exc_info) -> None:
        _local.confirmer = self._previous
        _local.memory = self._previous_memory
        _local.principal = self._previous_principal
        _local.grant_principal = self._previous_grant


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


# ---------------------------------------------------------------------------
# Confirmation policy: ask / decline / auto
# ---------------------------------------------------------------------------
#
# The owner's guardrails for "auto" (decided 2026-10-23) are all enforced
# here, in code, at decision time:
#   1. only requests the owner is making live (AUTO_SOURCES) — never a job,
#      WhatsApp, or a source this list does not name;
#   2. DESTRUCTIVE only — CRITICAL always asks;
#   3. hard refusals and the per-turn denial memory run first, unchanged;
#   4. it expires, and it exists only in this process's memory, so a restart
#      is always back to "ask";
#   5. every auto-approval is audited.
#
# Nothing in kravein/skills may call the setters below: the model must never
# be able to approve its own actions. tests/engine/test_confirmation_policy.py
# pins that.

CONFIRMATION_MODES = ("ask", "decline", "auto")
AUTO_DEFAULT_SECONDS = 3600
AUTO_MAX_SECONDS = 4 * 3600

# Sources where a human is driving the request right now. Anything else —
# "daemon-run", "whatsapp", "cli-run", "local", a job — keeps asking (and so,
# with no confirmer, keeps refusing).
AUTO_SOURCES = frozenset({"web", "web-panel", "voice", "voice-live", "cli", "cli-ask"})

_policy_lock = threading.Lock()
# time.monotonic() deadline while auto-approve is on, else None. Monotonic so
# a wall-clock jump (NTP, suspend/resume) cannot stretch it.
_auto_until: float | None = None


def _persisted_mode() -> str:
    """The mode stored in settings: only ever "ask" or "decline"."""
    if not config.get("safety.allow_destructive", True):
        return "decline"
    raw = str(config.get("safety.confirmation_mode", "ask") or "ask").strip().lower()
    return "decline" if raw == "decline" else "ask"


def _auto_seconds_left() -> float:
    """Seconds of auto-approve left; clears and audits a stale deadline."""
    global _auto_until
    with _policy_lock:
        if _auto_until is None:
            return 0.0
        left = _auto_until - time.monotonic()
        if left > 0:
            return left
        _auto_until = None
    audit.record("safety.auto_expired")
    return 0.0


def confirmation_policy() -> dict:
    """Current confirmation mode, for the web settings view.

    Returns ``{"mode": "ask"|"decline"|"auto", "seconds_left": int,
    "default_seconds": int, "max_seconds": int}``. ``seconds_left`` is 0
    unless the mode is "auto". "decline" in settings wins over a running
    auto-approve.
    """
    persisted = _persisted_mode()
    left = 0.0 if persisted == "decline" else _auto_seconds_left()
    return {
        "mode": "auto" if left > 0 else persisted,
        "seconds_left": int(left + 0.999) if left > 0 else 0,
        "default_seconds": AUTO_DEFAULT_SECONDS,
        "max_seconds": AUTO_MAX_SECONDS,
    }


def _persist_mode(mode: str) -> None:
    def apply(settings: dict) -> None:
        section = settings.setdefault("safety", {})
        section["confirmation_mode"] = mode
        # Keep the legacy switch coherent, or picking "ask" in the UI would stay
        # "decline" forever for a file that still says allow_destructive: false.
        section["allow_destructive"] = mode != "decline"

    # Atomic with /api/settings writes: no lost update between the two.
    config.update_settings(apply)


def set_confirmation_mode(mode: str, seconds: int | None = None, *, by: str = "") -> dict:
    """Set the confirmation mode. Owner-facing callers only — never a skill.

    ``mode`` is "ask", "decline" or "auto". For "auto", ``seconds`` is how
    long it stays on (default :data:`AUTO_DEFAULT_SECONDS`, clamped to
    :data:`AUTO_MAX_SECONDS`); afterwards the mode is "ask". "auto" is kept
    in memory only and stores "ask" in settings. ``by`` names the caller for
    the audit log (e.g. "web"). Returns :func:`confirmation_policy`.
    Raises ``ValueError`` for an unknown mode or a non-positive duration.
    """
    global _auto_until
    mode = str(mode or "").strip().lower()
    if mode not in CONFIRMATION_MODES:
        raise ValueError(f"unknown confirmation mode {mode!r}; use one of {CONFIRMATION_MODES}")

    duration = 0
    if mode == "auto":
        duration = AUTO_DEFAULT_SECONDS if seconds is None else int(seconds)
        if duration <= 0:
            raise ValueError("auto-approve needs a positive duration in seconds")
        duration = min(duration, AUTO_MAX_SECONDS)

    _persist_mode("decline" if mode == "decline" else "ask")
    with _policy_lock:
        _auto_until = time.monotonic() + duration if mode == "auto" else None
    audit.record("safety.mode_changed", mode=mode, seconds=duration, by=by)
    return confirmation_policy()


def disable_auto_approve(*, by: str = "") -> dict:
    """Turn auto-approve off now (mode becomes "ask"). Safe to call when off."""
    global _auto_until
    with _policy_lock:
        was_on = _auto_until is not None
        _auto_until = None
    if was_on:
        audit.record("safety.mode_changed", mode="ask", seconds=0, by=by)
    return confirmation_policy()


# ---------------------------------------------------------------------------
# "Always approve" rules (#199)
# ---------------------------------------------------------------------------
#
# A rule pre-approves one command signature (see command_signature) for one
# principal. Guardrails, all in code:
#   - written only by enforce(), when the owner's card answer is "always", and
#     only for a signature the classifier itself produced for that command, so
#     neither the model nor a forged client frame can create one;
#   - lives in STATE_DIR (check_path refuses the file tools there and
#     classify_command refuses shell writes naming the file);
#   - applies only where auto-approve does (live owner sources, attended),
#     never to CRITICAL/blocked, after the hard refusals, the "decline" policy
#     and the per-turn denial memory - a declined target still wins;
#   - every use is audited; the owner can list and revoke them.
# A rule only ever matches a signature recomputed from the actual command, so
# even a hand-edited store cannot approve anything outside the three families.

RULES_FILENAME = "safety_rules.json"
_rules_lock = threading.Lock()


@contextmanager
def _rules_locked():
    """Thread lock + a cross-process ``flock`` around every read-modify-write.

    The server and a jobs tick are separate processes that both rewrite the
    store (#237, D4 made jobs use grants). Without the file lock a job saving
    its use count could write back a grant the owner revoked a moment ago.
    The lock file is never replaced (the store is: tmp + rename swaps the
    inode, so locking the store itself guards nothing) - same as jobs.lock.
    """
    import fcntl

    with _rules_lock:
        path = _rules_path().with_name(RULES_FILENAME + ".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _rules_path() -> Path:
    return config.STATE_DIR / RULES_FILENAME


def _load_rules() -> list[dict]:
    try:
        data = json.loads(_rules_path().read_text())
    except (OSError, ValueError):
        return []
    rules = data.get("rules") if isinstance(data, dict) else None
    return [
        r for r in (rules if isinstance(rules, list) else [])
        if isinstance(r, dict)
        and isinstance(r.get("id"), str)
        and isinstance(r.get("signature"), str)
        and isinstance(r.get("principal"), str)
    ]


def _save_rules(rules: list[dict]) -> None:
    path = _rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"rules": rules}, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def list_rules(principal: str) -> list[dict]:
    """The principal's own rules, for the settings view."""
    with _rules_locked():
        return [dict(r) for r in _load_rules() if r["principal"] == principal]


def add_always_rule(signature: str, principal: str, *, by: str = "") -> dict | None:
    """Store a rule. Owner-facing callers only (enforce, after a card answer)."""
    if not signature or not principal:
        return None
    with _rules_locked():
        rules = _load_rules()
        for r in rules:
            if r["signature"] == signature and r["principal"] == principal:
                return dict(r)
        rule = {
            "id": uuid.uuid4().hex[:12], "signature": signature,
            "principal": principal, "created_at": time.time(),
            "uses": 0, "last_used": None,
        }
        rules.append(rule)
        _save_rules(rules)
    audit.record("safety.rule_created", signature=signature, principal=principal, by=by)
    return dict(rule)


def revoke_rule(rule_id: str, principal: str, *, by: str = "") -> bool:
    """Delete one of the principal's rules. False if it is not theirs/unknown."""
    with _rules_locked():
        rules = _load_rules()
        kept = [r for r in rules if not (r["id"] == rule_id and r["principal"] == principal)]
        if len(kept) == len(rules):
            return False
        _save_rules(kept)
    audit.record("safety.rule_revoked", rule_id=rule_id, principal=principal, by=by)
    return True


def _match_rule(signature: str, principal: str) -> dict | None:
    """The principal's rule for this signature, with its use count bumped."""
    if not signature or not principal:
        return None
    with _rules_locked():
        rules = _load_rules()
        for r in rules:
            if r.get("kind") == "grant":
                continue                # a GUARDED grant never answers a DESTRUCTIVE card
            if r["signature"] == signature and r["principal"] == principal:
                r["uses"] = int(r.get("uses") or 0) + 1
                r["last_used"] = time.time()
                try:
                    _save_rules(rules)
                except OSError:
                    pass
                return dict(r)
    return None


ALWAYS = "always"        # a Confirmer may return this instead of True


# ---------------------------------------------------------------------------
# GUARDED grants (#237)
# ---------------------------------------------------------------------------
#
# A grant is the owner's "Always allow" on a GUARDED card. It lives in the
# rules file (`kind: "grant"`) so the existing list/revoke path and
# /api/safety/rules see it. Rules that are not up for negotiation:
#   - created ONLY by enforce(), after a real confirmer answered "always" to
#     the very card enforce() offered it for; the signature is computed here
#     from the running call, never taken from the answer, a skill or the model;
#   - GUARDED only: DESTRUCTIVE/CRITICAL never get or use one (owner, 2026-11-13);
#   - a secret read or an owner-only card is never answered by one;
#   - applies attended and unattended alike (owner ruling D4), but is
#     suspended for an outward tool in a tainted run (§13.4);
#   - per-day cap and expiry, every use audited, revocable.

GRANT_MAX_CAP = 1000
GRANT_MAX_SECONDS = 365 * 86400


@dataclass(frozen=True)
class AlwaysAllow:
    """A confirmer's "Always allow" with the owner's own limits.

    ``cap`` = uses per day, ``expires_in`` = seconds. Both optional and
    bounded. It carries no scope and no signature: those come from code.
    """

    cap: int | None = None
    expires_in: float | None = None


def _answer_limits(answer: Any) -> tuple[int | None, float | None] | None:
    """``(cap, expires_in)`` for an Always answer, ``None`` if it is not one."""
    if answer == ALWAYS and isinstance(answer, str):
        return None, None
    if isinstance(answer, AlwaysAllow):
        cap = answer.cap
        if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int)
                                or not 1 <= cap <= GRANT_MAX_CAP):
            return None               # malformed limits: not an approval at all
        exp = answer.expires_in
        if exp is not None and (isinstance(exp, bool) or not isinstance(exp, (int, float))
                                or not 0 < exp <= GRANT_MAX_SECONDS):
            return None
        return cap, (float(exp) if exp is not None else None)
    return None


def grant_signature(ctx: CallContext) -> str:
    blob = json.dumps({"tool": ctx.tool, "scope": list(ctx.scope), "origin": ctx.origin},
                      sort_keys=True, separators=(",", ":"))
    return f"grant:{ctx.tool}:{hashlib.sha256(blob.encode()).hexdigest()[:24]}"


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _add_grant(ctx: CallContext, principal: str, cap: int | None,
               expires_in: float | None, *, by: str = "") -> dict | None:
    """Private: enforce() is the only caller."""
    if not principal:
        return None
    signature = grant_signature(ctx)
    now = time.time()
    with _rules_locked():
        rules = _load_rules()
        rules = [r for r in rules if not (r.get("kind") == "grant" and r["signature"] == signature
                                          and r["principal"] == principal)]
        grant = {
            "id": uuid.uuid4().hex[:12], "kind": "grant", "signature": signature,
            "principal": principal, "tool": ctx.tool, "origin": ctx.origin,
            "outward": ctx.outward, "scope": {k: json.loads(v) for k, v in ctx.scope},
            "cap": cap, "expires_at": (now + expires_in) if expires_in else None,
            "created_at": now, "uses": 0, "last_used": None, "day": _today(), "day_uses": 0,
        }
        rules.append(grant)
        _save_rules(rules)
    audit.record("safety.grant_created", grant_id=grant["id"], tool=ctx.tool,
                 principal=principal, cap=cap, expires_at=grant["expires_at"], by=by)
    return dict(grant)


def _use_grant(ctx: CallContext, principal: str) -> tuple[dict | None, str]:
    """``(grant, "")`` with its counters bumped, or ``(None, why)``.

    Matches only on the signature recomputed from the running call: a
    hand-edited store with a wider "scope" field still cannot match anything
    else.
    """
    if not principal:
        return None, "no principal"
    signature = grant_signature(ctx)
    with _rules_locked():
        rules = _load_rules()
        for r in rules:
            if not (r.get("kind") == "grant" and r["signature"] == signature
                    and r["principal"] == principal and r.get("tool") == ctx.tool):
                continue
            # Fail closed on anything malformed: a limit that cannot be read
            # is not "no limit" (a hand edit could turn cap into "5").
            expires = r.get("expires_at")
            if expires is not None and (isinstance(expires, bool)
                                        or not isinstance(expires, (int, float))):
                return None, "malformed grant"
            if expires is not None and time.time() >= expires:
                return None, "expired"
            cap = r.get("cap")
            if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int) or cap < 1):
                return None, "malformed grant"
            try:
                uses = int(r.get("uses") or 0)
                day_uses = int(r.get("day_uses") or 0) if r.get("day") == _today() else 0
            except (TypeError, ValueError):
                return None, "malformed grant"
            if cap is not None and day_uses >= cap:
                return None, "cap reached"
            r["day"] = _today()
            r["uses"] = uses + 1
            r["day_uses"] = day_uses + 1
            r["last_used"] = time.time()
            try:
                _save_rules(rules)
            except OSError:
                # Uncounted use would let a cap be exceeded: fail closed.
                return None, "could not record the use"
            return dict(r), ""
    return None, "no grant"


def _grant_principal() -> str:
    return getattr(_local, "grant_principal", "") or _current_principal()


def always_offer() -> dict:
    """What the card being raised right now may offer, for a Confirmer to read:
    ``{"allowed": bool, "signature": str}`` (unchanged for #199 rules). A grant
    offer adds ``kind: "grant"``, ``tool``, ``scope`` (shown to the owner) and
    ``outward`` (the UI offers "up to N per day" by default). Thread-local,
    set by enforce().
    """
    sig = getattr(_local, "always_signature", "")
    offer: dict[str, Any] = {"allowed": bool(sig), "signature": sig}
    ctx = getattr(_local, "always_grant", None)
    if sig and ctx is not None:
        offer.update(kind="grant", tool=ctx.tool, outward=ctx.outward,
                     scope={k: json.loads(v) for k, v in ctx.scope})
    return offer


def _current_principal() -> str:
    return getattr(_local, "principal", "")


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
    _local.enforce_calls = enforce_calls() + 1
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

    policy = confirmation_policy()
    if policy["mode"] == "decline":
        audit.record(
            "safety.denied_by_policy",
            skill=skill, source=source, action=action_desc, policy="decline",
        )
        raise SafetyError(
            f"Refused: {action_desc}. The confirmation policy is \"decline\": "
            "guarded, destructive and critical actions are refused without asking. Tell "
            "the user; only they can change the policy in settings — do not "
            "retry it another way."
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
        # Tell the agent loop the model is retrying something the owner
        # refused, so it can end the turn instead of spending more rounds.
        _memory().retried_declined = True
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

    # GUARDED + a matching grant (#237). After the hard refusals, the decline
    # policy and the denial memory; before the confirmer. Attended or not (D4).
    ctx = current_call()
    grant_principal = _grant_principal()
    grantable = bool(
        verdict.danger == Danger.GUARDED
        and not verdict.no_auto
        and ctx is not None
        and grant_principal
    )
    if grantable:
        taint = taint_sources()
        # The taint rule (§13.4): injected text can ask for anything, so an
        # outward grant does not apply once untrusted content is in the run -
        # unless all of it came from the very server/plugin the grant is for.
        suspended = bool(ctx.outward and taint and not (ctx.origin and taint <= {ctx.origin}))
        if suspended:
            audit.record("safety.grant_suspended", skill=skill, source=source, tool=ctx.tool,
                         action=action_desc, taint=sorted(taint))
        else:
            grant, why = _use_grant(ctx, grant_principal)
            if grant is not None:
                audit.record(
                    "safety.grant_used",
                    skill=skill, source=source, action=action_desc, tool=ctx.tool,
                    grant_id=grant["id"], principal=grant_principal, uses=grant["uses"],
                    day_uses=grant["day_uses"], cap=grant.get("cap"),
                    attended=confirmer is not _deny and _attended(confirmer),
                )
                return
            if why in ("expired", "cap reached", "could not record the use", "malformed grant"):
                audit.record("safety.grant_unusable", tool=ctx.tool, reason=why,
                             principal=grant_principal)

    # "Always approve" (#199): same preconditions as auto-approve, minus the
    # timer. Only an eligible, DESTRUCTIVE (never CRITICAL) signature.
    principal = _current_principal()
    rule_ok = bool(
        verdict.signature
        and not verdict.no_auto
        and verdict.danger == Danger.DESTRUCTIVE
        and principal
        and source in AUTO_SOURCES
        and confirmer is not _deny
        and _attended(confirmer)
    )
    if rule_ok:
        rule = _match_rule(verdict.signature, principal)
        if rule is not None:
            audit.record(
                "safety.rule_approved",
                skill=skill, source=source, action=action_desc,
                signature=verdict.signature, rule_id=rule["id"],
                principal=principal, uses=rule["uses"],
            )
            return

    # Auto-approve: after the hard refusals and the denial memory, never for
    # CRITICAL, only for a live owner source, and only where a human could
    # have been asked — a session with no confirmer is unattended whatever
    # its source says, and unattended must keep refusing.
    if (
        policy["mode"] == "auto"
        and not verdict.no_auto
        and verdict.danger == Danger.DESTRUCTIVE
        and source in AUTO_SOURCES
        and confirmer is not _deny
        and _attended(confirmer)
        and _auto_seconds_left() > 0
    ):
        audit.record(
            "safety.auto_approved",
            skill=skill, source=source, action=action_desc,
            danger=DANGER_LABELS[verdict.danger], detail=detail,
        )
        return

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
    always = False
    limits: tuple[int | None, float | None] | None = None
    # One offer per card: a #199 rule (DESTRUCTIVE) or a grant (GUARDED).
    offered_grant = ctx if grantable else None
    offered = verdict.signature if rule_ok else (
        grant_signature(offered_grant) if offered_grant is not None else "")
    _local.always_signature = offered
    _local.always_grant = offered_grant
    try:
        answer = confirmer(action_desc, detail, verdict.danger)
        # A str answer counts only if it is exactly "always"; any other string
        # is not an approval. An AlwaysAllow with bad limits is not one either.
        limits = _answer_limits(answer)
        always = limits is not None
        approved = always or (
            not isinstance(answer, (str, AlwaysAllow)) and bool(answer)
        )
    except Exception as exc:
        audit.record("safety.confirm_error", action=action_desc, error=str(exc))
        approved = always = False
    finally:
        _local.always_signature = ""
        _local.always_grant = None

    audit.record(
        "safety.confirm_result",
        skill=skill, source=source, action=action_desc,
        danger=DANGER_LABELS[verdict.danger], approved=approved,
        always=bool(always and offered),
    )
    if always and offered and offered_grant is not None:
        try:
            _add_grant(offered_grant, grant_principal, limits[0], limits[1], by=source)
        except OSError as exc:
            audit.record("safety.rule_store_error", error=str(exc))
    elif always and offered:
        try:
            add_always_rule(offered, principal, by=source)
        except OSError as exc:
            # The owner approved this command; failing to remember it must not
            # turn that into a refusal. They will simply be asked again.
            audit.record("safety.rule_store_error", error=str(exc))

    if not approved:
        # Remember the refusal so a differently-spelled retry at the same
        # target is refused outright instead of asking again.
        _denied_targets().update(keys)
        raise ConfirmationRequired(f"Cancelled: {action_desc}.", detail)
