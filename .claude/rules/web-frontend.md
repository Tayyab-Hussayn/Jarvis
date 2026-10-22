---
paths:
  - "web/**"
---

# Web frontend conventions

- Next.js static export: `cd web && npm run build` writes `kravein/web-dist/`. Dev server: `npm run dev` on :3000 talking to :8787.
- Scroll container is `Screen`'s content wrapper (`min-h-0 flex-1 overflow-y-auto`); a flex child needs `min-h-0` to scroll at all.
- Voice and text render through one `TurnGraph`; client voice turn ids are `s…`, never `v…`.
- Verify layout with `tools/check_layout.py`; cache-bust (`?r=<time>`) in the Playwright profile.
