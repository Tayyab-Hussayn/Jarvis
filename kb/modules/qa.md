---
type: module
owner: qa
updated: 2026-10-22
---

# qa

## Code map
- `tests/conftest.py` (shared helpers/fixtures: `_agent_with`, `habits`, `fake_desktop`); legacy `test_linux_port.py` was split into `tests/engine/` and `tests/skills/` (#6) · `tools/eval_models.py`, `tools/check_layout.py` · `install-linux.sh`, `systemd/`, `requirements-linux.txt`

- `systemd/kravein-web.service` — `serve --lan` user unit. `install-linux.sh` step 6d installs `~/.local/bin/kravein` (wrapper → `.venv/bin/python -m kravein`, idempotent, never overwrites a foreign file), writes `~/.config/kravein/web.env` (0600, `KRAVEIN_LAN_HOST` via `webauth.local_addresses()` minus docker/bridge/veth/virbr interfaces, keeps a still-valid address; the port is fixed at 8787, no override: `kravein start` / `kravein serve` refuse via `service.port_busy()` when anything listens on it) and the unit + `daemon-reload`, but **never enables** it (owner: manual start/stop via the CLI). Checked by `tools/test_systemd_units.py` (in `tools/` because new `tests/` files are orchestrator-owned).

- `install-linux.sh --check` — steps 1–2 only (compositor, Noctalia, native tools, pasteable `sudo pacman -S` line), writes nothing. The compositor is found from the env **or** from sockets in `$XDG_RUNTIME_DIR` (`niri.*.sock`, `noctalia-*.sock`), because ssh/systemd sessions have no `NIRI_SOCKET`. The package column of `CHECKS` must be one real package name (it is pasted into pacman).
- **Second machine (Arch + niri + Noctalia).** `ssh kravein-new`, remote login shell is fish → `ssh kravein-new 'bash -lc "..."'`. Only `/home/kravein/Projects/desktop/kravein` is writable (owner rule); no sudo, no installs, no autostart, niri/Noctalia config read-only. Sync = `tools/sync-remote.sh` (git-tracked files minus `android/ .claude/ .github/ kb/`, `--delete`, remote `.venv`/`node_modules`/`web-dist` protected; refuses any other destination). Run suite there: `ssh kravein-new 'bash -lc "cd /home/kravein/Projects/desktop/kravein && .venv/bin/python -m pytest tests/engine tests/skills tests/web tests/test_web_transport.py -q"'`.

## Verify
```bash
.venv/bin/python -m pytest tests/engine tests/skills tests/test_web_transport.py -q   # 428 (add tests/web once it has a file)
bash -n install-linux.sh
.venv/bin/python -m pytest tools/test_systemd_units.py -q   # 12 (units + installer + niri --check)
```

## Gotchas
- Expected failures: `test_screen_processor.py`, `test_gesture_utils.py` (no cv2/mediapipe on 3.14), `test_kravein_connect*.py` (Windows tree).
