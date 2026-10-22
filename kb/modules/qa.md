---
type: module
owner: qa
updated: 2026-10-22
---

# qa

## Code map
- `tests/conftest.py`, `tests/test_linux_port.py` (legacy, to be split per module) · `tools/eval_models.py`, `tools/check_layout.py` · `install-linux.sh`, `systemd/`, `requirements-linux.txt`

## Verify
```bash
.venv/bin/python -m pytest tests/test_linux_port.py tests/test_web_transport.py -q   # 314 + 87
bash -n install-linux.sh
```

## Gotchas
- Expected failures: `test_screen_processor.py`, `test_gesture_utils.py` (no cv2/mediapipe on 3.14), `test_kravein_connect*.py` (Windows tree).
