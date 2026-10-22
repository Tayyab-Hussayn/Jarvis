#!/usr/bin/env python3
"""SessionStart hook (startup | resume | clear | compact).

Prints a short, live briefing that Claude Code adds to context. Because it also runs after
compaction, every session re-learns who it is, what it owns, and what is in flight.
Network calls (gh) are best-effort with short timeouts; the briefing never fails the session.
"""
import json
import subprocess
import sys
import time

from team_lib import (active_grants, current_branch, git, load_roster, owned_paths, recent_blocks, resolve_identity,
                      unguard_until, worktree_root)

data = json.load(sys.stdin) if not sys.stdin.isatty() else {}
cwd = data.get("cwd") or "."
source = data.get("source", "startup")
me = resolve_identity(cwd)
roster = load_roster()
base = roster["base_branch"]
root = worktree_root(cwd)


def gh_json(args: list[str]) -> list:
    try:
        out = subprocess.run(["gh", *args], cwd=root, capture_output=True, text=True, timeout=10)
        return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else []
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []


lines = [f"# Team briefing ({source}) — you are **{me}**"]
if source == "compact":
    lines.append("Context was just compacted. Re-read your persona rules and this briefing before continuing; "
                 "do not rely on memory of earlier conversation for ownership or task scope.")

branch = current_branch(cwd)
if me == "orchestrator":
    lines.append(f"Role: orchestrator / team lead. Persona: {roster['orchestrator']['persona']}. Branch: {branch or '?'}")
else:
    a = roster["agents"][me]
    lines.append(f"Role: module agent — {a['summary']}.")
    lines.append(f"Persona: {a['persona']} · Your branch: {a['branch']} (current: {branch or '?'})")
    if branch and branch != a["branch"]:
        lines.append(f"WARNING: you are not on {a['branch']}. Do not edit anything; tell the orchestrator.")

lines.append("You may edit ONLY: " + ", ".join(owned_paths(me)))
if me != "orchestrator":
    until = unguard_until(me)
    if until:
        lines.append(f"OWNER OVERRIDE ACTIVE until {time.strftime('%H:%M', time.localtime(until))}: team guards are lifted "
                     "for you. Follow the owner's direct instructions; still commit on your branch and tell the orchestrator what changed.")
    else:
        lines.append("Anything else is read-only for you → STOP and run /conflict-report if you need it changed.")
    grants = active_grants(me)
    if grants:
        lines.append("Active grants (extra writable paths): " + "; ".join(
            f"{g['path']} for #{g['issue']} until {time.strftime('%H:%M', time.localtime(g['until']))}" for g in grants))

git(["fetch", "origin", "--quiet"], root, timeout=6)
ahead_behind = git(["rev-list", "--left-right", "--count", f"origin/{base}...HEAD"], root)
dirty = git(["status", "--porcelain"], root)
if ahead_behind:
    behind, ahead = ahead_behind.split()
    lines.append(f"Git: {ahead} commit(s) ahead / {behind} behind origin/{base}; "
                 f"{len(dirty.splitlines())} uncommitted change(s).")

issues = gh_json(["issue", "list", "--state", "open", "--limit", "100", "--json", "number,title,labels"])
prs = gh_json(["pr", "list", "--state", "open", "--json", "number,title,headRefName"])


def labelled(label: str) -> list:
    return [i for i in issues if any(l["name"] == label for l in i.get("labels", []))]


def fmt(items: list) -> str:
    return "; ".join(f"#{i['number']} {i['title']}" for i in items)


if me == "orchestrator":
    lines.append("Open PRs: " + ("; ".join(f"#{p['number']} [{p['headRefName']}] {p['title']}" for p in prs) or "none"))
    lines.append("Blocked issues: " + (fmt(labelled("status:blocked")) or "none"))
    for name, a in roster["agents"].items():
        tag = " [OWNER OVERRIDE]" if unguard_until(name) else ""
        lines.append(f"- {name}{tag}: " + (fmt(labelled(a["label"])) or "no open tasks"))
    blocks = recent_blocks(24)
    if blocks:
        lines.append("Guard blocks (24h): " + ", ".join(f"{k}={v}" for k, v in sorted(blocks.items()))
                     + " — repeated blocks on correct work mean the guard needs fixing (see .claude/team/state/guard-log.jsonl).")
else:
    a = roster["agents"][me]
    lines.append("Your open tasks: " + (fmt(labelled(a["label"])) or "none — wait for the orchestrator"))
    mine = [p for p in prs if p["headRefName"] == a["branch"]]
    if mine:
        lines.append("Your open PR: " + "; ".join(f"#{p['number']} {p['title']}" for p in mine)
                     + " — wait for review; do not start code for the next task until it is merged.")

lines.append("Project state: kb/state/project-state.md · KB index: kb/index.md · Team rules: .claude/rules/team-protocol.md")
print("\n".join(lines))
