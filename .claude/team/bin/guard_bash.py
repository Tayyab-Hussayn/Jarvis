#!/usr/bin/env python3
"""PreToolUse hook for Bash: git/gh guardrails for the agent team.

The command is parsed into individual simple commands (quotes respected, heredoc bodies ignored),
so text inside commit messages, PR bodies, or file names never triggers a block.
This is a guardrail against mistakes, not a security boundary.
Exit code 2 blocks the command and shows stderr to Claude.
"""
import json
import re
import shlex
import sys

from team_lib import (PROTECTED_STATE_MARKER, RUNTIME_MARKER, current_branch, frozen_match, git, load_roster, log_block,
                      resolve_identity, to_repo_relative, unguard_until)

SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", ";;"}
VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}      # git global options taking a value
WRITE_CAPABLE = {"rm", "mv", "cp", "touch", "tee", "truncate", "ln", "install", "chmod", "chown", "dd", "sed",
                 "perl", "python", "python3", "bash", "sh", "zsh", "echo", "printf", "rsync", "find", "xargs"}


def simple_commands(cmd: str) -> list[list[str]]:
    """Split a shell string into simple commands (token lists). Heredoc bodies are dropped."""
    lines, kept, i = cmd.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        kept.append(line)
        m = re.search(r"<<-?\s*['\"]?([A-Za-z_]\w*)['\"]?", line)
        if m:
            term = m.group(1)
            i += 1
            while i < len(lines) and lines[i].strip() != term:
                i += 1
        i += 1
    out: list[list[str]] = []
    for line in kept:
        # fd duplication (2>&1, >&2) never writes a file; drop it so '&' is not read as a separator.
        line = re.sub(r"\d*>&\d+", " ", line)
        try:
            lex = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
            lex.whitespace_split = True
            tokens = list(lex)
        except ValueError:
            tokens = line.split()
        cur: list[str] = []
        for t in tokens:
            if t in SEPARATORS or set(t) <= set(";&|()"):
                if cur:
                    out.append(cur)
                cur = []
            else:
                cur.append(t)
        if cur:
            out.append(cur)
    return out


def targets_dir(tokens: list[str], marker: str) -> bool:
    """True when a parsed command would write into <marker>/: some token *is* a path there
    (optionally after a redirect) and the command writes (redirect or write-capable program).
    Tokens that merely mention the path inside an expression (sed script, quoted code) do not count."""
    def is_path(t: str) -> bool:
        t = t.lstrip("12&>")
        if not t or re.match(r"^[sy]([#/|,:]).*\1", t) or any(c in t for c in " '\"(=\\"):
            return False                      # sed/tr scripts, quoted code, assignments: not a path
        return t.startswith((marker, "./" + marker)) or f"/{marker}" in t
    paths = [t for t in tokens if is_path(t)]
    if not paths:
        return False
    prog = tokens[0].rsplit("/", 1)[-1]
    redirect = any(t.startswith(">") or t in (">>", "1>", "2>") or t.endswith(">") for t in tokens)
    return redirect or prog in WRITE_CAPABLE


# Programs whose path arguments are all write targets, and those that write only to their last argument.
WRITES_ALL_ARGS = {"rm", "mv", "touch", "tee", "truncate", "chmod", "chown", "unlink", "shred"}
WRITES_LAST_ARG = {"cp", "install", "ln", "rsync"}


def frozen_write_target(tokens: list[str], cwd: str) -> str | None:
    """The first frozen roster path this simple command would write, if any.

    Edit/Write are stopped by guard_edits.py; this closes the shell route (`sed -i`, `rm`, `> file`,
    `cp x frozen/`, `git rm`). Reads of frozen files (cat, grep, cp *from* a frozen path) stay allowed.
    """
    targets: list[str] = []
    for i, t in enumerate(tokens):                                   # redirections: `> f`, `>>f`, `2> f`
        m = re.fullmatch(r"\d*>>?|&>>?", t)
        if m and i + 1 < len(tokens):
            targets.append(tokens[i + 1])
        elif re.match(r"^(\d*>>?|&>>?)\S", t):
            targets.append(re.sub(r"^(\d*>>?|&>>?)", "", t))
    args = [t for t in without_redirects(tokens)[1:] if not t.startswith("-")]
    prog = tokens[0].rsplit("/", 1)[-1] if tokens else ""
    if prog in WRITES_ALL_ARGS:
        targets += args
    elif prog in WRITES_LAST_ARG and args:
        targets.append(args[-1])
    elif prog == "sed" and any(t.startswith("-i") or t == "--in-place" for t in tokens):
        targets += args[1:]                                          # first non-option arg is the script
    elif prog == "git" and git_subcommand(tokens)[0] in ("rm", "mv", "restore", "checkout", "clean"):
        targets += [a for a in git_subcommand(tokens)[1] if not a.startswith("-")]
    for t in targets:
        rel = to_repo_relative(t, cwd)
        if rel is not None and rel not in ("", ".") and frozen_match(rel):
            return rel
    return None


def without_redirects(tokens: list[str]) -> list[str]:
    """Drop shell redirections (`> f`, `2>/dev/null`, `&>f`, `< f`) so they are never read as git arguments."""
    out, skip = [], False
    for t in tokens:
        if skip:
            skip = False
            continue
        if re.fullmatch(r"\d*(>>?|<)|&>>?", t):        # bare operator: the next token is its target
            skip = True
            continue
        if re.match(r"^(\d*(>>?|<)|&>>?)\S", t):       # operator glued to its target
            continue
        out.append(t)
    return out


