#!/usr/bin/env bash
# Start/stop the web server for a live pass with a throwaway state dir inside
# the project, so each check starts with an empty agent history and the owner's
# ~/.local/state/kravein is left alone.
#
#   tools/live_serve.sh LABEL   (re)start our own server with state .qa-state/LABEL
#   tools/live_serve.sh stop    stop the server this script started
#
# Only the pid recorded in .qa-state/live_serve.pid is ever signalled. A server
# the owner runs is never touched: if the port is held by anything else we
# refuse. (An earlier version used `pkill -f "kravein serve"`, which would have
# killed the owner's own server.)
set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || dirname "$(dirname "$(readlink -f "$0")")")"

PORT="${LIVE_SERVE_PORT:-8787}"   # override is for the test only; the server itself is fixed at 8787
PIDFILE=".qa-state/live_serve.pid"
mkdir -p .qa-state

listening() { ss -ltn | grep -q ":$PORT "; }

stop_ours() {
  [ -f "$PIDFILE" ] || return 0
  local pid; pid="$(cat "$PIDFILE")"
  # Guard against a recycled pid: it must still be a kravein serve process.
  if [ -n "$pid" ] && tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q "kravein serve"; then
    kill "$pid"
    for _ in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
  fi
  rm -f "$PIDFILE"
}

case "${1:?usage: live_serve.sh LABEL|stop}" in
  stop)
    stop_ours
    echo "stopped (own server only)"
    exit 0 ;;
esac

stop_ours
if listening; then
  echo "refusing: port $PORT is held by a server this script did not start" >&2
  exit 3
fi
export XDG_STATE_HOME="$PWD/.qa-state/$1"
mkdir -p "$XDG_STATE_HOME"
nohup .venv/bin/python -m kravein serve > .qa-serve.log 2>&1 &
echo $! > "$PIDFILE"
for _ in $(seq 1 20); do listening && break; sleep 1; done
ss -ltn | grep ":$PORT "
