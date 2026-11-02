---
type: module
owner: qa
updated: 2026-11-02
---

# qa

## Code map
- `tests/conftest.py` (shared helpers/fixtures: `_agent_with`, `habits`, `fake_desktop`); legacy `test_linux_port.py` was split into `tests/engine/` and `tests/skills/` (#6) · `tools/eval_models.py`, `tools/check_layout.py` · `install-linux.sh`, `systemd/`, `requirements-linux.txt`

- `systemd/kravein-web.service` — `serve --lan` user unit. `install-linux.sh` step 6d installs `~/.local/bin/kravein` (wrapper → `.venv/bin/python -m kravein`, idempotent, never overwrites a foreign file), writes `~/.config/kravein/web.env` (0600, `KRAVEIN_LAN_HOST` via `webauth.local_addresses()` minus docker/bridge/veth/virbr interfaces, keeps a still-valid address; the port is fixed at 8787, no override: `kravein start` / `kravein serve` refuse via `service.port_busy()` when anything listens on it) and the unit + `daemon-reload`, but **never enables** it (owner: manual start/stop via the CLI). Checked by `tools/test_systemd_units.py` (in `tools/` because new `tests/` files are orchestrator-owned).

- `install-linux.sh --check` — steps 1–2 only (compositor, Noctalia, native tools, pasteable `sudo pacman -S` line), writes nothing. The compositor is found from the env **or** from sockets in `$XDG_RUNTIME_DIR` (`niri.*.sock`, `noctalia-*.sock`), because ssh/systemd sessions have no `NIRI_SOCKET`. The package column of `CHECKS` must be one real package name (it is pasted into pacman).
- **Second machine (Arch + niri + Noctalia).** `ssh kravein-new`, remote login shell is fish → `ssh kravein-new 'bash -lc "..."'`. Only `/home/kravein/Projects/desktop/kravein` is writable (owner rule); no sudo, no installs, no autostart, niri/Noctalia config read-only. Sync = `tools/sync-remote.sh` (git-tracked files minus `android/ .claude/ .github/ kb/`, `--delete`, remote `.venv`/`node_modules`/`web-dist` protected; refuses any other destination). Run suite there: `ssh kravein-new 'bash -lc "cd /home/kravein/Projects/desktop/kravein && .venv/bin/python -m pytest tests/engine tests/skills tests/web tests/test_web_transport.py -q"'`. Status 2026-11-02 (new IP, same `kravein-new`): synced at main 7111de2; venv install (`PIP_CACHE_DIR=$PWD/.pip-cache`, detached) OK; full `pytest tests -q` = **1193 passed**; web built there with `npm ci --cache ../.npm-cache && npm run build` (node 20.18, ~2 min, detached). `--check` hints missing: ydotool grim slurp wl-clipboard pacman-contrib wtype (owner installs).

- **Live UI pass (#153, 2026-11-02).** `tools/live_ui.py` (headless system Chrome via the venv's Playwright; `shot`, `ask "text" PREFIX [approve|deny]`, `pane NAME OUT.png [WIDTH] [BUTTON]`; run with `PYTHONPATH=.`; token read from the runtime file, never printed) and `tools/live_serve.sh LABEL|stop` (restart only the server it started, pid in `.qa-state/live_serve.pid`, refuses if 8787 is held by anything else, never `pkill`; starts `kravein serve` with `XDG_STATE_HOME=<project>/.qa-state/LABEL`, so each check gets an empty agent history and the owner's state dir is untouched). `sync-remote.sh` protects `.qa-*`; before that fix its `--delete` wiped my scratch files. Report and screenshots: `kb/qa-shots/153/`. Gotcha: `get_by_role("button", name="Windows")` substring-matches the quick prompt "List open windows" — use `exact=True`.

- **Release pass on the niri laptop (#172, 2026-11-02, main 7561d10).** Deploy = `tools/sync-remote.sh`, then detached `pip install -r requirements-linux.txt` (edge-tts 7.3.1) + `npm ci && npm run build`; `install-linux.sh --check` all native tools present. Laptop suite: `pytest tests tools -q` = 1332 passed, 2 failed (`tools/test_sync_remote.py`: the laptop has no `.git`; now skipped there). Screenshots stay on the laptop in `.qa-shots/172/` (copies in the qa worktree `kb/qa-shots/172/`, untracked). Gotchas: laptop mic has no input source, so `kravein voice test` stops at stage 1 (call `tts.speak` directly to test TTS); Gemini free tier = 15 requests/min, so Orchestrator polling `task_status` or parallel delegates hit 429, and the failover chain behind it is dead there (Zen 402, NovaRouter DNS); `gemini-3.5-flash-lite` sometimes answers in >20 s, over the provider deadline; the full installer (writes `~/.local/bin`, `~/.config/systemd/user`) was not run because only the project dir is writable, so the `kravein` CLI is not installed there yet. Scratch scripts for the live pass lived in `.qa-scratch/` (cancel/Stop, Live text drive with `NetworkAudio`, settings round trip with backup + restore).

## Verify
```bash
.venv/bin/python -m pytest tests/engine tests/skills tests/test_web_transport.py -q   # 428 (add tests/web once it has a file)
bash -n install-linux.sh
.venv/bin/python -m pytest tools/test_systemd_units.py -q   # 12 (units + installer + niri --check)
.venv/bin/python -m pytest tools/test_eval_models.py -q   # 7 (scenario parsing/scoring, stub provider)
```

Agent evals (live, one call per scenario, nothing executed — first tool choice only):
`.venv/bin/python tools/eval_models.py --scenarios tools/eval/agents gemini:gemini-3.5-flash-lite`.
Calls that error after retries are listed as NOT RUN and left out of the denominator.

## Gotchas
- Expected failures: `test_screen_processor.py`, `test_gesture_utils.py` (no cv2/mediapipe on 3.14), `test_kravein_connect*.py` (Windows tree).
