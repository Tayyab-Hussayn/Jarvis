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
# - Permission mode defaults to `auto`: a classifier approves routine commands and blocks risky ones, so an
#   unattended agent does not stall on a prompt nobody is watching (acceptEdits stalled on every read-only
#   compound and the orchestrator had to babysit panes, burning tokens). The ownership/frozen/git hooks still
#   run on every call. Set KRV_AGENT_PERMISSION_MODE=acceptEdits or default to be stricter. Never `bypassPermissions`.
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
  echo "usage: $0 <agent> [--fresh] [--model m] [--account 1|2|dir] [claude flags]" >&2
  exit 1
fi

FRESH=0; MODEL="$(cfg agent_default sonnet)"; ACCOUNT=""; TASK=""
while [ $# -gt 0 ]; do
  case "$1" in
    --fresh) FRESH=1; shift ;;
    --model) MODEL="$2"; shift 2 ;;
    --account) ACCOUNT="${2:?--account needs a value}"; shift 2 ;;
    --task) TASK="${2:?--task needs an issue number}"; shift 2 ;;
    *) break ;;
  esac
done

BRANCH="agent/$AGENT"
WT="$ROOT/.claude/worktrees/$AGENT"
PERSONA="$ROOT/.claude/team/personas/$AGENT.md"
MARKER="$STATE/session-$AGENT"
MODE="${KRV_AGENT_PERMISSION_MODE:-auto}"
mkdir -p "$STATE"

# A launch from inside another Claude session (e.g. the orchestrator running `team up`) must not leak that
# session's identity, messaging socket, or nesting markers.
unset CLAUDECODE CLAUDE_CODE_CHILD_SESSION CLAUDE_CODE_SESSION_ID CLAUDE_PID CLAUDE_EFFORT \
      CLAUDE_CODE_MESSAGING_SOCKET CLAUDE_CODE_MESSAGING_TOKEN CLAUDE_CODE_SESSION_ATTENDED \
      CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_EXECPATH

# Claude account (CLAUDE_CONFIG_DIR): --account, else runtime/account (`team account`), else the environment.
# shellcheck source=account.sh
. "$(dirname "$(readlink -f "$0")")/account.sh"
krv_apply_account "$ROOT" "$ACCOUNT"

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

# A worktree has no untracked, gitignored dependencies. Without these links every `.venv/bin/python` in a Verify
# section fails inside an agent. Symlinks, not copies: one environment, nothing duplicated.
[ -e "$WT/.venv" ] || { [ -d "$ROOT/.venv" ] && ln -s "$ROOT/.venv" "$WT/.venv"; }
# web gets its own node_modules, not a link: Turbopack refuses a node_modules that resolves outside the
# project root, so `npm run build` failed in the web worktree (PR #20). `npm ci --prefer-offline` installs
# from the lockfile out of the shared ~/.npm cache; an older symlink is replaced (unlink touches only the link).
if [ "$AGENT" = "web" ] && [ -f "$WT/web/package-lock.json" ]; then
  [ -L "$WT/web/node_modules" ] && unlink "$WT/web/node_modules"
  if [ ! -d "$WT/web/node_modules" ]; then
    ( cd "$WT/web" && npm ci --prefer-offline --no-audit --no-fund --loglevel=error ) \
      || echo "warning: npm ci failed in $WT/web; run it there by hand" >&2
  fi
fi

# The Android build reads the SDK from local.properties or ANDROID_HOME. This machine exports
# ANDROID_HOME/ANDROID_SDK_ROOT as /opt/android-sdk, which holds only sdkmanager, so a fresh worktree
# could not build until someone wrote local.properties by hand. Point both at the shared SDK.
if [ "$AGENT" = "android" ] && [ -d "$HOME/Android/Sdk" ]; then
  export ANDROID_HOME="$HOME/Android/Sdk" ANDROID_SDK_ROOT="$HOME/Android/Sdk"
  if [ -d "$WT/android" ] && [ ! -e "$WT/android/local.properties" ]; then
    echo "sdk.dir=$HOME/Android/Sdk" > "$WT/android/local.properties"
  fi
fi

cd "$ROOT"
export KRV_AGENT="$AGENT"
COMMON=(--worktree "$AGENT" --append-system-prompt-file "$PERSONA" --permission-mode "$MODE"
        --model "$MODEL" --settings '{"autoMemoryEnabled": false, "crossSessionInbound": "accept"}')
echo "[$AGENT] account=$(krv_account_label) model=$MODEL mode=$MODE $( [ $FRESH = 1 ] && echo fresh || echo resume-if-possible )"

# Run claude; if it exits within a few seconds with an error (e.g. nothing to resume), report failure so the caller can fall back.
try_launch() {
  local start rc; start=$(date +%s)
  set +e; claude "$@"; rc=$?; set -e
  if [ $rc -ne 0 ] && [ $(( $(date +%s) - start )) -lt 10 ]; then return 10; fi
  return $rc
}

# --task N starts the agent already holding its assignment. A SendMessage sent right after `team up`
# found no session yet (it was still starting, e.g. during npm ci) and the orchestrator had to wait.
if [ -n "$TASK" ]; then
  case "$TASK" in *[!0-9]*) echo "--task needs an issue number" >&2; exit 1 ;; esac
  set -- "$@" "New task #$TASK assigned by the orchestrator. Run /sync-after-merge if your branch is behind origin/main, then /task-start for #$TASK. SendMessage the orchestrator when the PR is up, when blocked, or after 3 failed attempts."
fi

if [ "$FRESH" = 0 ] && [ -f "$MARKER" ]; then
  try_launch "${COMMON[@]}" --resume "$AGENT" --name "$AGENT" "$@" && exit 0 || rc=$?
  [ "${rc:-0}" = 10 ] || exit "$rc"
  echo "[$AGENT] resume not possible; starting a fresh session" >&2
fi

date +%s > "$MARKER"
exec claude "${COMMON[@]}" --name "$AGENT" "$@"
