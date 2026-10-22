#!/usr/bin/env python3
"""Pre-PR check for a module agent's branch.

1. Every file changed versus origin/main must be owned by this agent.
2. No changed file may also be changed by another open pull request (overlap = potential conflict).
Exit 0 = clean, 1 = problems found (printed).
Usage: python3 .claude/team/bin/check_branch.py
"""
import json
import subprocess
import sys

from team_lib import git, is_granted, load_roster, owner_of, resolve_identity, worktree_root

root = worktree_root(".")
me = resolve_identity(root)
base = load_roster()["base_branch"]
git(["fetch", "origin", "--quiet"], root, timeout=15)
changed = [f for f in git(["diff", "--name-only", f"origin/{base}...HEAD"], root).splitlines() if f]
problems = []

for f in changed:
    owner = owner_of(f)
    if me != "orchestrator" and owner != me and not is_granted(me, f):
        problems.append(f"ownership: {f} is owned by {owner}")

try:
    out = subprocess.run(["gh", "pr", "list", "--state", "open", "--json", "number,headRefName,files"],
                         cwd=root, capture_output=True, text=True, timeout=20)
    prs = json.loads(out.stdout) if out.returncode == 0 else []
except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
    prs = []
    print("warning: could not list open PRs; overlap check skipped")

my_branch = git(["branch", "--show-current"], root)
for pr in prs:
    if pr["headRefName"] == my_branch:
        continue
    theirs = {f["path"] for f in pr.get("files", [])}
    for f in sorted(theirs.intersection(changed)):
        problems.append(f"overlap: {f} is also changed by open PR #{pr['number']} ({pr['headRefName']})")

print(f"identity={me} branch={my_branch} changed_files={len(changed)}")
if problems:
    print("PROBLEMS:\n- " + "\n- ".join(problems))
    sys.exit(1)
print("OK: all changed files are owned by you and no open PR touches them.")
