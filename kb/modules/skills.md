---
type: module
owner: skills
updated: 2026-10-24
---

# skills

## Code map
- `kravein/skills/*.py` one module per category (windows, files, browser, interact, settings, media, notify, schedule, routines, …) · `kravein/platform/` desktop (Hyprland/Sway/X11), inputs (ydotool), audio, browser (Playwright), shell (only subprocess spawner) · `gentle.py`
- `kravein/skills/phone.py` — category `phone`: `list_phones`, `phone_status` (SAFE), `phone_flashlight`, `phone_set_volume` (MODERATE), `phone_open_url`, `phone_launch_app` (DESTRUCTIVE, confirmed). Drives the engine's `kravein/phone.py` (`call`, `connected`, `PhoneError`s); never touches the socket.

- Window ids/cwd (#82): `list_windows(as_json=True)` returns `[{id,title,app,pid,workspace,focused,cwd}]`; the text form shows `(id …, cwd …)`. `focus_window`/`close_window` take `id=` (exact backend handle: Hyprland address / sway con_id / X11 id); a stale id is refused, never name-matched. Web should use `desktop.window_record(w)` (`platform/desktop.py`) for `/api/windows`. `cwd` comes from `/proc` via `terminal_cwd` (null for non-terminals and for server-mode terminals sharing one pid).

- Apps on a workspace (#93): `open_app(app_name, workspace="")`; with a workspace it starts a **new** instance there via `backend.spawn_on_workspace` (Hyprland `exec_cmd(cmd, {workspace="N silent"})` / legacy `exec [workspace N silent] …`, verified live on 0.56.2; sway/X11 refuse with "not supported on this desktop"). Workspace must be a positive number or an existing workspace name; ambiguous app names list candidates; the result reports where the new window actually landed (forking launchers like browsers may ignore the rule). Structured list for the web panel: `kravein.platform.apps.list_entries(category="", limit=0)` -> `[{name, id, icon, exec}]` (visible entries, sorted by name); `list_apps` text unchanged.

- niri (#123): `desktop.NiriBackend` over `niri msg --json` (ids = niri window id; workspace label = name or `idx`, `idx@output` on non-focused outputs, not targetable by number). Actions take `--id`/`--window-id`, so no focus-first dance, but every change is re-read before returning True (niri clamps bad workspace indexes and still says Handled). `geometry` is None (IPC has no rect for tiled windows) so gentle scroll refuses; no kill (falls back to close); `move_cursor` unsupported. `open_app(..., workspace)`: no launch rule on niri, so `spawn_on_workspace` spawns, finds the new window id, moves it `--focus false`, refocuses the previous window (brief flash on the current workspace). `detect.find_niri_socket()` finds `/run/user/UID/niri.*.sock` (skips dead pids) and `_adopt_niri_session()` exports `NIRI_SOCKET`/`WAYLAND_DISPLAY`/`XDG_RUNTIME_DIR` so ssh/service runs work; session is then `wayland`. Screenshots fall back to `niri msg action screenshot-screen|screenshot-window --path` when grim is missing; region/named output refuse naming grim+slurp. Fixtures: `tests/skills/fixtures/niri/` (recorded from 26.04). Still missing on the remote (owner): ydotool, wtype, wl-clipboard, grim, slurp.

## Verify
```bash
.venv/bin/python -m compileall -q kravein/
.venv/bin/python -m pytest tests/skills -q   # 174: niri, hyprland, window targeting, close workspace, ydotool keys, terminal typing, gentle scroll, site search, platform adapters, phone skills
```
Never run tests that act on the live desktop (close/kill/type); mock `platform/`. Phone tests mock `phone.call`/`connected`/`list_devices`.

## Gotchas
- Docstring = tool schema. `describe_targets` + `:implicit` markers. See `CLAUDE.md` for the Hyprland, ydotool and browser findings.
- Phone skills resolve the phone *before* `enforce()` (so the confirmation names it) and pass its `device_id` to `call()`. Targets: `phone:<id>`, `phone:<id>:<command>`, `phone:implicit` when unnamed. Args and capability are checked before asking.
- A phone timeout/disconnect after sending means "outcome unknown" — never report success; success text comes only from the phone's returned data.
- Desktop `set_volume`/`open_url` and the phone tools name each other in their docstrings; keep it that way.
