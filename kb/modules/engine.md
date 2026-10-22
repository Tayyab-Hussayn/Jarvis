---
type: module
owner: engine
updated: 2026-10-22
---

# engine

## Code map
- `agent.py` loop, cancel, tool routing hooks · `llm.py`/`cline.py` providers, failover, breaker · `registry.py` `@skill` + `dispatch` · `safety.py` enforce, denial memory · `audit.py` · `config.py` · `toolselect.py`
- `jobs.py`/`routines.py` scheduler (tick via `python -m kravein.jobs tick`) · `voice.py`/`live.py` · `whatsapp_service.py` · `cli.py`/`daemon.py`

## Verify
```bash
.venv/bin/python -m compileall -q kravein/
.venv/bin/python -m pytest tests/engine -q          # your new tests
.venv/bin/python -m pytest tests/test_linux_port.py -q   # legacy suite, must stay green (314)
```

## Gotchas
- Tests stub all three providers; breaker tests monkeypatch `config.STATE_DIR`. See `CLAUDE.md` "Testing conventions".
- Tool routing is pinned by `tests/engine/test_tool_routing.py` (default off, only `auto` enables, fail-open, `more_tools` edge cases, byte-stable tool list between rounds).
