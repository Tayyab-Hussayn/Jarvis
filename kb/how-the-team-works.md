---
type: process
owner: orchestrator
updated: 2026-10-22
---

# How the Team Works

```text
   your terminal                          tmux session "kravein-team"
 ┌────────────────┐   SendMessage   ┌──────────┬──────────┬──────────┬──────────┐
 │  orchestrator  │ ◀─────────────▶ │ engine   │ skills   │   web    │   qa     │
 │  (main, lead)  │   issues / PRs  │agent/... │agent/... │agent/... │agent/... │
 └───────▲────────┘                 └────▲─────┴────▲─────┴────▲─────┴────▲─────┘
         │ owner talks here mostly       └──── owner can attach and talk to any agent ────┘
```

## Everyday use (owner)
| What | Command (run in the repository root) |
|---|---|
| Start/resume the orchestrator | `.claude/team/bin/start-lead.sh` — always this, never plain `claude`: otherwise the persona and the `orchestrator` name are missing |
| Start/resume agents (the orchestrator normally does this) | `.claude/team/bin/team up engine` · all: `team up` · stop one: `team stop engine` |
| Look at the agents | `.claude/team/bin/team attach` — `Ctrl-b 1..4` switch window, `Ctrl-b d` detach |
| Talk to one agent directly | `team attach engine`, then type |
| Lift guards for an agent (owner only, plain terminal) | `team unguard engine 60` · restore: `team guard engine` |
| Switch the Claude account for launches | `team account 2` (second account, `~/.claude-account2`) · back: `team account 1` · show: `team account` · then `team stop <agent>` + `team up <agent>`; orchestrator: `start-lead.sh --account 2`. Only sets `CLAUDE_CONFIG_DIR`; never logs out. A conversation lives in its account, so a switched agent starts fresh. |
| See everything | `team status` |
| End of day | tell the orchestrator "now end the session" → it shuts agents down → you `/exit` it |

## Models
| Who | Model | Note |
|---|---|---|
| Orchestrator | Opus | planning, review, merges |
| Agent, any task | Sonnet, effort medium | always — owner rule 2026-10-23 (token cost), enforced in `start-agent.sh`; hard tasks get a stricter packet, and the orchestrator's review sends mistakes back | One or two agents active at a time.

## Lifecycle of a task
1. Orchestrator: `/assign-task` → GitHub issue (task packet) + message.
2. Agent: `/task-start` → plan comment → code + tests → `/verify` → `/ship-pr`.
3. Orchestrator: `/review-pr` → fix small issues or request changes → `/merge-pr` (squash).
4. Agent: `/sync-after-merge` → next task.

## Conflict protocol
Agent needs a file it does not own, or a file another open PR changes → **stops**, `/conflict-report`, messages orchestrator, waits. Orchestrator resolves and sends the go-ahead **only after** the blocking PR is merged.

## Guard levels
| Level | What | Why |
|---|---|---|
| **Frozen paths** | roster `frozen.paths` (Windows-original tree) are writable by nobody, orchestrator included | untouched on purpose; edit the roster to unfreeze for a task |
| **Hard, always** | force-push · push to `main` · push to another agent's branch · agents merging/approving/closing PRs · deleting `main` or another agent's branch · writing owner overrides or grants | irreversible or bypasses review — correct work never needs these |
| **Hard, with a grant path** | editing files outside your ownership | prevents cross-agent conflicts; the orchestrator can `team grant` a path for one task instead of making the agent wait |
| **Left to git** | switching to `main`, deleting a branch in use | git itself refuses inside worktrees |
| **Owner override** | `team unguard <agent>` lifts everything for that agent for N minutes | owner's direct control |

## Guard friction
If a guard blocks correct work twice, the agent files `/conflict-report` titled `guard friction` with the exact command/path. Every block is logged in `.claude/team/state/guard-log.jsonl`; the orchestrator's briefing shows block counts per agent and fixes the guard (with a test) or corrects the agent's approach.

## Reboot survival
| Survives a reboot | How it comes back |
|---|---|
| Committed work, branches, worktrees (`.claude/worktrees/`) | on disk |
| Issues, PRs, reviews | GitHub |
| Knowledge base, rules, personas, skills | git |
| Each session's conversation | transcripts on disk → `start-lead.sh` / `team up` resume by name **into the right worktree** |
| Uncommitted edits | on disk in the worktree (shutdown commits them as `wip:`) |
| Running processes, tmux session | **not** kept → run `start-lead.sh`, then `team up` |
| Owner overrides | kept until they expire |

## Why it does not drift
| Layer | Loaded when | Survives compaction |
|---|---|---|
| `CLAUDE.md` + [[project-state]] (imported) | every session | yes — re-read from disk |
| `.claude/rules/*.md` without `paths` | every session | yes |
| Persona (`--append-system-prompt-file`) | every session | yes — part of system prompt |
| SessionStart hook briefing | startup, resume, clear, **compact** | yes — re-runs after compaction |
| Ownership + git guard hooks | every edit / git command | enforced, not remembered |
| Module notes `kb/modules/*` | on demand | re-read when needed |
