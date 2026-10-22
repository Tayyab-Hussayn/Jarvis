#!/usr/bin/env bash
# Start the orchestrator in its own terminal (not in tmux) — always the SAME long-lived session.
#   .claude/team/bin/start-lead.sh [--fresh] [extra claude flags]
#
# Which session it resumes, in order:
#   1. the session ID in .claude/team/runtime/orchestrator-session (machine-local, gitignored)
#   2. otherwise the session named "orchestrator"
#   3. otherwise (or with --fresh) a new session, whose ID is then recorded in (1)
#
# Defaults that match how the owner runs the orchestrator:
#   - normal permission mode (the lead is never started with permission checks bypassed)
#   - session name "orchestrator" (agents address their messages to this name)
#   - accept messages from local agent sessions without an approval dialog (crossSessionInbound=accept)
#   - model per roster "models.orchestrator" unless --model is given
set -euo pipefail
ROOT="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"   # main checkout
RUNTIME="$ROOT/.claude/team/runtime"
ID_FILE="$RUNTIME/orchestrator-session"
mkdir -p "$RUNTIME"

FRESH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --fresh) FRESH=1; shift ;;
    *) break ;;
  esac
done

cd "$ROOT"
export KRV_AGENT="orchestrator"
ARGS=(--append-system-prompt-file "$ROOT/.claude/team/personas/orchestrator.md"
      --settings '{"crossSessionInbound": "accept"}'
      --name orchestrator)
case " $* " in *" --model "*) ;; *)
  ARGS+=(--model "$(python3 -c "import json;print(json.load(open('$ROOT/.claude/team/roster.json')).get('models',{}).get('orchestrator','opus'))")") ;;
esac

try_resume() {   # $1 = session id or name; fails fast (<10 s) when there is nothing to resume
  # Shift the id off first: passing "$@" unshifted handed the id to claude a second time, as a prompt.
  local id="$1" start rc; shift; start=$(date +%s)
  set +e; claude "${ARGS[@]}" --resume "$id" "$@"; rc=$?; set -e
  if [ $rc -ne 0 ] && [ $(( $(date +%s) - start )) -lt 10 ]; then return 10; fi
  exit $rc
}

if [ "$FRESH" = 0 ]; then
  if [ -s "$ID_FILE" ]; then try_resume "$(tr -d '[:space:]' < "$ID_FILE")" "$@" || true; fi
  try_resume orchestrator "$@" || true
  echo "no previous orchestrator session found; starting a fresh one" >&2
fi

# Fresh session: pin its ID so every later start-lead.sh resumes exactly this one.
NEW_ID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
echo "$NEW_ID" > "$ID_FILE"
exec claude "${ARGS[@]}" --session-id "$NEW_ID" "$@"
