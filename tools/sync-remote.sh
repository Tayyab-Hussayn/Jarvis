#!/usr/bin/env bash
# Push the git-tracked tree of this checkout to the second machine (niri).
#
#   tools/sync-remote.sh [host] [remote-dir]
#
# The remote has no GitHub credentials, so the tracked files travel over ssh.
# Defaults: host kravein-new, dir /home/kravein/Projects/desktop/kravein — the
# only place the owner allows writes on that machine. Nothing outside that
# directory is touched.
#
# Tracked files only (git ls-files), staged first so rsync --delete can drop
# files that no longer exist upstream without ever seeing the remote's own
# .venv, node_modules or web-dist (protected below, not deleted).
# android/, .claude/, .github/ and kb/ (team docs, phone app) are tracked but
# not needed on the laptop, and the owner wants no old project name there.

set -euo pipefail

HOST="${1:-kravein-new}"
DEST="${2:-/home/kravein/Projects/desktop/kravein}"
cd "$(git rev-parse --show-toplevel)"

case "$DEST" in
  /home/*/Projects/*/kravein|/home/*/Projects/*/kravein/) ;;
  *) [ "$HOST" = "-" ] && true || { echo "refusing: $DEST is not under ~/Projects/<group>/kravein" >&2; exit 2; } ;;
esac

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

git ls-files -z --exclude-standard \
  | grep -zvE '^(android/|\.claude/|\.github/|kb/)' \
  | rsync -a --from0 --files-from=- ./ "$STAGE/"

# One array element per filter: a missing space once glued two into one argument
# and silently left .build.log and __pycache__ unprotected.
PROTECT=(/.venv/ /web/node_modules/ /web/.next/ /kravein/web-dist/ /.pip-cache/
         /.npm-cache/ /.install.log /.build.log __pycache__/ .pytest_cache/)
FILTERS=()
for p in "${PROTECT[@]}"; do FILTERS+=("--filter=P $p"); done

# HOST "-" = local directory (used by the test); anything else is ssh.
if [ "$HOST" = "-" ]; then TARGET="$DEST/"; else TARGET="$HOST:$DEST/"; fi
rsync -a --delete --mkpath "${FILTERS[@]}" "$STAGE/" "$TARGET"
echo "synced $(git rev-parse --short HEAD) -> $HOST:$DEST"
