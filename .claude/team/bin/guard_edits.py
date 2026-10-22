#!/usr/bin/env python3
"""PreToolUse hook for Edit / Write / NotebookEdit.

Blocks a module agent from editing any file it does not own (see .claude/team/roster.json).
The orchestrator may edit anything except the owner-only runtime state directory and roster["frozen"] paths
(frozen paths are blocked for EVERYONE, orchestrator included; unfreeze by editing roster.json by hand).
An owner "unguard" (see `team unguard`) lifts the ownership check for that agent until it expires.
Exit code 2 blocks the call and shows stderr to Claude.
"""
import json
import sys

from team_lib import (PROTECTED_STATE_MARKER, frozen_match, is_granted, log_block, owner_of, resolve_identity, to_repo_relative,
                      unguard_until)

data = json.load(sys.stdin)
cwd = data.get("cwd") or "."
tool_input = data.get("tool_input") or {}
file_path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
if not file_path:
    sys.exit(0)

me = resolve_identity(cwd)

# Owner-only switches: no Claude session (orchestrator included) may create or edit them.
if PROTECTED_STATE_MARKER in file_path.replace("\\", "/"):
    print("BLOCKED: .claude/team/state/ is owner-only runtime state. Ask the owner to run the `team` command in a terminal.",
          file=sys.stderr)
    log_block(me, "edit", file_path, "owner-only state")
    sys.exit(2)

rel = to_repo_relative(file_path, cwd)

# Frozen paths: nobody, not the orchestrator, not an unguarded agent, not a grant holder.
if rel is not None:
    hit = frozen_match(rel)
    if hit:
        print(f"BLOCKED: '{rel}' is frozen (matches '{hit}'): edit roster.json to unfreeze. "
              "Nobody may modify frozen paths, including the orchestrator.", file=sys.stderr)
        log_block(me, "edit", rel, f"frozen: {hit}")
        sys.exit(2)

if me == "orchestrator" or unguard_until(me):
    sys.exit(0)

if rel is None:
    # Outside the repository (e.g. scratch files); Claude Code's own worktree isolation still applies.
    sys.exit(0)

owner = owner_of(rel)
if owner == me or is_granted(me, rel):
    sys.exit(0)

log_block(me, "edit", rel, f"owned by {owner}")
print(
    f"BLOCKED by team ownership guard: '{rel}' is owned by '{owner}', you are '{me}'.\n"
    f"Do not work around this guard (no sed/echo/cp via Bash either).\n"
    f"- If your task genuinely needs this file: run /conflict-report and wait.\n"
    f"- If you believe this block is wrong (your work is correct and in scope): run /conflict-report "
    f"with 'guard friction' in the title and the exact path above, so the orchestrator can fix the guard.",
    file=sys.stderr,
)
sys.exit(2)
