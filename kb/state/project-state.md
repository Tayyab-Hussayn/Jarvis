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
- Owner 2026-10-24: (a) Focus on one of several same-app windows must hit exactly that window + show terminal cwd → done #85 + #89; (b) live voice through the phone's mic/speaker → engine pluggable audio merged #84, web merged #91; #92 android (mic permission + adb device test, owner speaks one sentence).
- Owner reports 2026-10-24 fixed: stale confirm card + 14-day pairing #75; `kravein-web.service` user unit #74. Owner 2026-10-24: NO autostart, manual on/off via a `kravein` CLI → done: `kravein` CLI #79, installer wrapper + unit (never enabled) #78. Installer run by owner 2026-10-24 (web.env fixed to 192.168.100.239). Owner rule: Kravein always on port 8787, one server only → done #81 (second server refused, docker address skipped, hints fixed). All Kravein services stopped on owner request.
- Nothing assigned. Android full-client plan phases 1–6 done (#45 #47 #48 #52 #53 #55 #57 #60 #61 #64 #67 #68 #69; TLS #22 closed). Device test 2026-10-24 (adb, Vivo V2254): pairing over TLS + pinned phone channel OK; WebView fixed via loopback TLS pipe #71; full adb device pass 1–9 OK (chat, phone battery, confirm Approve/Decline, share, reminder). Open: Chromium renderer keeps ~130 MB after WebView destroy (PSS 210 MB open → 163 MB bg).
- Remaining plan ideas (not started): home-screen widget, push-to-talk from the phone mic, offline message queue, notification mirroring, clipboard sync.
- `kravein/web-dist/` is gitignored: run `cd web && npm run build` after a web merge.

## Next (see `pending.md`)
- Run `tools/eval_models.py` off-vs-auto for tool routing; flip default if parity (needs live provider calls).
- Claude provider; verify NovaRouter tool calling with the real schema.
- Android app (owner-approved 2026-10-22, agent `android`): 1) ~~toolchain + rename + build~~ done #17, 2) ~~phone channel~~ done #20, 3) ~~app on the protocol~~ done #26, 4) ~~`phone_*` skills~~ done #32.
- ~~Rename leftovers + XDG fallback~~ done #127.

