---
type: module
owner: engine
updated: 2026-10-23
---

# engine

## Code map
- `agent.py` loop, cancel, tool routing hooks · `llm.py`/`cline.py` providers, failover, breaker · `registry.py` `@skill` + `dispatch` · `safety.py` enforce, denial memory · `audit.py` · `config.py` · `toolselect.py`
- `jobs.py`/`routines.py` scheduler (tick via `python -m kravein.jobs tick`) · `voice.py`/`live.py` · `whatsapp_service.py` · `cli.py`/`daemon.py`

## Verify
```bash
.venv/bin/python -m compileall -q kravein/
.venv/bin/python -m pytest tests/engine -q          # 248: safety, registry, llm, agent loop, cline, novarouter, voice, jobs, routines, whatsapp, audit, routing
```

## Public interfaces
- Confirmation policy (`safety.py`, #35): `confirmation_policy() -> {"mode": "ask"|"decline"|"auto", "seconds_left", "default_seconds", "max_seconds"}`; `set_confirmation_mode(mode, seconds=None, *, by="")` (`auto` in memory only, default 1 h, capped 4 h, raises `ValueError` on bad input; `ask`/`decline` persist as `safety.confirmation_mode`); `disable_auto_approve(*, by="")`. `auto` covers DESTRUCTIVE from `safety.AUTO_SOURCES` (web, web-panel, voice, voice-live, cli, cli-ask) only, with a real confirmer; CRITICAL, hard refusals and the denial memory are unchanged. `allow_destructive: false` = `decline`. Audit: `safety.auto_approved`, `safety.mode_changed`, `safety.auto_expired`. `check_path` refuses writes to `settings.json`. Skills must never call the setters (pinned by `tests/engine/test_confirmation_policy.py`).

## Gotchas
- Tests stub all three providers; breaker tests monkeypatch `config.STATE_DIR`. See `CLAUDE.md` "Testing conventions".
- Tool routing is pinned by `tests/engine/test_tool_routing.py` (default off, only `auto` enables, fail-open, `more_tools` edge cases, byte-stable tool list between rounds).
- `serve --lan` address choice is `cli._pick_lan_host`: explicit `--host` must be a held private address; several candidates and no `--host` → refuse and list (never `addresses[0]`). Pinned by `tests/engine/test_serve_lan_host.py`.
- `classify_command` hard-blocks shell writes to Kravein's `settings.json` (redirects, sed -i, cp/mv/tee/dd/ln, editors, interpreters); reads stay allowed. Text matching only: a path assembled at run time is not caught, and a bare `settings.json` counts as ours (cwd unknown). Pinned in `tests/engine/test_confirmation_policy.py` (#40). Globs matching the file and any write-class command naming its directory (`cp -r x dir/`, `cd dir && sed -i *`) are blocked too; bare globs without a directory part and the parent `~/.config` are not.
