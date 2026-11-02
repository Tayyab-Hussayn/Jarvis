---
paths:
  - "kravein/platform/**"
  - "kravein/skills/windows.py"
  - "kravein/skills/apps.py"
  - "kravein/skills/interact.py"
  - "install-linux.sh"
---

# niri (and Noctalia) — learn it before integrating

Owner rule 2026-11-02: the second laptop runs **niri 26.04 with the Noctalia shell**. That is *not* Hyprland and *not* Caelestia. Before writing or changing any integration that touches it, first understand how niri actually works. Never carry Hyprland settings, commands or assumptions over.

**Before any code:**
1. Read the niri docs for the installed version (context7 or the niri wiki: IPC, `niri msg`, the `--json` output, actions, workspaces, window rules) and check them against the real machine:
   - `ssh kravein-new 'niri msg --json windows'`
   - `ssh kravein-new 'niri msg --json workspaces'`
   - `ssh kravein-new 'niri msg --json focused-window'`
   - `ssh kravein-new 'niri msg action --help'`
2. Write down the model in the PR or the module note:
   - niri is a **scrollable-tiling** compositor. Windows sit in columns on dynamic, per-output workspaces.
   - Workspace ids are not fixed indices like Hyprland's.
   - There is no `hyprctl` and no Lua dispatcher API.
   - Actions go through `niri msg action …`. Each one acts on a target id or on the focused window; check which, for each action.
3. If the change touches the shell UI (panels, notifications, the launcher), learn how Noctalia does it first. Never assume Caelestia's behaviour.

**Hard limits on that machine:**
- niri and Noctalia **config is read-only**: never edit `~/.config/niri/`, the Noctalia config, or any shell/compositor setting. Kravein adapts to the desktop, never the other way round.
- Only `/home/kravein/Projects/desktop/kravein` is writable.
- No sudo, no package installs.
- Never kill or restart niri, Noctalia or the owner's apps.
- Live tests only on windows the agent opened itself.

**Same hard-won rules as on Hyprland** (`hard-won-desktop.md`):
- verify the effect, not the acknowledgement (`niri msg action` exiting 0 proves nothing);
- never take the first of several matches;
- confirmations name the window and workspace;
- put focus back after acting.

**ydotool needs its daemon.** The owner installed ydotool on 2026-11-02, but `ydotool.service` (user) is inactive. Synthetic input does nothing until `ydotoold` runs, and ydotool still exits 0. Detect this and report it to the owner; do not enable services yourself.
