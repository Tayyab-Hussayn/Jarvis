#!/usr/bin/env bash
# Kravein — Linux installer.
#
# Sets up the virtualenv, checks for the native tools the assistant drives,
# and optionally installs the systemd user service.
#
# Safe to re-run; it only reports what is missing rather than installing
# system packages behind your back.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO_DIR/.venv"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/kravein"

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------------------
bold "1. Checking the base system"

if [[ ! -f /etc/arch-release ]]; then
  warn "This installer targets Arch Linux. Other distributions will work, but"
  warn "the package names below will differ."
fi

if ! command -v python3 >/dev/null; then
  fail "python3 is not installed."
  exit 1
fi
PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
ok "python $PYVER"

# ---------------------------------------------------------------------------
bold "2. Native tools the assistant drives"

# capability|binaries (first found wins)|arch package
CHECKS=(
  "window control|hyprctl swaymsg wmctrl|hyprland or wmctrl"
  "input synthesis|ydotool wtype xdotool|ydotool"
  "screen capture|grim grimblast maim scrot|grim"
  "region select|slurp|slurp"
  "clipboard|wl-copy xclip xsel|wl-clipboard"
  "volume|wpctl pactl amixer|wireplumber"
  "brightness|brightnessctl light|brightnessctl"
  "media keys|playerctl|playerctl"
  "notifications|notify-send dunstify|libnotify"
  "notification watching|dbus-monitor|dbus"
  "network|nmcli|networkmanager"
  "audio playback|mpv ffplay paplay|mpv"
  "update checking|checkupdates|pacman-contrib"
  "file opening|xdg-open gio|xdg-utils"
)

MISSING_PKGS=()
for entry in "${CHECKS[@]}"; do
  IFS='|' read -r capability binaries package <<< "$entry"
  found=""
  for binary in $binaries; do
    if command -v "$binary" >/dev/null 2>&1; then found="$binary"; break; fi
  done
  if [[ -n "$found" ]]; then
    ok "$(printf '%-22s %s' "$capability" "$found")"
  else
    warn "$(printf '%-22s missing -> pacman -S %s' "$capability" "$package")"
    MISSING_PKGS+=("$package")
  fi
done

