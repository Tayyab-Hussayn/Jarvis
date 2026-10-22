#!/usr/bin/env bash
# Start (or resume) one module agent as its own Claude Code session.
#   .claude/team/bin/start-agent.sh <agent> [--fresh] [--model <sonnet|opus|...>] [claude flags]
# Normally launched by `team up` inside tmux.
# - Ensures a git worktree at .claude/worktrees/<agent> on branch agent/<agent>.
# - Resumes the agent's previous conversation (survives reboots) unless --fresh is given.
#   `--worktree <agent> --resume <agent>` restores both the conversation and the worktree.
# - Model policy (roster.json "models"): Sonnet by default; Opus for hard tasks.
#   Pick the model at task start: a different model on resume discards the prompt cache once.
# - Appends the agent persona to Claude Code's default system prompt (does not replace it).
# - Permission mode defaults to `default`; set KRV_AGENT_PERMISSION_MODE=auto (or acceptEdits) to opt in.
# - Disables auto memory for agents: shared knowledge lives in git (kb/), not per-machine memory.
# - Accepts messages from other local sessions without an approval dialog (crossSessionInbound=accept):
#   otherwise a message from a session in a different permission mode is held, and unattended agents stall.
set -euo pipefail

AGENT="${1:-}"
shift || true
ROOT="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"   # main checkout
ROSTER="$ROOT/.claude/team/roster.json"
STATE="$ROOT/.claude/team/state"
cfg() { python3 -c "import json,sys; r=json.load(open('$ROSTER')); print(r.get('models',{}).get(sys.argv[1], sys.argv[2]))" "$1" "$2"; }

if ! python3 -c "import json,sys; sys.exit(0 if sys.argv[1] in json.load(open(sys.argv[2]))['agents'] else 1)" "$AGENT" "$ROSTER"; then
  echo "usage: $0 <agent> [--fresh] [--model m] [claude flags]" >&2
  exit 1
fi

FRESH=0; MODEL="$(cfg agent_default sonnet)"
while [ $# -gt 0 ]; do
  case "$1" in
    --fresh) FRESH=1; shift ;;
    --model) MODEL="$2"; shift 2 ;;
    *) break ;;
  esac
done

BRANCH="agent/$AGENT"
WT="$ROOT/.claude/worktrees/$AGENT"
PERSONA="$ROOT/.claude/team/personas/$AGENT.md"
MARKER="$STATE/session-$AGENT"
MODE="${KRV_AGENT_PERMISSION_MODE:-default}"
mkdir -p "$STATE"

# A launch from inside another Claude session (e.g. the orchestrator running `team up`) must not leak that
# session's identity, messaging socket, or nesting markers.
unset CLAUDECODE CLAUDE_CODE_CHILD_SESSION CLAUDE_CODE_SESSION_ID CLAUDE_PID CLAUDE_EFFORT \
      CLAUDE_CODE_MESSAGING_SOCKET CLAUDE_CODE_MESSAGING_TOKEN CLAUDE_CODE_SESSION_ATTENDED \
      CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_EXECPATH

git -C "$ROOT" fetch origin --quiet || echo "warning: fetch failed; continuing with local refs" >&2

if [ ! -d "$WT" ]; then
  if git -C "$ROOT" show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git -C "$ROOT" worktree add "$WT" "$BRANCH"
  elif git -C "$ROOT" show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
    git -C "$ROOT" worktree add "$WT" -b "$BRANCH" "origin/$BRANCH"
  else
    git -C "$ROOT" worktree add "$WT" -b "$BRANCH" origin/main
  fi
fi

cd "$ROOT"
export KRV_AGENT="$AGENT"
COMMON=(--worktree "$AGENT" --append-system-prompt-file "$PERSONA" --permission-mode "$MODE"
        --model "$MODEL" --settings '{"autoMemoryEnabled": false, "crossSessionInbound": "accept"}')
echo "[$AGENT] model=$MODEL mode=$MODE $( [ $FRESH = 1 ] && echo fresh || echo resume-if-possible )"

# Run claude; if it exits within a few seconds with an error (e.g. nothing to resume), report failure so the caller can fall back.
try_launch() {
  local start rc; start=$(date +%s)
  set +e; claude "$@"; rc=$?; set -e
  if [ $rc -ne 0 ] && [ $(( $(date +%s) - start )) -lt 10 ]; then return 10; fi
  return $rc
}

if [ "$FRESH" = 0 ] && [ -f "$MARKER" ]; then
  try_launch "${COMMON[@]}" --resume "$AGENT" --name "$AGENT" "$@" && exit 0 || rc=$?
  [ "${rc:-0}" = 10 ] || exit "$rc"
  echo "[$AGENT] resume not possible; starting a fresh session" >&2
fi

date +%s > "$MARKER"
exec claude "${COMMON[@]}" --name "$AGENT" "$@"
