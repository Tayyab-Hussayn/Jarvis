#!/usr/bin/env bash
# Restart the web server for a live pass with a throwaway state dir inside the
# project, so each check starts with an empty agent history and the owner's
# ~/.local/state/kravein is left alone.  Usage (on the laptop): tools/live_serve.sh LABEL
set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || dirname "$(dirname "$(readlink -f "$0")")")"
pkill -f "[k]ravein serve" || true
sleep 1
export XDG_STATE_HOME="$PWD/.qa-state/${1:?label}"
mkdir -p "$XDG_STATE_HOME"
nohup .venv/bin/python -m kravein serve > .qa-serve.log 2>&1 &
for _ in $(seq 1 20); do ss -ltn | grep -q ':8787 ' && break; sleep 1; done
ss -ltn | grep ':8787 '
