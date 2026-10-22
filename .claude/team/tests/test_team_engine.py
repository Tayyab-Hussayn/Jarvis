#!/usr/bin/env python3
"""Scenario tests for the agent-team engine (guards, briefing, grants, owner override, team CLI).

Builds a throwaway repository (with a bare 'origin' and an engine worktree) from the current engine files,
then exercises every hook as Claude Code would call it. Never touches the real repository.

Run:  python3 .claude/team/tests/test_team_engine.py
Add a case here for every guard bug (guard friction) before fixing it.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[3]          # repository root
T = Path(tempfile.mkdtemp(prefix="kravein-team-test-"))
G = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
M = "main"
res: list[bool] = []


def sh(args, cwd, env=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=env)


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in ("KRV_AGENT", "CLAUDECODE")}
    env.update(extra)
    return env


# ---- fixture repository ----
R = T / "repo"
R.mkdir(parents=True)
sh(["git", "init", "-q", "-b", M], R)
for name in [".claude", "kb", "CLAUDE.md", ".gitignore"]:
    s = SRC / name
    if not s.exists():
        continue
    if s.is_dir():
        def skip(d, names):   # machine-local runtime dirs only, not e.g. kb/state
            local = {"state", "runtime"} if d.replace("\\", "/").endswith(".claude/team") else set()
            return [n for n in names if n in local or n in ("worktrees", "__pycache__")]
        shutil.copytree(s, R / name, ignore=skip)
    else:
        shutil.copy(s, R / name)
for f, c in {"kravein/agent.py": "x", "kravein/skills/a.py": "y", "docs/spec.md": "z", "src/main.kt": "m",
             "kb/state/project-state.md": "# v1\n", "kb/modules/engine.md": "e", "kb/modules/skills.md": "s",
             "main.py": "win", "actions/a.py": "win", "extra/A.java": "j"}.items():
    (R / f).parent.mkdir(parents=True, exist_ok=True)
    (R / f).write_text(c)
sh(["git", "add", "-A"], R)
sh(G + ["commit", "-qm", "init"], R)
sh(["git", "clone", "-q", "--bare", "repo", "origin.git"], T)
sh(["git", "remote", "add", "origin", "../origin.git"], R)
sh(["git", "fetch", "-q", "origin"], R)
sh(["git", "worktree", "add", "-q", ".claude/worktrees/engine", "-b", "agent/engine", f"origin/{M}"], R)
WT = R / ".claude/worktrees/engine"
BIN = R / ".claude/team/bin"
STATE = R / ".claude/team/state"


def hook(script, payload, agent=None):
    env = clean_env(**({"KRV_AGENT": agent} if agent else {}))
    p = subprocess.run([sys.executable, str(BIN / script)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    return p.returncode, (p.stdout + p.stderr).strip()


def expect(name, got, want, out=""):
    ok = got == want
    res.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got}, want {want})\n      {out[:300]}"))


def edit(path, cwd=WT, agent=None):
    return hook("guard_edits.py", {"cwd": str(cwd), "tool_input": {"file_path": str(path)}}, agent)


def bash(cmd, cwd=WT, agent=None):
    return hook("guard_bash.py", {"cwd": str(cwd), "tool_input": {"command": cmd}}, agent)


print("== edit guard ==")
for name, path, want in [
    ("own file", WT / "kravein/agent.py", 0), ("skills file", WT / "kravein/skills/a.py", 2),
    ("own kb note", WT / "kb/modules/engine.md", 0), ("skills kb note", WT / "kb/modules/skills.md", 2),
    ("project-state", WT / "kb/state/project-state.md", 2), ("roster", WT / ".claude/team/roster.json", 2),
    ("outside repo", Path("/tmp/x.txt"), 0), ("state dir", WT / ".claude/team/state/unguard-engine", 2),
]:
    c, o = edit(path)
    expect(f"edit: engine -> {name}", c, want, o)
c, o = edit(R / "kravein/skills/a.py", cwd=R)
expect("edit: orchestrator may edit anything", c, 0, o)
c, o = edit(R / ".claude/team/state/unguard-engine", cwd=R)
expect("edit: orchestrator blocked from owner-only state", c, 2, o)

print("== bash guard: must block ==")
for name, cmd, cwd in [
    ("push other agent branch", "git push origin agent/skills", WT),
    ("push main", f"git push origin {M}", WT),
    ("push refs/heads/main", f"git push origin HEAD:refs/heads/{M}", WT),
    ("force", "git push --force", WT),
    ("force-with-lease", "git push --force-with-lease origin agent/engine", WT),
    ("+refspec", "git push origin +agent/engine", WT),
    ("gh pr merge", "gh pr merge 3 --squash", WT),
    ("gh pr approve", "gh pr review 3 --approve", WT),
    ("delete other agent branch", "git branch -D agent/skills", WT),
    ("delete main", f"git branch -d {M}", WT),
    ("delete remote branch", "git push origin --delete agent/skills", WT),
    ("agent team grant", ".claude/team/bin/team grant engine kravein/skills/ 2 5", WT),
    ("agent writes grants file", "echo x > .claude/team/runtime/grants.json", WT),
    ("chained push main", f"git add . && git commit -m ok && git push origin {M}", WT),
    ("git -C push main", f"git -C . push origin {M}", WT),
    ("write state via redirect", "echo 1 > .claude/team/state/unguard-engine", WT),
    ("rm state file", "rm .claude/team/state/guard-log.jsonl", WT),
    ("team unguard", ".claude/team/bin/team unguard engine 60", WT),
    ("env -u team unguard", "env -u CLAUDECODE .claude/team/bin/team unguard engine", WT),
    ("orchestrator team unguard", ".claude/team/bin/team unguard skills", R),
    ("env -u X git push main (wrapper bypass)", f"env -u FOO git push origin {M}", WT),
    ("timeout git push other agent", "timeout 60 git push origin agent/skills 2>&1", WT),
    ("push main with redirect", f"git push origin {M} >/dev/null 2>&1", WT),
    ("orchestrator push main (non-kb)", f"git push origin {M}", R),
    ("orchestrator force push", "git push -f origin agent/engine", R),
]:
    c, o = bash(cmd, cwd)
    expect(f"block: {name}", c, 2, o)

print("== bash guard: must allow ==")
heredoc = f"gh pr create --body-file - <<'EOF'\nSteps: git switch {M}\ngit push origin {M}\nEOF"
for name, cmd, cwd in [
    ("push own branch", "git push -u origin agent/engine", WT),
    ("push own branch 2>&1 (issue #10)", "git push -u origin agent/engine 2>&1", WT),
    ("push own branch piped (issue #10)", "git push -u origin agent/engine 2>&1 | tail -5", WT),
    ("push own branch >/dev/null (issue #10)", "git push -u origin agent/engine >/dev/null 2>&1", WT),
    ("push own branch with cd prefix (issue #10)", "cd /repo && git push -u origin agent/engine", WT),
    ("push own branch with env prefix (issue #10)", "GIT_TERMINAL_PROMPT=0 git push -u origin agent/engine", WT),
    ("push own branch with timeout (issue #10)", "timeout 60 git push -u origin agent/engine", WT),
    ("plain push", "git push", WT),
    ("push HEAD on own branch", "git push origin HEAD", WT),
    ("commit message mentions push main", f'git commit -m "docs: never git push origin {M}"', WT),
    ("heredoc PR body mentions main", heredoc, WT),
    ("checkout file src/main.kt", "git checkout src/main.kt", WT),
    ("checkout -- file", f"git checkout -- {M}.kt", WT),
    ("restore file from main", f"git checkout {M} -- kravein/agent.py", WT),
    ("switch main (git itself refuses)", f"git switch {M}", WT),
    ("sync switch -C", f"git switch -C agent/engine origin/{M}", WT),
    ("merge origin/main", f"git merge origin/{M}", WT),
    ("delete own helper branch", "git branch -D engine-scratch", WT),
    ("read guard log", "tail -n 20 .claude/team/state/guard-log.jsonl", WT),
    ("read grants", "cat .claude/team/runtime/grants.json", WT),
    ("python heredoc editing .gitignore", "python3 - <<'EOF'\nopen('.gitignore','a').write('.claude/team/state/\\n')\nEOF", WT),
    ("sed on .gitignore mentioning state path", "sed -i 's#^\\.claude/team/state/$#x#' .gitignore", WT),
    ("team status", ".claude/team/bin/team status", WT),
    ("unrelated command", "ls && pytest -q", WT),
    ("orchestrator merges PR", "gh pr merge 3 --squash --delete-branch", R),
    ("orchestrator pushes review fix", "git push origin HEAD:agent/engine", R),
    ("orchestrator team grant", ".claude/team/bin/team grant skills kravein/platform/ 2 7", R),
]:
    c, o = bash(cmd, cwd)
    expect(f"allow: {name}", c, 0, o)

print("== kb-only direct push (orchestrator) ==")
(R / "kb/state/project-state.md").write_text("# v2\n")
sh(G + ["commit", "-qam", "kb"], R)
c, o = bash(f"git push origin {M}", R)
expect("kb-only push allowed", c, 0, o)
(R / "docs/spec.md").write_text("v2\n")
sh(G + ["commit", "-qam", "docs"], R)
c, o = bash(f"git push origin {M}", R)
expect("kb+docs push blocked", c, 2, o)

print("== owner override ==")
STATE.mkdir(parents=True, exist_ok=True)
(STATE / "unguard-engine").write_text(str(int(time.time()) + 600))
c, o = edit(WT / "kravein/skills/a.py")
expect("unguarded engine may edit skills file", c, 0, o)
c, o = bash("git push origin agent/skills")
expect("unguarded engine push not blocked", c, 0, o)
c, o = edit(WT / "kravein/agent.py", agent="skills")
expect("override is per agent (skills still guarded)", c, 2, o)
(STATE / "unguard-engine").write_text(str(int(time.time()) - 1))
c, o = edit(WT / "kravein/skills/a.py")
expect("expired override -> guarded again", c, 2, o)

print("== guard log & briefing ==")
log = (STATE / "guard-log.jsonl").read_text().splitlines()
expect("blocks are logged", len(log) > 10, True, str(len(log)))
c, o = hook("session_context.py", {"cwd": str(R), "source": "startup"})
expect("orchestrator briefing shows guard block counts", "Guard blocks (24h)" in o, True, o)
(STATE / "unguard-engine").write_text(str(int(time.time()) + 600))
c, o = hook("session_context.py", {"cwd": str(WT), "source": "compact"})
expect("agent briefing after compaction names identity", "you are **engine**" in o and "compacted" in o, True, o)
expect("agent briefing shows owner override", "OWNER OVERRIDE ACTIVE" in o, True, o)
(STATE / "unguard-engine").unlink()

print("== grants ==")
p = sh([str(BIN / "team"), "grant", "engine", "kravein/skills/", "1", "5"], R, clean_env())
expect("orchestrator grants engine kravein/skills/", p.returncode, 0, p.stdout + p.stderr)
c, o = edit(WT / "kravein/skills/a.py")
expect("granted path editable", c, 0, o)
c, o = edit(WT / "kravein/platform/x.py")
expect("non-granted path still blocked", c, 2, o)
c, o = hook("session_context.py", {"cwd": str(WT), "source": "startup"})
expect("briefing lists grant", "Active grants" in o and "kravein/skills/" in o, True, o)
p = sh([str(BIN / "team"), "grant", "engine", "docs/", "1", "5"], R, clean_env(KRV_AGENT="engine"))
expect("team grant refused when run as an agent", p.returncode, 1, p.stdout + p.stderr)
(WT / "kravein/skills/a.py").write_text("granted change\n")
sh(G + ["commit", "-qam", "granted"], WT)
p = sh([sys.executable, str(WT / ".claude/team/bin/check_branch.py")], WT, clean_env())
expect("check_branch accepts granted file", p.returncode == 0 and "OK:" in p.stdout, True, p.stdout + p.stderr)
sh([str(BIN / "team"), "revoke", "engine"], R, clean_env())
p = sh([sys.executable, str(WT / ".claude/team/bin/check_branch.py")], WT, clean_env())
expect("check_branch flags file after revoke", p.returncode == 1 and "owned by skills" in p.stdout, True, p.stdout + p.stderr)
c, o = edit(WT / "kravein/skills/a.py")
expect("revoked grant -> edit blocked again", c, 2, o)
p = sh([str(BIN / "team"), "grant", "engine", ".github/workflows/x.yml", "1", "5"], R, clean_env())
p = sh([str(BIN / "team"), "grants"], R, clean_env())
expect("grant keeps the dot of .github/ (no lstrip)", ".github/workflows/x.yml" in p.stdout, True, p.stdout + p.stderr)
(WT / ".github/workflows").mkdir(parents=True, exist_ok=True)
c, o = edit(WT / ".github/workflows/x.yml")
expect("granted dot-path editable", c, 0, o)
sh([str(BIN / "team"), "revoke", "engine", "./.github/workflows/x.yml"], R, clean_env())
p = sh([str(BIN / "team"), "grants"], R, clean_env())
expect("revoke with ./ prefix removes the dot-path grant", ".github/workflows/x.yml" in p.stdout, False, p.stdout + p.stderr)

print("== team CLI ==")
p = sh([str(BIN / "team"), "unguard", "skills", "5"], R, clean_env(CLAUDECODE="1"))
expect("team unguard refused inside Claude", p.returncode, 1, p.stdout + p.stderr)
p = sh([str(BIN / "team"), "unguard", "skills", "5"], R, clean_env())
expect("team unguard works for owner", p.returncode == 0 and (STATE / "unguard-skills").exists(), True, p.stdout + p.stderr)
p = sh([str(BIN / "team"), "guard", "skills"], R, clean_env())
expect("team guard restores", p.returncode == 0 and not (STATE / "unguard-skills").exists(), True, p.stdout + p.stderr)
p = sh([str(BIN / "team"), "status"], R, clean_env())
expect("team status runs", p.returncode, 0, p.stdout + p.stderr)

print("== dotted paths ==")
def owner(path):
    code = f"import sys; sys.path.insert(0, {str(BIN)!r}); import team_lib; print(team_lib.owner_of({path!r}))"
    return sh([sys.executable, "-c", code], R, clean_env()).stdout.strip()
expect("./kravein/ path belongs to engine", owner("./kravein/agent.py"), "engine")
expect("kravein/skills/ belongs to skills", owner("kravein/skills/a.py"), "skills")
expect(".github/ISSUE_TEMPLATE stays orchestrator", owner(".github/ISSUE_TEMPLATE/task.md"), "orchestrator")
expect("unlisted path belongs to orchestrator", owner("docs/spec.md"), "orchestrator")

print("== frozen paths ==")
for name, path in [("main.py", "main.py"), ("actions/ prefix", "actions/a.py"),
                   ("extra dir", "extra/A.java")]:
    c, o = edit(WT / path, agent="engine")
    expect(f"frozen: agent blocked on {name}", c == 2 and "frozen" in o and "roster.json" in o, True, o)
    c, o = edit(R / path, cwd=R)
    expect(f"frozen: orchestrator blocked on {name}", c == 2 and "frozen" in o, True, o)
c, o = edit(R / "kravein/agent.py", cwd=R)
expect("frozen: orchestrator may still edit non-frozen", c, 0, o)
c, o = edit(WT / "kravein/agent.py", agent="engine")
expect("frozen: agent may still edit own non-frozen file", c, 0, o)
c, o = edit(WT / "main.pyx", agent="engine")
expect("frozen: exact entry does not prefix-match (main.pyx is not main.py)", "frozen" in o, False, o)
(STATE / "unguard-engine").write_text(str(int(time.time()) + 600))
c, o = edit(WT / "main.py", agent="engine")
expect("frozen: owner unguard does not lift a freeze", c, 2, o)
(STATE / "unguard-engine").unlink()
c, o = edit(R / ".claude/team/state/unguard-engine", cwd=R)
expect("frozen: state dir still blocked for orchestrator", c, 2, o)


print("== frozen paths via bash ==")
for cmd, want in [
    ("sed -i 's/a/b/' main.py", 2), ("rm -f actions/a.py", 2), ("echo x > main.py", 2), ("echo x >>main.py", 2),
    ("cp kravein/agent.py extra/A.java", 2), ("git rm actions/a.py", 2), ("mv main.py old.py", 2),
    ("cat main.py", 0), ("grep -n x actions/a.py", 0), ("cp main.py /tmp/x.py", 0), ("sed -n 1,5p main.py", 0),
    ("sed -i 's/a/b/' kravein/agent.py", 0), ("echo x > /tmp/main.py", 0), ("rm -f kravein/agent.pyc", 0),
]:
    c, o = bash(cmd, agent="engine")
    expect(f"bash frozen (engine): {cmd}", c, want, o)
c, o = bash("sed -i 's/a/b/' main.py", cwd=R)
expect("bash frozen: orchestrator blocked too", c, 2, o)
c, o = bash("sed -i 's/a/b/' CLAUDE.md", cwd=R)
expect("bash frozen: orchestrator may sed a non-frozen file", c, 0, o)

print("== launcher safety ==")
sl = (BIN / "start-lead.sh").read_text()
expect("start-lead.sh does not pass the resume id twice", 'local id="$1"' in sl and '--resume "$id" "$@"' in sl, True)
for f in sorted(p for p in BIN.iterdir() if p.is_file()):
    txt = f.read_text()
    expect(f"no --dangerously-skip-permissions in {f.name}", "dangerously-skip-permissions" not in txt, True)
    expect(f"no account switching in {f.name}", "CLAUDE_CONFIG_DIR" not in txt and "ACCOUNT2" not in txt, True)
expect("account.sh is gone", (BIN / "account.sh").exists(), False)
sa = (BIN / "start-agent.sh").read_text()
expect("start-agent.sh defaults to auto (never bypass)", "KRV_AGENT_PERMISSION_MODE:-auto" in sa and "bypassPermissions\"" not in sa, True)
expect("start-agent.sh links .venv into the worktree", 'ln -s "$ROOT/.venv"' in sa, True)
expect("no SSW leftovers in bin", any("SSW_" in f.read_text() for f in BIN.iterdir() if f.is_file()), False)

shutil.rmtree(T, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