def git_subcommand(tokens: list[str]) -> tuple[str, list[str]]:
    """For ['git', '-C', 'x', 'push', 'origin', 'b'] return ('push', ['origin', 'b']).
    Any prefix before the git binary (VAR=value, env -u X, timeout 60, nice, ...) is ignored,
    so wrappers can neither hide a git command nor confuse its arguments."""
    tokens = without_redirects(tokens)
    starts = [i for i, t in enumerate(tokens) if t.rsplit("/", 1)[-1] == "git"]
    if not starts:
        return "", []
    tokens = tokens[starts[0]:]
    i = 1
    while i < len(tokens) and tokens[i].startswith("-"):
        i += 2 if tokens[i] in VALUE_OPTS else 1
    return (tokens[i], tokens[i + 1:]) if i < len(tokens) else ("", [])


def main() -> None:
    data = json.load(sys.stdin)
    cmd = (data.get("tool_input") or {}).get("command", "")
    cwd = data.get("cwd") or "."
    me = resolve_identity(cwd)

    def block(reason: str) -> None:
        log_block(me, "bash", cmd, reason)
        print(f"BLOCKED by team git guard ({me}): {reason}\n"
              f"If you believe this block is wrong for correct, in-scope work, run /conflict-report with "
              f"'guard friction' in the title and the exact command, so the orchestrator can fix the guard.",
              file=sys.stderr)
        sys.exit(2)

    # Owner-only runtime switches: no Claude session may write them (orchestrator included).
    # Only parsed commands count (heredoc bodies and quoted text in other files are not writes);
    # reading the state (cat/tail/grep/jq/...) is allowed.
    for tokens in simple_commands(cmd):
        if tokens and targets_dir(tokens, PROTECTED_STATE_MARKER):
            block(".claude/team/state/ is owner-only runtime state; only the owner changes it from a terminal.")
        if tokens and me != "orchestrator" and targets_dir(tokens, RUNTIME_MARKER):
            block("grants are written only by the orchestrator (`team grant`). Ask for one with /conflict-report.")
        hit = frozen_write_target(tokens, cwd) if tokens else None
        if hit:
            block(f"'{hit}' is frozen (roster.json \"frozen\"): nobody writes it, orchestrator included. "
                  "Edit roster.json to unfreeze it for a task.")
        names = [t.rsplit("/", 1)[-1] for t in tokens]
        if me != "orchestrator" and any(n == "team" and nxt in ("grant", "revoke") for n, nxt in zip(names, names[1:])):
            block("`team grant/revoke` is for the orchestrator. Ask for a grant with /conflict-report.")

    if "git" not in cmd and "gh" not in cmd and "team" not in cmd:
        sys.exit(0)
    if me != "orchestrator" and unguard_until(me):
        sys.exit(0)

    roster = load_roster()
    base = roster["base_branch"]
    own_branch = roster["agents"][me]["branch"] if me != "orchestrator" else None

    for tokens in simple_commands(cmd):
        hit = frozen_write_target(tokens, cwd) if tokens else None
        if hit:
            block(f"'{hit}' is frozen (roster.json \"frozen\"): nobody writes it, orchestrator included. "
                  "Edit roster.json to unfreeze it for a task.")
        names = [t.rsplit("/", 1)[-1] for t in tokens]
        if any(n == "team" and nxt in ("unguard", "guard") for n, nxt in zip(names, names[1:])):
            block("`team unguard/guard` is owner-only; ask the owner to run it in a plain terminal.")
        sub, args = git_subcommand(tokens)

        if sub == "push":
            positional = [a for a in args if not a.startswith("-")]
            if any(a in ("--force", "-f") or a.startswith("--force") for a in args) or any(p.startswith("+") for p in positional[1:]):
                block("force-push is never allowed. After a branch is pushed, integrate main with `git merge origin/main`, not rebase.")
            refspecs = positional[1:]
            dest = [r.split(":")[-1].removeprefix("refs/heads/") for r in refspecs]
            dest = [current_branch(cwd) if d == "HEAD" else d for d in dest if d]
            if "--delete" in args or "-d" in args or any(r.startswith(":") for r in refspecs):
                if me != "orchestrator":
                    block("deleting remote branches is reserved for the orchestrator.")
            if base in dest:
                changed = git(["diff", "--name-only", f"origin/{base}..HEAD"], cwd).splitlines()
                if not (me == "orchestrator" and changed and all(f.startswith("kb/") for f in changed)):
                    block(f"direct push to '{base}' is not allowed (orchestrator: only commits that change kb/ alone may go "
                          f"direct — commit first in a separate command). Everything else reaches {base} via a pull request.")
            if me != "orchestrator":
                if any(d != own_branch for d in dest):
                    block(f"you may push only to '{own_branch}'.")
                if not dest and current_branch(cwd) != own_branch:
                    block(f"current branch is not '{own_branch}'; push only from your own branch.")

        # No checkout/switch rule: git itself refuses to check out a branch used by another worktree
        # (main, other agents' branches), and `git checkout main -- <file>` is a legitimate file restore.

        elif sub == "branch" and me != "orchestrator":
            if any(a in ("-D", "-d", "--delete") for a in args):
                names = [a for a in args if not a.startswith("-")]
                protected = [n for n in names if n == base or (n.startswith("agent/") and n != own_branch)]
                if protected:
                    block(f"you may not delete {', '.join(protected)}; local helper branches of your own are fine.")

        elif tokens[:2] == ["gh", "pr"] and me != "orchestrator" and len(tokens) > 2:
            action = tokens[2]
            if action in ("merge", "close") or (action == "review" and "--approve" in tokens):
                block("only the orchestrator merges, approves, or closes pull requests.")

    sys.exit(0)


if __name__ == "__main__":
    main()
