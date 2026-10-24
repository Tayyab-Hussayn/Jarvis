---
type: module
owner: skills
updated: 2026-10-23
---

# skills

## Code map
- `kravein/skills/*.py` one module per category (windows, files, browser, interact, settings, media, notify, schedule, routines, …) · `kravein/platform/` desktop (Hyprland/Sway/X11), inputs (ydotool), audio, browser (Playwright), shell (only subprocess spawner) · `gentle.py`
- `kravein/skills/phone.py` — category `phone`: `list_phones`, `phone_status` (SAFE), `phone_flashlight`, `phone_set_volume` (MODERATE), `phone_open_url`, `phone_launch_app` (DESTRUCTIVE, confirmed). Drives the engine's `kravein/phone.py` (`call`, `connected`, `PhoneError`s); never touches the socket.

- Window ids/cwd (#82): `list_windows(as_json=True)` returns `[{id,title,app,pid,workspace,focused,cwd}]`; the text form shows `(id …, cwd …)`. `focus_window`/`close_window` take `id=` (exact backend handle: Hyprland address / sway con_id / X11 id); a stale id is refused, never name-matched. Web should use `desktop.window_record(w)` (`platform/desktop.py`) for `/api/windows`. `cwd` comes from `/proc` via `terminal_cwd` (null for non-terminals and for server-mode terminals sharing one pid).

## Verify
```bash
.venv/bin/python -m compileall -q kravein/
.venv/bin/python -m pytest tests/skills -q   # 142: hyprland, window targeting, close workspace, ydotool keys, terminal typing, gentle scroll, site search, platform adapters, phone skills
```
Never run tests that act on the live desktop (close/kill/type); mock `platform/`. Phone tests mock `phone.call`/`connected`/`list_devices`.

## Gotchas
- Docstring = tool schema. `describe_targets` + `:implicit` markers. See `CLAUDE.md` for the Hyprland, ydotool and browser findings.
- Phone skills resolve the phone *before* `enforce()` (so the confirmation names it) and pass its `device_id` to `call()`. Targets: `phone:<id>`, `phone:<id>:<command>`, `phone:implicit` when unnamed. Args and capability are checked before asking.
- A phone timeout/disconnect after sending means "outcome unknown" — never report success; success text comes only from the phone's returned data.
- Desktop `set_volume`/`open_url` and the phone tools name each other in their docstrings; keep it that way.
