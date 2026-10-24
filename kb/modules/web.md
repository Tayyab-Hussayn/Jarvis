---
type: module
owner: web
updated: 2026-10-23
---

# web

## Code map
- `web/` Next.js app (screens, `TurnGraph`, `Screen`) → `npm run build` → `kravein/web-dist/` · `kravein/server.py` FastAPI + ws · `events.py` Session frames · `webauth.py` token/Origin/Host
- `kravein/phone.py` phone channel: offers, device store, `PhoneLink`, `call()` · `kravein/phone_protocol.json` the phone vocabulary · tests in `tests/web/test_phone_channel.py`

- Phones panel: `web/components/PhonesView.tsx` (pair + paired list) · pure logic in `web/lib/phones.ts`, QR matrix in `web/lib/qr.ts` (`qrcode-generator`, offline) · tests `web/lib/phones.test.ts`

- Confirmation policy UI: `web/components/PolicyPanel.tsx` (Settings selector + in-page auto-approve confirmation) · `AutoBanner.tsx` (red strip on every view) · `web/lib/policy.ts` pure logic, `usePolicy.ts` server-confirmed state + 5 s poll · tests `web/lib/policy.test.ts`, `tests/web/test_policy_api.py`

## Verify
```bash
.venv/bin/python -m pytest tests/web tests/test_web_transport.py -q     # 97 + 87
cd web && npm test          # node --test lib/: phones, QR matrix, policy
cd web && npm run build
.venv/bin/python tools/check_layout.py     # only when the UI changed
PYTHONPATH=. .venv/bin/python web/scripts/check_phone_layout.py   # 390x844: no sideways scroll, targets >=40px, drawer, composer above keyboard
```

## Phone channel
The Android app is a device Kravein controls. It connects to the same server and port as the browser, under the same Host rules (`--lan` adds the served IP literal, never a hostname).

- **Vocabulary:** `kravein/phone_protocol.json` is the only definition of frames, commands and arguments. Envelope `{type, request_id, payload}`. A capability name **is** a command name (`get_battery`, not `battery`). The server validates every inbound frame and every outbound `execute`, and the app implements exactly this file.
- **Pair:** the owner calls `POST /api/phone/offer` (browser token) and gets `{v, service, host, port, code, expires_at}`, shown as a QR. The phone calls `POST /phone/pair {code, device_name}` and gets `{device_id, device_secret}`, once. Each offer is single-use and lasts 120 s (`phone.offer_ttl_seconds`). A new offer replaces the old one. A wrong code doesn't burn the offer, so nobody can cancel someone else's pairing. Offers live only in memory.
- **Pair UI:** the Phones view draws the QR from the offer object verbatim (`JSON.stringify(offer)`, no field added or dropped). `reachable_from_lan:false` shows the `--lan --host` instruction and never a QR. The countdown uses the offer's `expires_at`, so a browser clock far from the server's shows it early or late; the server still enforces the real expiry.
- **Store:** `DATA_DIR/phones.json` (0600) keeps only the SHA-256 of each secret. `GET /api/phones` lists devices; `DELETE /api/phones/{id}` revokes one and drops its live socket.
- **Connect:** `WS /phone/ws` with `Authorization: Device <id>:<secret>`, checked before `accept()`. The browser `Bearer` token is refused there, and a device credential is refused on `/ws` and `/api/*`.
- **UI token:** `POST /phone/ui-session` (Device auth, no body) → `{token, expires_at, ttl_seconds}` (default 12 h, `phone.ui_token_ttl_seconds`). Accepted exactly where the browser token is (`/api/*` Bearer, `/ws?token=`), never on `/phone/*`; Host/Origin checks unchanged. Store is in `webauth.py`, memory only, tied to the device: `phone.revoke()` calls `webauth.revoke_ui_tokens()`, which closes `/ws` sockets opened with it (a watcher task in `/ws` also closes at expiry). Audit `phone.ui_session` carries the device id, never the token. Tests `tests/web/test_phone_ui_session.py`.
- **Instead of Origin:** any `/phone/*` request that **carries** `Origin` is refused (403, or the websocket closes). Browsers always send it on websocket handshakes and cross-site POSTs, and phones never do.
- **Frames:** phone `hello{protocol, capabilities}` → `welcome` · server `execute{command,args}` → phone `result{ok,data | error_code,error}` (or `error`) · `ping`→`pong` · bad frame → `error{code,message}`. A frame over 64 KiB → error, then close. A late or invented `result` gets `error unknown_request`.
- **Result shapes:** every command declares `result` (the fields of `data`, same field specs as `args`, plus `values` for a fixed set) in the JSON. On `ok:true` the server checks `data` strictly (missing, wrong type, out of range, unexpected field) and a mismatch raises `PhoneBadResult` (`code="bad_result"`, audited as `phone.bad_result` with the reason only, never the data). The socket stays up. A failed result (`ok:false`) is not checked against it. A new command must add its `result`, or `test_protocol_file_is_self_consistent` fails. The app's `PhoneProtocolJsonTest` only reads `args`, so it does not compare `result` yet.
- **API:** `phone.call(command, args, device=None, timeout=15)` blocks and returns `data` or raises a `PhoneError` subclass: not connected, ambiguous (several phones and no name: it refuses and lists them, never picks the first), rejected, timeout, disconnected, or failed. A drop, revoke or replacing connection fails pending calls at once. It only works inside the `kravein serve` process. Calling it on the event-loop thread raises. State-changing commands still need `safety.enforce()` in the calling skill.
- **No TLS** (risk accepted for now): the secret crosses the LAN in plain text on every connect, the same exposure as the browser token under `--lan`. WPA home network only. Follow-up: a self-signed cert with its fingerprint pinned in the QR.

## Confirmation policy (#39)
- **API:** `GET /api/safety/policy`, `POST /api/safety/policy {mode, seconds?}`, `POST /api/safety/auto/off`; all token-gated, call `safety.*` with `by="web"`, bad input is 400 with a message. The engine enforces every guardrail; the web layer only carries the owner's choice.
- **One write path:** `POST /api/settings` returns 400 for a `safety` that is not an object or that carries `confirmation_mode`/`allow_destructive`. The Settings list hides those two rows.
- **UI invariant:** the selector and banner show only what the server last answered (`parsePolicy` rejects malformed replies); no optimistic state. The banner counts down locally from `seconds_left` re-anchored on each answer, polls every 5 s, and re-asks the server when its own countdown hits 0. Another tab's change or an expiry shows within 5 s.
- `config._deep_merge` shares nested dicts with `DEFAULT_SETTINGS`, so writing the `safety` section mutates the defaults in-process; `test_policy_api.py` deep-copies them. Reported to the orchestrator (config.py is not ours).

## Gotchas
- **Phone width (<640px, Android WebView):** the rail is `hidden sm:flex`; `NavDrawer` (Shell.tsx, opened by the top bar's menu button) replaces it. Touch targets are enforced by one `@media (max-width: 639px)` rule in `globals.css` (min 40px on buttons/inputs), not per component. `layout.tsx` sets `interactiveWidget: "resizes-content"` so the on-screen keyboard shrinks the layout instead of covering the composer. Desktop is unchanged above the breakpoint.
- No `from __future__ import annotations` in `server.py`. Every request gets a reply frame. Confirmation frames are never dropped.
- Never pass `host=` to `audit.record`: it overwrites the record's own machine-name field.
- A test that expects a refused phone socket must probe it (`ping`); an accepted socket waits for `hello` forever, so a plain `receive_text()` hangs instead of failing.
