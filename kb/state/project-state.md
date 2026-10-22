---
type: state
owner: orchestrator
updated: 2026-10-22
---

# Project state (orchestrator-edited; keep under 60 lines)

## Done
- Linux port `kravein/`: 161 skills, web UI (`kravein serve`), voice (local + Gemini Live), jobs/routines on a systemd tick, WhatsApp confirmation channel, LAN access, stop button.
- 2026-10-19: opt-in tool routing (`llm.tool_routing`, default `off`) and `skill.unenforced` audit tripwire.
- 2026-10-22: multi-agent workflow installed (engine, skills, web, qa); agents default to acceptEdits; trial #2 -> PR #4; audit fixes #5; prompt fix #7; legacy suite split per module #8; agents in auto mode #14; account switching #15.
- 2026-10-22: Android app #17 — moved to `android/` (`com.kravein.connect`), Gradle 9.8.1/AGP 9.4.1/Kotlin 2.4.21 on the shared platform, debug APK builds offline, 10 JVM tests.

## In flight
- #24 android: app on the new phone channel + one vocabulary. #21 engine: `serve --lan --host` first-match bug. #22 TLS follow-up (later). web/qa/skills stopped.

## Next (see `pending.md`)
- Run `tools/eval_models.py` off-vs-auto for tool routing; flip default if parity (needs live provider calls).
- Claude provider; verify NovaRouter tool calling with the real schema.
- Android app (owner-approved 2026-10-22, agent `android`): 1) ~~toolchain + rename + build~~ done #17, 2) ~~phone channel~~ done #20, 3) app on the new protocol + one vocabulary + tests, 4) `phone_*` skills (skills).
- Rename leftovers: `brahma` strings, XDG fallback in `config.py`.

## Open owner decisions
- Flip `llm.tool_routing` to `auto`? Both wait on evidence/owner call.

## Blockers
- None.

Baseline: `.venv/bin/python -m pytest tests/engine tests/skills tests/test_web_transport.py -q` → 428 pass (engine 248, skills 93, web transport 87). Expected failures: `test_screen_processor.py`, `test_gesture_utils.py`, `test_kravein_connect*.py`.
