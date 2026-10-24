---
type: module
owner: qa
updated: 2026-10-22
---

# qa

## Code map
- `tests/conftest.py` (shared helpers/fixtures: `_agent_with`, `habits`, `fake_desktop`); legacy `test_linux_port.py` was split into `tests/engine/` and `tests/skills/` (#6) · `tools/eval_models.py`, `tools/check_layout.py` · `install-linux.sh`, `systemd/`, `requirements-linux.txt`

- `systemd/kravein-web.service` — `serve --lan` user unit. `install-linux.sh` step 6d installs `~/.local/bin/kravein` (wrapper → `.venv/bin/python -m kravein`, idempotent, never overwrites a foreign file), writes `~/.config/kravein/web.env` (0600, `KRAVEIN_LAN_HOST` via `webauth.local_addresses()` minus docker/bridge/veth/virbr interfaces, keeps a still-valid address; `KRAVEIN_PORT`, default 8787, an existing value is kept; the unit passes `--port ${KRAVEIN_PORT}` and `kravein/service.py port()` reads the same file) and the unit + `daemon-reload`, but **never enables** it (owner: manual start/stop via the CLI). Checked by `tools/test_systemd_units.py` (in `tools/` because new `tests/` files are orchestrator-owned).

## Verify
```bash
.venv/bin/python -m pytest tests/engine tests/skills tests/test_web_transport.py -q   # 428 (add tests/web once it has a file)
bash -n install-linux.sh
.venv/bin/python -m pytest tools/test_systemd_units.py -q   # 6 (units + installer)
```

## Gotchas
- Expected failures: `test_screen_processor.py`, `test_gesture_utils.py` (no cv2/mediapipe on 3.14), `test_kravein_connect*.py` (Windows tree).
