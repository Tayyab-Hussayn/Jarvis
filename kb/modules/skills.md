---
type: module
owner: skills
updated: 2026-10-22
---

# skills

## Code map
- `kravein/skills/*.py` one module per category (windows, files, browser, interact, settings, media, notify, schedule, routines, …) · `kravein/platform/` desktop (Hyprland/Sway/X11), inputs (ydotool), audio, browser (Playwright), shell (only subprocess spawner) · `gentle.py`

## Verify
```bash
.venv/bin/python -m compileall -q kravein/
.venv/bin/python -m pytest tests/skills -q
.venv/bin/python -m pytest tests/test_linux_port.py -q
```
Never run tests that act on the live desktop (close/kill/type); mock `platform/`.

## Gotchas
- Docstring = tool schema. `describe_targets` + `:implicit` markers. See `CLAUDE.md` for the Hyprland, ydotool and browser findings.
