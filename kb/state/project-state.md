---
type: state
owner: orchestrator
updated: 2026-10-22
---

# Project state (orchestrator-edited; keep under 60 lines)

## Done
- Linux port `kravein/`: 161 skills, web UI (`kravein serve`), voice (local + Gemini Live), jobs/routines on a systemd tick, WhatsApp confirmation channel, LAN access, stop button.
- 2026-10-19: opt-in tool routing (`llm.tool_routing`, default `off`) and `skill.unenforced` audit tripwire.
- 2026-10-22: multi-agent workflow installed (engine, skills, web, qa); agents default to acceptEdits; trial #2 -> PR #4 merged; audit fixes PR #5 (path-scoped hard-won rules, bash frozen guard).

## In flight
- #6 qa: split `tests/test_linux_port.py` into tests/engine|skills|web (grants 24h). Engine idle, synced, stopped.

## Next (see `pending.md`)
- Run `tools/eval_models.py` off-vs-auto for tool routing; flip default if parity (needs live provider calls).
- Claude provider; verify NovaRouter tool calling with the real schema.
- Kravein Connect + `brahma-connect-android/` (frozen): secure the gateway, one capability vocabulary, first build (Android SDK/JDK 21 exist on this machine).
- Rename leftovers: `brahma` strings, XDG fallback in `config.py`.

## Open owner decisions
- Flip `llm.tool_routing` to `auto`? Unfreeze Android? Both wait on evidence/owner call.

## Blockers
- None.

Baseline: `.venv/bin/python -m pytest tests/test_linux_port.py -q` → 314 pass; `tests/test_web_transport.py` → 87 pass. Expected failures: `test_screen_processor.py`, `test_gesture_utils.py`, `test_kravein_connect*.py`.
