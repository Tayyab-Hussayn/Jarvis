"""Running arbitrary commands.

This is the sharpest tool in the box, so it carries the most safety
machinery: every command is classified by :mod:`brahma.safety` before it
runs, outright-dangerous patterns are refused, and anything destructive
needs a human yes.

It replaces the original ``agent/executor.py`` codegen-and-exec path, which
asked an LLM to write Python and ran it with no review at all.
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import config
from ..platform import shell as shell_platform
from ..registry import skill
from ..safety import Danger, classify_command, enforce

MAX_OUTPUT_CHARS = 8000


def _trim(text: str, label: str) -> str:
    text = text.rstrip()
    if not text:
        return ""
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + f"\n… [{label} truncated]"
    return text


@skill(category="shell", danger=Danger.DESTRUCTIVE)
def run_command(
    command: str,
    working_directory: str = "",
    timeout_seconds: int = 60,
    _source: str = "local",
    _confirm: bool = False,
) -> str:
    """Run a shell command and return its output.

    The command is risk-classified first. Read-only commands run straight
    away; anything that deletes, kills, installs or needs root asks you to
    confirm. Outright dangerous patterns are refused.

    Args:
        command: The command line to run.
        working_directory: Directory to run it in. Defaults to your home.
        timeout_seconds: Kill the command after this long.
    """
    command = (command or "").strip()
    if not command:
        return "No command given."

    verdict = classify_command(command)
    enforce(
        verdict, f"run `{command}`",
        skill="run_command", source=_source, pre_confirmed=_confirm,
    )

    cwd = config.resolve_user_path(working_directory) if working_directory else Path.home()
    if not cwd.is_dir():
        return f"Working directory does not exist: {cwd}"

    timeout = max(1, min(int(timeout_seconds), 600))
    result = shell_platform.run(
        command,
        shell=True,
        cwd=cwd,
        timeout=timeout,
        audit_event="shell.user_command",
    )

    parts = [f"$ {command}"]
    if result.timed_out:
        parts.append(f"[timed out after {timeout}s]")

    stdout = _trim(result.stdout, "stdout")
    stderr = _trim(result.stderr, "stderr")

    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    if not stdout and not stderr:
        parts.append("(no output)")

    parts.append(f"[exit {result.returncode}]")
    return "\n".join(parts)


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
