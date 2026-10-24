---
type: state
owner: orchestrator
updated: 2026-10-23
---

# Project state (orchestrator-edited; keep under 60 lines)

## Done
- Linux port `kravein/`: 161 skills, web UI (`kravein serve`), voice (local + Gemini Live), jobs/routines on a systemd tick, WhatsApp confirmation channel, LAN access, stop button.
- 2026-10-19: opt-in tool routing (`llm.tool_routing`, default `off`) and `skill.unenforced` audit tripwire.
- 2026-10-22: multi-agent workflow installed (engine, skills, web, qa); agents default to acceptEdits; trial #2 -> PR #4; audit fixes #5; prompt fix #7; legacy suite split per module #8; agents in auto mode #14; account switching #15; phone channel #20; `serve --lan --host` fix #25; app on the phone channel #26 (56 JVM tests); result shapes #28; `phone_*` skills #32; Phones view with pairing QR #34 (2026-10-23); confirmation policy ask/decline/auto in safety.py #38; shell writes to settings.json refused #41; policy selector + auto-approve banner #42; android full client (plan `kb/plans/android-agent-app.md`) #45–#69 (2026-10-24).
- 2026-10-22: Android app #17 — moved to `android/` (`com.kravein.connect`), Gradle 9.8.1/AGP 9.4.1/Kotlin 2.4.21 on the shared platform, debug APK builds offline, 10 JVM tests.

## In flight
- Owner 2026-10-24: (a) Focus on one of several same-app windows must hit exactly that window + show terminal cwd → skills ids+cwd merged #85, #87 web UI; (b) live voice through the phone's mic/speaker → engine pluggable audio merged #84, then web (/ws audio + browser capture), then android (mic permission + adb device test).
- Owner reports 2026-10-24 fixed: stale confirm card + 14-day pairing #75; `kravein-web.service` user unit #74. Owner 2026-10-24: NO autostart, manual on/off via a `kravein` CLI → done: `kravein` CLI #79, installer wrapper + unit (never enabled) #78. Installer run by owner 2026-10-24 (web.env fixed to 192.168.100.239). Owner rule: Kravein always on port 8787, one server only → done #81 (second server refused, docker address skipped, hints fixed). All Kravein services stopped on owner request. TODO lead/batch: add `kravein/service.py` to engine in roster.json.
- Nothing assigned. Android full-client plan phases 1–6 done (#45 #47 #48 #52 #53 #55 #57 #60 #61 #64 #67 #68 #69; TLS #22 closed). Device test 2026-10-24 (adb, Vivo V2254): pairing over TLS + pinned phone channel OK; WebView fixed via loopback TLS pipe #71; full adb device pass 1–9 OK (chat, phone battery, confirm Approve/Decline, share, reminder). Open: Chromium renderer keeps ~130 MB after WebView destroy (PSS 210 MB open → 163 MB bg).
- Remaining plan ideas (not started): home-screen widget, push-to-talk from the phone mic, offline message queue, notification mirroring, clipboard sync.
- `kravein/web-dist/` is gitignored: run `cd web && npm run build` after a web merge.

## Next (see `pending.md`)
- Run `tools/eval_models.py` off-vs-auto for tool routing; flip default if parity (needs live provider calls).
- Claude provider; verify NovaRouter tool calling with the real schema.
- Android app (owner-approved 2026-10-22, agent `android`): 1) ~~toolchain + rename + build~~ done #17, 2) ~~phone channel~~ done #20, 3) ~~app on the protocol~~ done #26, 4) ~~`phone_*` skills~~ done #32.
- Rename leftovers: `brahma` strings, XDG fallback in `config.py`.

## Open owner decisions
- Decided 2026-10-23: auto-approve allowed only with guardrails — live owner sources only (web/voice/cli, never jobs/WhatsApp), CRITICAL still asks, hard refusals stay, auto expires (1 h) and resets on restart, every auto-approval audited (#35).
- Flip `llm.tool_routing` to `auto`? Both wait on evidence/owner call.

- Tests isolated from a live server #88 (suite 785 green with the owner server up).

## Blockers
- None.

## Small known bugs (fix when convenient)
- `config._deep_merge` shares nested dicts with `DEFAULT_SETTINGS` (writing a section mutates defaults in-process); `/api/settings` load-merge-save can race `set_confirmation_mode` (from #42 notes).
- `team up <agent>` (resume) can hang on Claude's "Resume session" picker when `--resume <name>` matches nothing, so the fallback to a fresh session never fires and messages go nowhere (engine, 2026-10-23). Workaround: `team up --fresh`. Fix in `start-agent.sh` (resume by stored session id) via lead/batch.

Baseline (2026-10-24, after #69): `.venv/bin/python -m pytest tests/engine tests/skills tests/web tests/test_web_transport.py -q` → 734 pass; android 150 JVM tests. Expected failures: `test_screen_processor.py`, `test_gesture_utils.py`, `test_kravein_connect*.py`.