if (( ${#MISSING_PKGS[@]} )); then
  echo
  warn "Install the missing pieces with:"
  echo "    sudo pacman -S --needed ${MISSING_PKGS[*]}"
fi

# ---------------------------------------------------------------------------
bold "3. Python environment"

if [[ ! -d "$VENV" ]]; then
  python3 -m venv "$VENV"
  ok "created $VENV"
else
  ok "reusing $VENV"
fi

"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$REPO_DIR/requirements-linux.txt"
ok "dependencies installed"

# ---------------------------------------------------------------------------
bold "4. Configuration"

mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"

if [[ ! -f "$CONFIG_DIR/api_keys.json" ]]; then
  cat > "$CONFIG_DIR/api_keys.json" <<'JSON'
{
  "gemini_api_key": "",
  "openrouter_api_key": ""
}
JSON
  chmod 600 "$CONFIG_DIR/api_keys.json"
else
  ok "api_keys.json present"
fi

# The default backend is the Cline CLI, which needs no key of ours.
if command -v cline >/dev/null 2>&1; then
  if [[ -f "$HOME/.cline/data/settings/providers.json" ]]; then
    ok "cline CLI authenticated — no API key needed"
  else
    warn "cline CLI found but not authenticated. Run:  cline auth"
  fi
else
  warn "cline CLI not installed. Either install it, or add a Gemini/OpenRouter"
  warn "key to $CONFIG_DIR/api_keys.json"
fi

if [[ ! -f "$CONFIG_DIR/env" ]]; then
  cat > "$CONFIG_DIR/env" <<'ENVFILE'
# Environment for the systemd user service. Keys set here override
# api_keys.json and never appear in the unit file or the journal.
# KRAVEIN_GEMINI_API_KEY=
# KRAVEIN_OPENROUTER_API_KEY=
ENVFILE
  chmod 600 "$CONFIG_DIR/env"
fi

# ---------------------------------------------------------------------------
bold "5. Input synthesis daemon (ydotoold)"

if command -v ydotool >/dev/null 2>&1; then
  SOCKET="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/.ydotool_socket"
  if [[ -S "$SOCKET" ]] || [[ -S /tmp/.ydotool_socket ]]; then
    ok "ydotoold is running"
  else
    warn "ydotool is installed but its daemon is not running."
    warn "Typing and clicking will not work until you start it:"
    if systemctl --user list-unit-files 2>/dev/null | grep -q '^ydotool\.service'; then
      echo "    systemctl --user enable --now ydotool.service"
    else
      echo "    sudo systemctl enable --now ydotoold.service"
    fi
    warn "If it fails with a permission error on /dev/uinput, add yourself"
    warn "to the input group:  sudo usermod -aG input \$USER  (then re-login)"
  fi
else
  warn "ydotool not installed — typing and clicking will be unavailable."
fi

# ---------------------------------------------------------------------------
bold "6. systemd user service (optional)"

UNIT_SRC="$REPO_DIR/systemd/kravein.service"
UNIT_DST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/kravein.service"

read -r -p "  Install the background service? [y/N] " reply
if [[ "${reply,,}" == "y" ]]; then
  mkdir -p "$(dirname "$UNIT_DST")"
  # Rewrite the paths so the unit works from wherever the repo actually is.
  sed "s|%h/code/Kravein|$REPO_DIR|g" "$UNIT_SRC" > "$UNIT_DST"
  systemctl --user daemon-reload
  ok "installed $UNIT_DST"
  echo "    Start it with:  systemctl --user enable --now kravein"
  echo "    Watch logs:     journalctl --user -u kravein -f"
else
  ok "skipped (run it in a terminal instead)"
fi

# ---------------------------------------------------------------------------
bold "6b. scheduled jobs timer"

# This one is not optional in the same way. Reminders and scheduled jobs are
# only reliable because systemd wakes the tick — there is no always-on Kravein
# process to do it. Without the timer, `schedule_job` and `set_reminder` write
# a job that nothing ever fires.
JOBS_UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$JOBS_UNITS"
for unit in kravein-jobs.service kravein-jobs.timer; do
  sed "s|%h/code/Kravein|$REPO_DIR|g" "$REPO_DIR/systemd/$unit" > "$JOBS_UNITS/$unit"
done
systemctl --user daemon-reload

if systemctl --user enable --now kravein-jobs.timer >/dev/null 2>&1; then
  ok "scheduled-jobs timer installed and running (ticks every minute)"
  echo "    Next tick:  systemctl --user list-timers kravein-jobs.timer"
  echo "    Run log:    ${XDG_STATE_HOME:-$HOME/.local/state}/kravein/job-runs.jsonl"
else
  echo "    Could not enable the timer. Reminders will not fire until you run:"
  echo "      systemctl --user enable --now kravein-jobs.timer"
fi

# ---------------------------------------------------------------------------
bold "7. web UI (optional)"

# The UI is a static export, so Node is a build-time dependency only — nothing
# runs it in production. Skip cleanly when Node is absent rather than failing
# the whole install over an optional frontend.
if command -v npm >/dev/null 2>&1; then
  read -r -p "  Build the web UI? (needs npm, takes a few minutes) [y/N] " reply
  if [[ "${reply,,}" == "y" ]]; then
    (cd "$REPO_DIR/web" && npm install --no-fund --no-audit && npm run build) \
      && ok "built $REPO_DIR/kravein/web-dist" \
      || echo "    Build failed — 'kravein serve' will still serve the API."
  else
    ok "skipped (build later with: cd web && npm install && npm run build)"
  fi
else
  ok "npm not found — skipping the web UI (everything else works)"
fi

# ---------------------------------------------------------------------------
echo
bold "Done."
echo
echo "  Interactive:  $VENV/bin/python -m kravein"
echo "  One-shot:     $VENV/bin/python -m kravein ask 'what is using my disk?'"
echo "  Web UI:       $VENV/bin/python -m kravein serve"
echo "  Check setup:  $VENV/bin/python -m kravein doctor"
echo "  Skills:       $VENV/bin/python -m kravein skills"
echo
