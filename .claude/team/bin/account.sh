# Sourced by start-agent.sh, start-lead.sh and team: choose which Claude account a launch uses.
#
# An account is a Claude Code config directory (CLAUDE_CONFIG_DIR). Values:
#   1 | main     the default account (~/.claude; CLAUDE_CONFIG_DIR unset)
#   2           the second account ($KRV_ACCOUNT2_DIR, default ~/.claude-account2)
#   /abs/path   any other config directory
#
# Precedence: an explicit --account value > .claude/team/runtime/account (machine-local, set with
# `team account <value>`) > the caller's environment unchanged. tmux windows inherit the tmux
# server's environment, not the caller's, so set the runtime file when agents must use one account.
#
# Safety: this file only chooses an environment variable for the process about to start. It never
# reads, writes, moves or deletes anything inside a config directory, and never runs a login or
# logout, so switching cannot sign either account out. A missing directory is refused, not created.

krv_account_file() { echo "$1/.claude/team/runtime/account"; }

# $1 = repo root, $2 = explicit value (may be empty). Exports/unsets CLAUDE_CONFIG_DIR.
krv_apply_account() {
  local root="$1" value="${2:-}" file
  file="$(krv_account_file "$root")"
  if [ -z "$value" ] && [ -s "$file" ]; then value="$(tr -d '[:space:]' < "$file")"; fi
  case "$value" in
    "") ;;
    1|main) unset CLAUDE_CONFIG_DIR ;;
    2) export CLAUDE_CONFIG_DIR="${KRV_ACCOUNT2_DIR:-$HOME/.claude-account2}" ;;
    /*) export CLAUDE_CONFIG_DIR="$value" ;;
    *) echo "unknown account '$value' (use 1, 2 or an absolute config directory)" >&2; return 1 ;;
  esac
  if [ -n "${CLAUDE_CONFIG_DIR:-}" ] && [ ! -d "$CLAUDE_CONFIG_DIR" ]; then
    echo "Claude config directory not found: $CLAUDE_CONFIG_DIR" >&2; return 1
  fi
}

krv_account_label() {
  if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then echo "$CLAUDE_CONFIG_DIR"; else echo "$HOME/.claude (account 1)"; fi
}
