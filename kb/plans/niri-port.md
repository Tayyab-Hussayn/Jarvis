---
type: plan
owner: orchestrator
updated: 2026-10-29
---

# Port to the second machine (Arch + niri + Noctalia)

Owner request 2026-10-29: make Kravein work on a second laptop running niri with the Noctalia shell.

## Rules (owner, 2026-10-29) — read before any ssh
- Access: `ssh kravein-new` (key auth, alias in ~/.ssh/config). Remote login shell is **fish**: run commands as `ssh kravein-new 'bash -lc "..."'` or `ssh kravein-new 'bash -s' <<'EOF' ... EOF`.
- **Work only inside `/home/kravein/Projects/desktop/kravein` on the remote.** Nothing else on that machine is written, moved or deleted.
- **niri config (`~/.config/niri/`) and Noctalia config are read-only** — read to audit, never edit.
- No `sudo`, no package installs, no systemd *system* units, no enabling autostart. Missing packages → list them in the PR for the owner.
- Never kill/restart niri, noctalia or the owner's apps. Live desktop tests only on windows **you** opened (e.g. a test `kitty` with a unique title) and close only those.
- Environment over ssh has no `WAYLAND_DISPLAY`/`NIRI_SOCKET`: discover the socket (`/run/user/$UID/niri.*.sock`, `wayland-*`) — the code must do this too.
- Audit facts (orchestrator, 2026-10-29): Arch, kernel 6.18 LTS, Python 3.14.8, niri 26.04 (JSON IPC works: `niri msg --json windows|workspaces`), Noctalia owns org.freedesktop.Notifications, PipeWire (pactl/wpctl), brightnessctl, playerctl, nmcli, bluetoothctl, kitty (no foot). **Missing:** ydotool, wtype, wl-clipboard, grim, slurp. No project dir yet.

## Tasks
- Owner 2026-10-29: project lives at `/home/kravein/Projects/desktop/kravein` on the new machine; Windows tree deleted and all Brahma names removed first (#124), then sync.
- #122 qa: provision `/home/kravein/Projects/desktop/kravein` on the remote, installer niri detection + missing-package hints.
- #123 skills: niri platform backend (windows/workspaces/ids/open_app), socket discovery, live check on the remote.
- Owner: `sudo pacman -S ydotool wtype wl-clipboard grim slurp` on the new machine.
