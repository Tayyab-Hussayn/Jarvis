"""Shared helpers for the agent-team hooks and scripts.

Identity resolution order:
  1. KRV_AGENT environment variable (set by start-agent.sh / start-lead.sh)
  2. current git branch "agent/<name>" of the working directory
  3. otherwise: orchestrator (main checkout)
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

TEAM_DIR = Path(__file__).resolve().parent.parent          # .claude/team (of whichever checkout runs this)
PROJECT_ROOT = TEAM_DIR.parent.parent                       # repo root of the checkout holding this file


def _main_checkout(start: Path) -> Path:
    """Main checkout root, also when running from a worktree (git common dir is <main>/.git)."""
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=start,
                                capture_output=True, text=True, timeout=5).stdout.strip()
        if common:
            return Path(common).parent
    except (OSError, subprocess.TimeoutExpired):
        pass
    return start


MAIN_ROOT = _main_checkout(PROJECT_ROOT)
STATE_DIR = MAIN_ROOT / ".claude/team/state"               # owner-only runtime state (overrides, guard log)
RUNTIME_DIR = MAIN_ROOT / ".claude/team/runtime"           # orchestrator-written runtime state (grants)
GUARD_LOG = STATE_DIR / "guard-log.jsonl"
GRANTS_FILE = RUNTIME_DIR / "grants.json"
PROTECTED_STATE_MARKER = ".claude/team/state"               # no Claude session may write here (owner-only)
RUNTIME_MARKER = ".claude/team/runtime"                     # module agents may not write here


def active_grants(identity: str) -> list[dict]:
    """Temporary extra paths the orchestrator granted this agent for a specific task."""
    import time
    try:
        grants = json.loads(GRANTS_FILE.read_text()).get(identity, [])
    except (OSError, ValueError):
        return []
    return [g for g in grants if g.get("until", 0) > time.time()]


def _normalize(rel_path: str) -> str:
    """Drop leading "./" segments. Not str.lstrip("./"): that also eats the dot of ".github/"."""
    while rel_path.startswith("./"):
        rel_path = rel_path[2:]
    return rel_path


def is_granted(identity: str, rel_path: str) -> bool:
    rel = _normalize(rel_path)
    return any(_matches(rel, g["path"]) for g in active_grants(identity))


def log_block(identity: str, guard: str, subject: str, reason: str) -> None:
    """Append one guard block to the shared log so the orchestrator can spot friction."""
    import time
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with GUARD_LOG.open("a") as f:
            f.write(json.dumps({"ts": int(time.time()), "agent": identity, "guard": guard,
                                "subject": subject[:200], "reason": reason[:200]}) + "\n")
    except OSError:
        pass


def unguard_until(identity: str) -> int:
    """Epoch seconds until which the owner has lifted guards for this identity (0 = guarded).
    Created only by the owner via `.claude/team/bin/team unguard <agent> [minutes]` in a plain terminal."""
    import time
    try:
        until = int((STATE_DIR / f"unguard-{identity}").read_text().strip())
    except (OSError, ValueError):
        return 0
    return until if until > time.time() else 0


def recent_blocks(hours: int = 24) -> dict:
    """Count guard blocks per agent in the last `hours`."""
    import time
    counts: dict = {}
    cutoff = time.time() - hours * 3600
    try:
        for line in GUARD_LOG.read_text().splitlines():
            e = json.loads(line)
            if e.get("ts", 0) >= cutoff:
                counts[e["agent"]] = counts.get(e["agent"], 0) + 1
    except (OSError, ValueError):
        pass
    return counts


def load_roster() -> dict:
    return json.loads((TEAM_DIR / "roster.json").read_text())


def git(args: list[str], cwd: str | Path, timeout: float = 5.0) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def worktree_root(cwd: str | Path) -> Path:
    top = git(["rev-parse", "--show-toplevel"], cwd)
    return Path(top) if top else Path(cwd)


def current_branch(cwd: str | Path) -> str:
    return git(["branch", "--show-current"], cwd)


def resolve_identity(cwd: str | Path) -> str:
    roster = load_roster()
    env = os.environ.get("KRV_AGENT", "").strip()
    if env == "orchestrator" or env in roster["agents"]:
        return env
    branch = current_branch(cwd)
    if branch.startswith("agent/") and branch[len("agent/"):] in roster["agents"]:
        return branch[len("agent/"):]
    return "orchestrator"


def owned_paths(identity: str) -> list[str]:
    roster = load_roster()
    if identity == "orchestrator":
        return roster["orchestrator"]["owns"]
    return roster["agents"][identity]["owns"]


def _matches(rel: str, entry: str) -> bool:
    return rel.startswith(entry) if entry.endswith("/") else rel == entry


def frozen_match(rel_path: str) -> str | None:
    """The roster["frozen"]["paths"] entry covering this repo-relative path, if any (same prefix/exact rules as owns)."""
    rel = _normalize(rel_path)
    for entry in load_roster().get("frozen", {}).get("paths", []):
        if _matches(rel, entry):
            return entry
    return None


def owner_of(rel_path: str) -> str:
    """Return the owner of a repo-relative path. Unlisted paths belong to the orchestrator."""
    roster = load_roster()
    rel = _normalize(rel_path)
    # Agents first: their entries (e.g. kb/modules/core.md) are more specific than orchestrator prefixes (kb/).
    for name, agent in roster["agents"].items():
        if any(_matches(rel, e) for e in agent["owns"]):
            return name
    return "orchestrator"


def to_repo_relative(file_path: str, cwd: str | Path) -> str | None:
    """Map an absolute or relative path to a path relative to the current worktree root.
    Returns None when the path lies outside the worktree."""
    root = worktree_root(cwd).resolve()
    p = Path(file_path)
    if not p.is_absolute():
        p = Path(cwd) / p
    try:
        return str(p.resolve().relative_to(root))
    except ValueError:
        return None
