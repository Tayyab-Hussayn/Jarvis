# PLAN.md — Brahma Echo Frontend

Living plan for the frontend development phase. Updated as phases complete and as decisions
land. `user-space/STATE.md` tracks *where we are*; this file tracks *what we agreed to build*.

**Status:** Phases 1–5 complete for v1. 192 tests passing. Awaiting the look-and-feel brief.
**Look and feel:** not yet specified by the user — see [Theming](#theming-placeholder-until-specified).

---

## 1. Background

The original Windows build shipped two separate frontends:

| | What it was | Size |
|---|---|---|
| `ui.py` | PyQt6 desktop HUD — boot sequence, scanning overlay, HUD canvas, floating launcher, conversation feed, task dock, gesture camera preview, smart-device tiles, setup wizard | 12,005 lines, 40+ widget classes |
| `dashboard/server.py` + `static/app.html` | FastAPI + websocket remote control — QR device login, phone mic streaming, file upload, command relay | ~38 KB + 36 KB |

Qt nav was four pages: Dashboard, Brahma Echo Home, Devices, System & Connect.

**Neither is being ported directly.** Reasons:

1. `ui.py` imports `discord_bot`, `gesture_utils`, `smart_home`, `smart_home_page_new`,
   `workspace_store`. Gesture tracking is blocked (no mediapipe wheels for Python 3.14) and smart
   home was never ported, so a 1:1 port drags in five dead dependencies.
2. Wayland gives no application the right to position its own window, sit always-on-top, or go
   click-through. The floating launcher and full-screen HUD overlay need `wlr-layer-shell`, which
   PyQt6 does not speak. That part is deferred and isolated (see [v4](#v4--hud-extras-optional)).
3. One web codebase serves the desktop window *and* the phone. The original needed two codebases
   for that.

---

## 2. Decisions (locked)

| Decision | Choice | Why |
|---|---|---|
| Frontend stack | **Next.js 15 (App Router) + React 19 + TypeScript + Tailwind v4** | User's home turf; App Router in pure-SPA mode |
| Component primitives | **shadcn/ui** (Radix under the hood) | Unstyled + accessible. We own the look; it owns the keyboard/focus/ARIA correctness we would otherwise get wrong |
| Build output | **`output: 'export'` → static** | No Node at runtime. FastAPI serves the built `out/` dir |
| Backend | **FastAPI + `uvicorn`, websocket + REST** | Same shape the original `dashboard/server.py` proved |
| Bind | **`127.0.0.1` only in v1** | Remote is v3, and shipping LAN-bound early is how local services get owned |
| v1 target | **Browser tab** | Confirmed by user |
| v2 | **Tauri desktop window** | Native window + tray, tiny binary, no Electron |
| v3 | **Phone / remote** | Confirmed as wanted, deliberately not v1 |

### Repo layout

```
brahma/
  server.py        # FastAPI app: websocket, REST, static mount
  events.py        # typed event frames + per-session bus
  webauth.py       # token issue/verify, Origin check
web/               # Next.js app (its own package.json, not in the venv)
  app/
  components/
  lib/
  styles/tokens.css
brahma/web-dist/   # built static output, gitignored, served by server.py
```

---

## 3. The blocking constraint

**The UI cannot be built against the transport that exists today.**

`brahma/daemon.py` is a Unix-socket JSONL server: one request in, one full response out. No
streaming, no events. Its confirmer is `_preapproved`, which **auto-denies** everything
destructive — correct for a background daemon that must not approve its own dangerous actions,
useless for an interactive UI.

Three capabilities have to exist before any pixel is worth drawing:

1. **Streaming** — tool calls and results as they happen, not a 24-second silence then a wall of
   text. `Agent` already has an `Observer` hook (`kind, text` for `thinking | tool | tool_result |
   answer | error`), but it only carries strings. It needs structured payloads.
2. **Round-trip confirmation** — `safety.Confirmer` is a **blocking, thread-local** callable
   `(action, detail, Danger) -> bool`. The web bridge runs the agent on a worker thread and blocks
   that thread on a `threading.Event` while the browser is asked. **This is the one piece that
   must not be shortcut.** Getting it wrong means a web page silently approving `delete_path`.
3. **Push events** — audit entries, provider failover, voice state, system metrics.

Order is therefore: **transport → design → pixels.**

---

## 4. Wire protocol

One websocket at `/ws`. JSON frames, `{"t": "<type>", ...}`. Client→server frames are few;
server→client carries the stream.

### Client → server

| `t` | Payload | Meaning |
|---|---|---|
| `ask` | `text`, `id` | New user request |
| `confirm_reply` | `id`, `approve: bool` | Answer to a pending confirmation |
| `cancel` | `id` | Abort the in-flight turn |
| `reset` | — | Clear conversation history |
| `ping` | — | Keepalive |

### Server → client

| `t` | Payload | Meaning |
|---|---|---|
| `hello` | `assistant_name`, `skills`, `provider`, `model`, `environment` | Sent on connect |
| `turn_start` | `id` | Request accepted |
| `thinking` | `id`, `text` | Model prose emitted alongside tool calls |
| `tool_call` | `id`, `call_id`, `name`, `args` | A skill is about to run |
| `tool_result` | `id`, `call_id`, `result`, `ok` | That skill returned |
| `confirm_request` | `id`, `confirm_id`, `action`, `detail`, `danger` | **Blocks the agent thread** until answered |
| `answer` | `id`, `text` | Final prose reply |
| `turn_end` | `id`, `iterations`, `error` | Turn finished either way |
| `audit` | audit record | Tailed from the audit log |
| `error` | `message` | Transport-level failure |

Notes:

- `danger` is the `safety.Danger` name (`SAFE`/`MODERATE`/`DESTRUCTIVE`/`CRITICAL`). The UI styles
  the confirm card by level and requires a deliberate click for `CRITICAL`.
- A `confirm_request` with no reply within a timeout resolves to **deny**. Silence is never
  approval.
- Only one turn in flight per session. A second `ask` while one is running is rejected, not queued.

### REST

Read-only state that does not belong in the stream, so a page refresh doesn't need a turn:

```
GET  /api/state        provider, model, breaker state, skill count, environment
GET  /api/skills       full registry with schemas
GET  /api/system       CPU / mem / disk / battery / network snapshot
GET  /api/audit?n=100  recent audit entries
GET  /api/voice        mic health, STT/TTS backend availability
GET  /api/settings     redacted settings (keys never returned)
POST /api/settings     patch settings
```

---

## 5. Security

Loopback binding alone is **not** sufficient. Any page in the user's browser can issue requests to
`http://127.0.0.1:8787`, and DNS rebinding defeats naive host checks. Three controls, all required:

1. **Bearer token.** Generated at server start, written `0600` to
   `$XDG_RUNTIME_DIR/brahma-echo/web-token` inside a `0700` directory. Required on every REST call
   and as a `?token=` query parameter on the websocket handshake. Compared with
   `secrets.compare_digest`.
2. **Origin allowlist.** Reject any websocket or REST request whose `Origin` is not the server's
   own origin or the dev origin (`http://localhost:3000`). Guards against a random site scripting
   the local server.
3. **Host header check.** Reject requests whose `Host` is not `127.0.0.1`/`localhost` + our port.
   This is the actual DNS-rebinding defence.

Additionally:

- **Settings API never returns key material.** `GET /api/settings` redacts; `POST` is write-only
  for key fields. There is already a test asserting `read_auth()` never leaks `eyJ`/`workos:`
  material; an equivalent test covers the HTTP surface.
- **The web confirmer is a real confirmer**, not `_auto_yes`. It surfaces the exact
  `(action, detail, danger)` the skill passed, and forbidden actions stay forbidden regardless of
  what the browser answers — `safety.enforce()` refuses protected paths, PID 1, the compositor and
  `rm -rf /` before any confirmer is consulted, and the web path does not change that.
- Server is **opt-in**: `brahma serve`. It does not start with the REPL or the systemd daemon.

---

## 6. Screens

### Console *(the core; everything else is secondary)*

The conversation. Streaming reply, tool calls rendered inline as they fire — this is what makes a
119-skill agent legible instead of a black box. Inline confirm cards for destructive actions,
styled by danger level. Command palette for direct skill invocation (`brahma run <skill>` with no
model in the loop).

### System

Live CPU / memory / disk / battery / network, running apps, windows per workspace. Replaces the Qt
`StatCard` / `MetricBar` / `HudCanvas`.

### Activity

The audit log, filterable by skill, source, danger and outcome. Already exists as JSONL at
`~/.local/state/brahma-echo/audit.jsonl` — this is the highest value-per-line-of-code screen on
the list, and per `CLAUDE.md` it's the first thing to read when debugging "why did it do that".

### Voice

Mic health and calibration, STT/TTS backend state, `/voice` (local, free) vs `/voice live`
(Gemini native audio, metered) toggle. Surfaces the measured tradeoff: 19.3s local vs 7.1s Live.

### Settings

Provider order, model, circuit-breaker state, safety thresholds, API keys (write-only).

**Deliberately excluded from v1:** Devices / Brahma Connect (the gateway was never ported, so the
page would have nothing behind it), and the animated boot sequence + scanning overlay (most code,
least function in the original, and riskiest on Wayland).

---

## 7. Theming *(placeholder until specified)*

The user will describe the look and feel separately; this section gets rewritten then.

Until that lands, **every colour, radius, spacing step, font and motion duration lives as a CSS
custom property in `web/styles/tokens.css`**, and no component hardcodes a value. Tailwind is
configured to read those variables rather than its default palette. The re-skin is then a
single-file change, not a sweep through every component.

Structural choices being made now that are theme-independent: layout grid, information density,
what goes on which screen, interaction and keyboard model, motion *semantics* (what animates and
why, not how fast or in what colour).

---

## 8. Phases

### Phase 1 — Transport ✅

- [x] `Agent.Observer` carries structured payloads; 2-arg callers (`cli.py`, `voice.py`) unchanged
- [x] `brahma/events.py` — frames + per-session bus + confirmation bridge
- [x] `brahma/webauth.py` — token issue/verify, Origin + Host checks
- [x] `brahma/server.py` — FastAPI app, `/ws`, REST endpoints, static mount
- [x] Blocking web confirmer bridged to `confirm_request` / `confirm_reply`
- [x] `brahma serve` CLI subcommand
- [x] `tests/test_web_transport.py` — 48 tests, all passing

Two things this phase turned up, both now encoded as comments where they bite:

- **`server.py` must not use `from __future__ import annotations`.** FastAPI resolves route type
  hints to decide what is a request object and what is a query parameter. Postponed annotations
  are strings resolved against module scope, where the lazily-imported `Request` / `WebSocket`
  names don't exist — so every handler's request argument was misread as a required query
  parameter and the routes 422'd.
- **Starlette's `TestClient` sends `Host: testserver` on websocket upgrades** regardless of
  `base_url`, which the loopback guard correctly rejects. Tests pass an explicit loopback `Host`.

Verified against a live server on port 8791: no token → 401, foreign Origin → 403, valid token →
200 with all 119 skills. Both directions of the confirmation bridge are covered end to end — a
denial leaves the file on disk, an approval actually deletes it.

### Phase 1a — Safety hardening *(unplanned; forced by a live test)* ✅

Driving a real turn over the websocket — "delete this file", denying every confirmation — the
model **worked around the denial**. It tried `delete_path`, then `delete_path permanent=true`,
then `rm -f`, then `rm`, all denied, then `unlink`, which matched no pattern in `safety.py`. That
one ran unconfirmed and the file was deleted.

The transport was not at fault: every confirmation was raised and denied correctly. The hole was
in the pre-existing safety layer, and the system prompt's "never try to work around a
confirmation" turned out to be advice, not enforcement.

- [x] Classify the missing verbs: `unlink`, `find -delete`, `find -exec rm`, `xargs rm`,
      `rsync --delete`, `busybox rm`, `gio trash`, `trash-put`, `sed -i`, and interpreter
      one-liners (`python -c "os.unlink(…)"`, `perl -e unlink`)
- [x] **Per-turn denial memory** in `safety.enforce()`, keyed on the *target* rather than the
      verb. Declining an action on a path refuses every later destructive action on that same
      path in the same request, without re-asking. Scoped by `confirmer_scope`, so it lasts one
      turn — a blanket lockout would break the legitimate "delete A, keep B" case.
- [x] 15 tests: every deletion verb, the five-verb attack, target isolation, per-turn clearing

Re-running the identical live attack after the fix: **one** confirmation, the retry refused
outright, and the model reported honestly that the file was not deleted.

A verb blocklist is inherently incomplete — that is why the denial memory exists. Do not treat
the pattern list as the defence.

### Phase 2 — App shell ✅

- [x] Next.js 16.3.2 / React 19.2.8 / Tailwind 4 in `web/`, static export configured
- [x] `app/tokens.css` with placeholder theme; Tailwind reads the variables
- [x] `lib/useBrahma.ts` — typed websocket client, exponential reconnect, no retry on auth
      rejection (close code 1008)
- [x] Nav shell across the five screens; connection state always visible

Two deviations from the original decisions, both deliberate:

- **Hash-based views, not Next routes.** A static export served by FastAPI's `StaticFiles`
  resolves nested routes through directory-index lookups, turning every deep-link refresh into a
  trailing-slash problem. One route avoids that entirely.
- **No shadcn/ui yet.** Its `init` rewrites `globals.css`, which is where the theming contract
  lives, and the shell needs a panel, a stat, a badge and a button — none with accessibility
  behaviour worth outsourcing. Radix earns its place when the command palette and dialogs land
  (focus traps, portals, roving tabindex). That is Phase 3.

### Phase 3 — Console ✅

- [x] Message stream, tool-call cards with argument/result disclosure, answer rendering
- [x] Confirm cards styled by danger level, with a live countdown and a second deliberate click
      for `CRITICAL`
- [x] History, reset, Enter-to-send
- [ ] Command palette / direct skill invocation — deferred; this is what pulls in Radix

### Phase 4 — Remaining screens ✅

- [x] System (3s polling), Activity (audit log, filterable), Voice, Settings
- [x] `GET /api/voice` added to the backend to support the Voice screen

### Phase 5 — Integration ✅

- [x] `install-linux.sh` step 7 builds the web app when npm is present, skips cleanly when it isn't
- [x] `brahma doctor` reports frontend build status
- [x] `CLAUDE.md` — new tree, commands, architecture, and both hard-won lessons from this phase

### v2 — Tauri

Wrap the same build. Native window, tray icon, global hotkey. No frontend rewrite.

### v3 — Phone / remote

LAN binding, QR pairing, device tokens with revocation, phone mic streaming — the parts
`dashboard/server.py` already solved once and can be read for reference. Gated behind explicit
opt-in; loopback stays the default.

### v4 — HUD extras *(optional)*

Floating launcher and overlay via `gtk4-layer-shell`. Separate process, separate decision. Last
because it's the only part that can fail on platform grounds.

---

## 9. Open questions

1. **Look and feel** — still the one blocking input. Everything is themed through
   `web/app/tokens.css`; describe the look and the re-skin is that one file.
2. ~~Raw tool JSON by default or behind a disclosure?~~ Resolved: disclosure, with a persisted
   "always expand tool calls" toggle in the Console header.
3. ~~Does `brahma serve` auto-open a browser tab?~~ Resolved: yes, with `--no-open`.
4. Should the Console be able to *cancel* an in-flight turn? The `cancel` frame is specified in
   §4 but not implemented — the agent loop has no interruption point, so this needs a real design
   rather than a button that lies.

## 10. Verified

- 192 tests pass (`tests/test_linux_port.py` 144, `tests/test_web_transport.py` 48)
- Live server on a real port: no token → 401, foreign Origin → 403, rebound Host → 400, valid
  token → 200 with all 119 skills
- A full turn end to end against the real model:
  `hello → turn_start → tool_call(disk_usage) → tool_result → answer → turn_end`, 2 rounds
- The confirmation bypass attack, before and after the fix (see Phase 1a)
- `npm run build` → static export served by `brahma serve`; index, chunks and API all 200
