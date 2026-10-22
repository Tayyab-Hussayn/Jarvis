---
type: module
owner: qa
updated: 2026-10-22
---

# qa

## Code map
- `tests/conftest.py` (shared helpers/fixtures: `_agent_with`, `habits`, `fake_desktop`); legacy `test_linux_port.py` was split into `tests/engine/` and `tests/skills/` (#6) · `tools/eval_models.py`, `tools/check_layout.py` · `install-linux.sh`, `systemd/`, `requirements-linux.txt`

## Verify
```bash
.venv/bin/python -m pytest tests/engine tests/skills tests/test_web_transport.py -q   # 428 (add tests/web once it has a file)
bash -n install-linux.sh
```

## Gotchas
- Expected failures: `test_screen_processor.py`, `test_gesture_utils.py` (no cv2/mediapipe on 3.14), `test_kravein_connect*.py` (Windows tree).
