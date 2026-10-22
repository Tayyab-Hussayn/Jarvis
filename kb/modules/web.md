---
type: module
owner: web
updated: 2026-10-22
---

# web

## Code map
- `web/` Next.js app (screens, `TurnGraph`, `Screen`) → `npm run build` → `kravein/web-dist/` · `kravein/server.py` FastAPI + ws · `events.py` Session frames · `webauth.py` token/Origin/Host

## Verify
```bash
.venv/bin/python -m pytest tests/test_web_transport.py -q     # 87
cd web && npm run build
.venv/bin/python tools/check_layout.py     # only when the UI changed
```

## Gotchas
- No `from __future__ import annotations` in `server.py`. Every request gets a reply frame. Confirmation frames are never dropped.
