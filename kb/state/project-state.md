---
type: state
owner: orchestrator
updated: 2026-10-23
---

# Project state (orchestrator-edited; keep under 60 lines)

## Done
- Linux port `kravein/`: 161 skills, web UI (`kravein serve`), voice (local + Gemini Live), jobs/routines on a systemd tick, WhatsApp confirmation channel, LAN access, stop button.
- 2026-10-19: opt-in tool routing (`llm.tool_routing`, default `off`) and `skill.unenforced` audit tripwire.
- 2026-10-22: multi-agent workflow installed (engine, skills, web, qa); agents default to acceptEdits; trial #2 -> PR #4; audit fixes #5; prompt fix #7; legacy suite split per module #8; agents in auto mode #14; account switching #15; phone channel #20; `serve --lan --host` fix #25; app on the phone channel #26 (56 JVM tests); result shapes #28; `phone_*` skills #32; Phones view with pairing QR #34 (2026-10-23); confirmation policy ask/decline/auto in safety.py #38; shell writes to settings.json refused #41.
- 2026-10-22: Android app #17 — moved to `android/` (`com.kravein.connect`), Gradle 9.8.1/AGP 9.4.1/Kotlin 2.4.21 on the shared platform, debug APK builds offline, 10 JVM tests.

## In flight
- #39 web: confirmation policy selector + red auto-approve banner (on engine API from #38).

- Owner: live pairing via the Phones view. Then: app result-shape test (android); #22 TLS. `kravein/web-dist/` is gitignored: run `cd web && npm run build` after a web merge.

## Next (see `pending.md`)
- Run `tools/eval_models.py` off-vs-auto for tool routing; flip default if parity (needs live provider calls).
- Claude provider; verify NovaRouter tool calling with the real schema.
- Android app (owner-approved 2026-10-22, agent `android`): 1) ~~toolchain + rename + build~~ done #17, 2) ~~phone channel~~ done #20, 3) ~~app on the protocol~~ done #26, 4) ~~`phone_*` skills~~ done #32.
- Rename leftovers: `brahma` strings, XDG fallback in `config.py`.

## Open owner decisions
- Decided 2026-10-23: auto-approve allowed only with guardrails — live owner sources only (web/voice/cli, never jobs/WhatsApp), CRITICAL still asks, hard refusals stay, auto expires (1 h) and resets on restart, every auto-approval audited (#35).
- Flip `llm.tool_routing` to `auto`? Both wait on evidence/owner call.

## Blockers
- None.

## Known team bug
- `team up <agent>` (resume) can hang on Claude's "Resume session" picker when `--resume <name>` matches nothing, so the fallback to a fresh session never fires and messages go nowhere (engine, 2026-10-23). Workaround: `team up --fresh`. Fix in `start-agent.sh` (resume by stored session id) via lead/batch.

Baseline (2026-10-23, after #32): `.venv/bin/python -m pytest tests/engine tests/skills tests/web tests/test_web_transport.py -q` → 547 pass. Expected failures: `test_screen_processor.py`, `test_gesture_utils.py`, `test_kravein_connect*.py`.
