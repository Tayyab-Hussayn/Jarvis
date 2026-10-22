---
paths:
  - "kb/**"
---

# Knowledge base conventions

- `kb/` is an Obsidian-compatible vault: YAML frontmatter (`type`, `owner`, `updated`), `[[wikilinks]]` by file name, one topic per note.
- Keep notes short and factual; link to `CLAUDE.md`/audit docs instead of copying them.
- `kb/state/project-state.md` is imported into every session via CLAUDE.md: **keep it under 60 lines**, current, orchestrator-edited only. Move finished detail to `kb/lessons.md` or the module note.
- Module notes (`kb/modules/<agent>.md`) hold Code map, Public interfaces, **Verify** commands, Gotchas; the owner updates them in the same PR that changes the module.
