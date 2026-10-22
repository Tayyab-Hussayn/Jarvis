---
type: state
owner: orchestrator
updated: 2026-10-22
---

# Project state (orchestrator-edited; keep under 60 lines)

## Done
- Linux port `kravein/`: 161 skills, web UI (`kravein serve`), voice (local + Gemini Live), jobs/routines on a systemd tick, WhatsApp confirmation channel, LAN access, stop button.
- 2026-10-19: opt-in tool routing (`llm.tool_routing`, default `off`) and `skill.unenforced` audit tripwire.
- 2026-10-22: multi-agent workflow installed (engine, skills, web, qa); agents default to acceptEdits; trial #2 -> PR #4; audit fixes #5; prompt fix #7; legacy suite split per module #8.

## In flight
- Nothing assigned. Engine stopped; qa idle after #8.

## Next (see `pending.md`)
- Run `tools/eval_models.py` off-vs-auto for tool routing; flip default if parity (needs live provider calls).
- Claude provider; verify NovaRouter tool calling with the real schema.
- Android app (owner-approved 2026-10-22, agent `android`): 1) shared toolchain + rename + first build, 2) phone channel on `kravein serve --lan` (web; the old `kravein_connect` gateway is not revived), 3) app on the new protocol + one vocabulary + tests, 4) `phone_*` skills (skills).
- Rename leftovers: `brahma` strings, XDG fallback in `config.py`.

## Open owner decisions
- Flip `llm.tool_routing` to `auto`? Both wait on evidence/owner call.

## Blockers
- None.

Baseline: `.venv/bin/python -m pytest tests/engine tests/skills tests/test_web_transport.py -q` → 428 pass (engine 248, skills 93, web transport 87). Expected failures: `test_screen_processor.py`, `test_gesture_utils.py`, `test_kravein_connect*.py`.
