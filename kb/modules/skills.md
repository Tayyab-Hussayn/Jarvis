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

- File editing (#133, #136): `files.edit_file` (exact, unique replace; >=50% of the file counts as an overwrite = DESTRUCTIVE; confirmation shows line and -/+ preview; symlinks kept) and `files.search_text` (capped, skips secrets). `safety.sensitive_kind()` is shared: Kravein config/state/data are blocked for writes (workspace excepted), ssh/gpg/shell-startup/systemd-user/autostart need confirmation.
- Merged skills (#133): 169 -> 123 tools. One skill with an `action`/`view`/`topic` parameter replaces a family; the old names are registry aliases with a preset (`aliases={"pause_job": {"action": "pause"}}`, preset wins over the caller) and never reach the model. The old functions stay as **plain undecorated functions** in the same module (so they keep their `enforce` calls, targets and direct callers); the merged skill only dispatches (`skills/_merge.py`: `run()` passes only given args so the plain function's defaults apply, `unknown()`). Merged: `volume`, `brightness`, `list_devices`, `media_control`(status), `wifi_control`(list), `power_session`, `gentle_scroll`, `mouse`, `clipboard`, `screen_info`(topic=input), `reminder`, `notifications`, `spotify`, `wallpaper`, `memory`, `llm_control`, `routine_step`, `routine`, `routine_status`(history), `manage_job`, `list_jobs`(view), `browser_tab`, `browser_read`(status), `file_manager`, `list_windows`(view), `window_state`. Kept apart on purpose: `cancel_shutdown`, `list_processes`/`kill_process`, `browser_search_web`/`browser_search_site`, `delete_*`, `forget`, `install_package`, `control_service`. Rule: a merged skill is declared MODERATE (or CRITICAL for `power_session`, where every action enforces) and each state-changing action still calls `safety.enforce` itself; never declare DESTRUCTIVE+ for a skill with a read-only or non-enforcing action (the `skill.unenforced` tripwire). Tests: `tests/skills/test_merged_skills.py`.

## Verify
```bash
.venv/bin/python -m compileall -q kravein/
.venv/bin/python -m pytest tests/skills -q   # 154: hyprland, window targeting, close workspace, ydotool keys, terminal typing, gentle scroll, site search, platform adapters, phone skills
```
Never run tests that act on the live desktop (close/kill/type); mock `platform/`. Phone tests mock `phone.call`/`connected`/`list_devices`.

## Gotchas
- Docstring = tool schema. `describe_targets` + `:implicit` markers. See `CLAUDE.md` for the Hyprland, ydotool and browser findings.
- Phone skills resolve the phone *before* `enforce()` (so the confirmation names it) and pass its `device_id` to `call()`. Targets: `phone:<id>`, `phone:<id>:<command>`, `phone:implicit` when unnamed. Args and capability are checked before asking.
- A phone timeout/disconnect after sending means "outcome unknown" — never report success; success text comes only from the phone's returned data.
- Desktop `set_volume`/`open_url` and the phone tools name each other in their docstrings; keep it that way.