## Open owner decisions
- Decided 2026-10-23: auto-approve allowed only with guardrails — live owner sources only (web/voice/cli, never jobs/WhatsApp), CRITICAL still asks, hard refusals stay, auto expires (1 h) and resets on restart, every auto-approval audited (#35).
- Flip `llm.tool_routing` to `auto`? Both wait on evidence/owner call.

- Tests isolated from a live server #88 (suite 785 green with the owner server up).

- Owner 2026-10-24: open_app into a workspace merged #95; #96 web sorted workspaces + app launcher + terminal popup merged.

- Live voice: control tokens stripped, empty-turn fallback, English/Roman-Urdu only #99 (the missed tool call was a model-side glitch, mitigated not proven fixed). Phone mic+speaker live voice merged #98 (owner-confirmed on device).

- Voice transcript in English via Whisper translate #101 (base model downloads on first use). Stuck "running" cards → "unknown" + auto-reconnect #104. Nothing in flight.

- Dynamic LAN address + mDNS `_kravein._tcp` #107 (owner re-runs installer: web.env → auto). Android rediscovers the PC by mDNS #108 (not yet device-tested on another network). Hybrid native app: native Chat merged #113 (device-tested; PSS 115 MB with Chat). Plan items 8–10: resumable sessions merged #111 (resume via /ws?resume=, voice grace 60 s, not across server restart), native voice + background + resume + bubble merged #119 (open: owner heard 2–3 overlapping voices once, not reproduced); #120 android device pass of #119 open items + overlap hunt — checks 1 bubble, 2 rationale, 4 PSS (115–126 MB bg) verified #121; check 3 overlap hunt needs the owner speaking ~10 turns (typed turns do not reach Live; no working TTS on the PC); declined-target retry ends the turn #117. Owner's phone has 3 pairings — remove the 2 old ones after the device test.

- Niri port (plan `kb/plans/niri-port.md`, owner 2026-10-29): #122 qa provision + installer, #123 skills niri backend. Remote via `ssh kravein-new`; only `/home/kravein/Projects/desktop/kravein` writable; Windows tree + old-name fallbacks deleted #127, docs/team cleanup lead batch, niri config read-only. Paused 2026-10-30 (remote unreachable, "No route to host"; team down): qa #122 has 3 local commits on agent/qa (not pushed); remote .venv half-installed → resume: detached venv install, remote suite + web build, ship. skills #126 (changes-requested) left: live open_app into ws 2 + screenshot on the remote. Owner still to run `sudo pacman -S ydotool wtype wl-clipboard grim slurp` there. **Owner 2026-11-02: after the v2 UI (#145) and agent_state (#146), set Kravein up fully on the niri laptop; tell the owner before live testing so he turns ssh on.** Resume order: qa #122 (pushed agent/qa) → skills #126 live ws-2 test → full live pass of the v2 UI + Orchestrator on niri.

- Owner 2026-11-01: text primary = OpenCode Zen `deepseek-v4.1-flash`, merged #130 (chain zen → cline → novarouter; 402/429 opens the breaker for 30 min). Live Zen answer pending: account has no credit (402); Zen `-free` models are OpenCode-client only (429). After the owner adds credit: run `reset_llm_failover`, then a live ask + tool call. Owner 2026-11-01: this machine's settings.json now leads with Gemini `gemini-3.5-flash-lite` (asked for 2.5-flash-lite: 404 "no longer available to new users"); chain gemini → zen → cline → novarouter; live ask + tool call OK. Backup: settings.json.bak-2026-11-01. Code defaults unchanged (zen).

- **Orchestrator + sub-agents (plan `kb/plans/orchestrator-subagents.md`, owner 2026-11-01):** phase 1 merged — #136 edit_file/search_text + sensitive-path rules in safety.check_path (Kravein config/state/data blocked; ssh/gpg/shell rc/systemd user/autostart confirm), #137 agent profiles (`kravein/agents/<name>/SKILL.md`), dispatch `allowed` set, alias presets, SQLite per-agent history per principal, token budget + summary (994 tests). In flight: skills #133 part 2 (skill merges via alias presets), engine #138 phase 2 (delegate, Orchestrator profile, escalation; switch `agents.orchestrator` default off). Guard allows task branches `agent/<name>-<task>` (#135). Owner decision pending: code default cline model paid kimi-k2 vs free. Phase 2 merged #139 (delegate on caller's thread + owner confirmer pass-through + shared denial memory, Orchestrator + minimal system-ops profiles, escalation after 3 rounds, `agents.orchestrator` switch default off, profile `model` = provider:model). Skill merges #140 (169→123 tools, aliases with presets). Phase 3 merged #142 (system-ops/research/reviewer profiles, Orchestrator guidance, eval scenario files; research cannot write memory — `recall` split out as its own SAFE skill to stop alias widening). Live 2026-11-01: `agents.orchestrator` ON in this machine's settings.json; Orchestrator did disk_usage itself and delegated folder sizes to system-ops (outcome done, Gemini flash-lite). Phase 4 engine #143 in progress (plan approved with session_id + review-kind guards); **paused 2026-11-02 (owner powered off): WIP committed by orchestrator and pushed as agent/engine d57d9e6 ("wip: proposals queue"), unverified — engine was mid full-suite run. Resume: `team up --fresh --task 143 engine`, re-run suite, finish, ship.** qa #122 commits pushed to origin agent/qa. Phase 4 merged #144 (proposals queue, REST decide + `t:proposal` frames, approve-by-voice/text = CRITICAL card, daily review job default off; tests isolated from real settings.json). #147 `/api/agents`, `/api/agents/state`, `t:agent_state` frames per principal; tool frames carry agent/delegation. v2 UI merged #148 (Command Constellation: no sidebar, bottom command deck, chrome orb + live agent nodes, glass panes; brief `kb/design/kravein-v2-ui.md`) + #150 Proposals pane/badge/stream polish; web-dist rebuilt. Niri laptop (ssh kravein-new = 100.84.230.49, permanent) provisioned #151 (1193 pass there) and niri backend merged #126 (live open/focus/move/close on own windows; 1218 pass); rule `.claude/rules/niri-desktop.md` #152; owner must run `systemctl --user enable --now ydotool` (no sudo) there. Live pass on niri #155: 8/8 (qa live_serve.sh stops only its own pid). Gemini multi-turn break #154 fixed #156 (tagged base64 signatures; signature-less replayed calls flattened to text — Google's dummy signature is rejected by gemini-3.5-flash-lite). Synced to niri 7eff51b. Open: ydotool on niri needs owner re-login; Owner 2026-11-02: growth agent + Android redesign ON HOLD; priority = Live voice as the Orchestrator (#157, also fixes HUD model label). Next: growth agent; Live voice on the Orchestrator; eval_models.py scenario-file support.

**Deferred by owner 2026-11-04 (token cost; do after Kravein is complete):** second post-harness eval run (#224), full eval after phase 4, tool-routing eval, #178 niri live pass, #120 android device pass; model stays gemini-3.5-flash-lite (cheapest available here; 2.5-flash-lite 404s for this account), owner upgrades the model later.
Owner-blocked (from 2026-11-02): run `./install-linux.sh` on the niri laptop (kravein CLI), a working second provider there (Zen credit or `cline auth`), owner voice test, #120 overlap hunt, 2 old phone pairings. On hold: growth agent, Android redesign.

## Status 2026-11-03
Merged since: #197–#210. **System-ops harness upgrade** (plan `kb/plans/system-ops-harness.md`): merged #215 (secret-read guard, Verdict.secret_read never auto/Always, Agent.tool_state), #216 (qa outcome eval `tools/eval_tasks.py`, baseline 11/13 + 10/13 on flash-lite; fails: 6-round cap, rm -rf refusal without safer way). Merged #214 (read with line numbers + read record, edit read-before-edit/replace_all/multi-edit/diff, regex search, find by mtime). Merged #219 (update_plan, one in_progress enforced, plan in context tail, 25 rounds + 60k token budget, partial report). Merged #221 (judgment rules, CONCERNS/IMPROVEMENTS, sub-agent propose note/capability max 2 origin-tagged), #222 (run_command cwd persists, spill to workspace outputs/, group kill, background jobs via command_job, timeout→background). Merged #226 (checkpoints per principal + undo_changes; force card owner_only via Verdict.no_auto; KRAVEIN.md in prompts; copy/move source checks; safety.shell_tokens). In flight: skills #227 (phase 4 bwrap sandbox + shell_tokens switch), qa #224 (one post-harness eval run). Next: engine adds sandbox SKILL.md line, web (plan + proposal origin/capability).

## Status 2026-11-13 (architecture redesign, no execution yet)
- Owner wants Kravein v3 (JARVIS goal). Research pack + Fable's proposal **v4** in `user-space/fable-research/` (gitignored, main checkout only, for Fable; README has the owner's end goal; delete after the plan lands in `kb/plans/`). Fable runs as a separate session; the owner relays messages (Fable is NOT a team agent).
- **2026-11-14 owner: execute the v3 plan autonomously** (plan of record `kb/plans/kravein-v3-architecture.md`). Engine runs on Opus via the logged `--owner-opus` exception (PR #229 is open: the classifier blocked the self-merge, so the owner merges it). Packets follow §19's recommendations until the owner rules; D2, D3 and D7 removals wait for the owner. Merged #230 (model roles + usage ledger, `kravein usage`) and #232 (generic OpenAI-compatible endpoints + Anthropic provider; no live keys yet). Parked for phase 2: #233 (append-only transcript so Claude thinking survives). In flight: engine #234, phase 0c (catalog, probe, doctor, prompt snapshot). Next: 0d web model picker + spend view; then phase 1.
- **Opus queue** (engine, one at a time; review → merge → next immediately; owner rulings D4/D9/D10 at the top of the plan):
  1. 0c #234 (in flight)
  2. 1a safety: `GUARDED` + grants (D4) + taint
  3. 1b MCP core (`kravein/mcp/`, stdio via platform/shell, pins, bridge, mock server)
  4. 1c plugin loader, declarative + easy code plugins (D10), plus the `growth` profile
  5. 2a `Runtime.submit` + runs table + parked approvals + unattended profile, moving jobs/daemon/review onto it
  6. 2b loop: degrade, tool-result clearing, parallel SAFE calls, loop guard, #233
  7. 2c streaming + real cancel
  8. 2d verification: `submit_report`, handoff packet, `ask_parent`, typed results, code outcome
  9. 3a tool search bridge
  10. 4a voice thin front + handoff
  11. then the cores of 5, 6/6B, 7, 3B and 4B

  **Sonnet in parallel:**
  - web 0d (picker + spend) after 0c;
  - skills: `browser_type` submit fix + re-class outward skills to `GUARDED` after 1a;
  - web: grants/MCP/plugin UI;
  - qa: mock MCP + evals.
- Then: orchestrator reviews v4's new sections (8A computer use, 8B browser, 12A goals, phases 3B/4B/6B), then the owner decides D1–D23; then the plan goes to `kb/plans/`, with the core built by Fable and the rest by Sonnet agents (split proposed in chat: Fable = Runtime/runs, safety GUARDED/grants/taint, MCP core, LLM contract, voice handoff, verification contract).
- Fable found a safety gap: `browser_type(submit=True)` submits forms without a card (to fix first in phase 1). `click_on_screen` uses the broken ydotool absolute-move path, and GUI click does not work on niri.
- #227 (skills sandbox): plan + commit f737040 on origin/agent/skills, no PR yet. Agents are all stopped.
- superpowers plugin: locked system-wide; only `brainstorming` is enabled, and only when the owner asks (`~/.claude/hooks/superpowers-lock.sh`).

## Blockers
- None.

## Small known bugs (fix when convenient)
- edge-tts returns 403 on this machine (seen by android 2026-10-25) — may break local `kravein voice` TTS; check `kravein voice test`.
- `config._deep_merge` shares nested dicts with `DEFAULT_SETTINGS` (writing a section mutates defaults in-process); `/api/settings` load-merge-save can race `set_confirmation_mode` (from #42 notes).

Baseline (2026-10-24, after #69): `.venv/bin/python -m pytest tests/engine tests/skills tests/web tests/test_web_transport.py -q` → 734 pass; android 150 JVM tests. Expected failures: `test_screen_processor.py`, `test_gesture_utils.py`, `test_kravein_connect*.py`.
