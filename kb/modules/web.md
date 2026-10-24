---
type: module
owner: web
updated: 2026-10-24
---

# web

## Code map
- `web/` Next.js app (screens, `TurnGraph`, `Screen`) → `npm run build` → `kravein/web-dist/` · `kravein/server.py` FastAPI + ws · `events.py` Session frames · `webauth.py` token/Origin/Host
- `kravein/phone.py` phone channel: offers, device store, `PhoneLink`, `call()` · `kravein/phone_protocol.json` the phone vocabulary · tests in `tests/web/test_phone_channel.py`

- Phones panel: `web/components/PhonesView.tsx` (pair + paired list) · pure logic in `web/lib/phones.ts`, QR matrix in `web/lib/qr.ts` (`qrcode-generator`, offline) · tests `web/lib/phones.test.ts`

- Confirmation policy UI: `web/components/PolicyPanel.tsx` (Settings selector + in-page auto-approve confirmation) · `AutoBanner.tsx` (red strip on every view) · `web/lib/policy.ts` pure logic, `usePolicy.ts` server-confirmed state + 5 s poll · tests `web/lib/policy.test.ts`, `tests/web/test_policy_api.py`

- Share-in link `/?ask=<text>` (#58): `web/lib/ask.ts` (pure: cap 4000 code points, control chars stripped, tests `ask.test.ts`) · `captureAsk()` in `session.ts` reads it once and strips it from the URL next to `token` · `Console` puts it in the composer from an effect (an existing draft wins). Never sent, shown only as a textarea value.

## Verify
```bash
.venv/bin/python -m pytest tests/web tests/test_web_transport.py -q     # 97+17 + 87
cd web && npm test          # node --test lib/: phones, QR matrix, policy
node web/scripts/check_contrast.mjs        # WCAG AA on token pairs, both themes
cd web && npm run build
.venv/bin/python tools/check_layout.py     # only when the UI changed
PYTHONPATH=. .venv/bin/python web/scripts/check_phone_layout.py   # 390x844: no sideways scroll, targets >=40px, drawer, composer above keyboard
```

## Phone channel
The Android app is a device Kravein controls. It connects to the same server and port as the browser, under the same Host rules (`--lan` adds the served IP literal, never a hostname).

- **Vocabulary:** `kravein/phone_protocol.json` is the only definition of frames, commands and arguments. Envelope `{type, request_id, payload}`. A capability name **is** a command name (`get_battery`, not `battery`). The server validates every inbound frame and every outbound `execute`, and the app implements exactly this file.
- **Pair:** the owner calls `POST /api/phone/offer` (browser token) and gets `{v, service, host, port, code, expires_at}`, shown as a QR. The phone calls `POST /phone/pair {code, device_name}` and gets `{device_id, device_secret}`, once. Each offer is single-use and lasts 120 s (`phone.offer_ttl_seconds`). A new offer replaces the old one. A wrong code doesn't burn the offer, so nobody can cancel someone else's pairing. Offers live only in memory.
- **Pair UI:** the Phones view draws the QR from the offer object verbatim (`JSON.stringify(offer)`, no field added or dropped). `reachable_from_lan:false` shows the `--lan --host` instruction and never a QR. The countdown uses the offer's `expires_at`, so a browser clock far from the server's shows it early or late; the server still enforces the real expiry.
- **Lifetime (#72):** a pairing lasts `phone.device_ttl_days` (default 14) from `paired_at` (computed at check time; unreadable `paired_at` = expired). Expired devices are dropped via `phone._drop` (same path as revoke: store, live socket, UI tokens) with audit `phone.expired`, at auth time, at startup and hourly (`run_expiry_sweeper`). `/api/phones` carries `expires_at`; the Phones view shows "expires in N days".
- **Confirm frames (#72):** `Session.confirm` emits `confirm_resolved {confirm_id, outcome: approved|denied|expired, by}` on every exit (`confirm_expired` is still sent on timeout for old clients); `lib/confirm.ts` settles inline and overlay cards, overlay cards self-remove after 4 s.
- **Store:** `DATA_DIR/phones.json` (0600) keeps only the SHA-256 of each secret. `GET /api/phones` lists devices; `DELETE /api/phones/{id}` revokes one and drops its live socket.
- **Connect:** `WS /phone/ws` with `Authorization: Device <id>:<secret>`, checked before `accept()`. The browser `Bearer` token is refused there, and a device credential is refused on `/ws` and `/api/*`.
- **UI token:** `POST /phone/ui-session` (Device auth, no body) → `{token, expires_at, ttl_seconds}` (default 12 h, `phone.ui_token_ttl_seconds`). Accepted exactly where the browser token is (`/api/*` Bearer, `/ws?token=`), never on `/phone/*`; Host/Origin checks unchanged. Store is in `webauth.py`, memory only, tied to the device: `phone.revoke()` calls `webauth.revoke_ui_tokens()`, which closes `/ws` sockets opened with it (a watcher task in `/ws` also closes at expiry). Audit `phone.ui_session` carries the device id, never the token. Tests `tests/web/test_phone_ui_session.py`.
- **Instead of Origin:** any `/phone/*` request that **carries** `Origin` is refused (403, or the websocket closes). Browsers always send it on websocket handshakes and cross-site POSTs, and phones never do.
- **Frames:** phone `hello{protocol, capabilities}` → `welcome` · server `execute{command,args}` → phone `result{ok,data | error_code,error}` (or `error`) · `ping`→`pong` · bad frame → `error{code,message}`. A frame over 64 KiB → error, then close. A late or invented `result` gets `error unknown_request`.
- **Confirmation relay (#50):** a pending `Session.confirm` is sent to every ready phone as `confirm_request` (request_id = confirmation id), and to a phone that says hello while one is open. Phone `confirm_reply{approve}` goes through `events.resolve_open` → the owning session's `resolve_confirm(by="phone")`. First answer wins (`resolve_confirm` refuses once the event is set, so a late approve cannot flip a denial). Unknown/late/duplicate id → `error unknown_request`; non-bool `approve` → `malformed_frame`; reply before hello → `not_ready`; none approves anything. `confirm_closed{outcome}` (approved/denied/expired) is sent from the tail of `confirm()`, i.e. on every exit (browser, phone, cancel, drop, timeout), only for requests that were relayed. A phone disconnecting does not deny. Confirmation ids are global (`events._confirm_ids`), not per session, since a phone answers by id alone. Setting `phone.confirm_notifications` (default true). Audit `phone.confirm_reply`. Tests `tests/web/test_confirm_relay.py`.
- **Notify relay (#62):** server → phone `notify{title≤120, body≤1000, kind: reminder|job, at}` (request_id unique, no reply). The tick is another process, so `jobs.deliver` calls `phone.outbox_append` (one call; swallows every error; no-op when `phone.forward_notifications` is off, default true) which appends to `STATE_DIR/phone_outbox.jsonl` (0600, flock on a separate never-replaced `.lock`; trimmed past 100 entries, dropping entries older than 10 min). `create_app`'s lifespan runs `phone.OutboxRelay` on a thread: polls every 2 s, skips when (inode, size, mtime) is unchanged, sends to every ready phone, drops entries older than 10 min, and ignores entries that existed at server start (no re-buzz on restart; a reminder that fires with no server running is desktop-only). `notify` jobs are `reminder`, agent jobs `job`; failures forward too. Tests `tests/web/test_notify_relay.py`.
- **Result shapes:** every command declares `result` (the fields of `data`, same field specs as `args`, plus `values` for a fixed set) in the JSON. On `ok:true` the server checks `data` strictly (missing, wrong type, out of range, unexpected field) and a mismatch raises `PhoneBadResult` (`code="bad_result"`, audited as `phone.bad_result` with the reason only, never the data). The socket stays up. A failed result (`ok:false`) is not checked against it. A new command must add its `result`, or `test_protocol_file_is_self_consistent` fails. The app's `PhoneProtocolJsonTest` only reads `args`, so it does not compare `result` yet.
- **API:** `phone.call(command, args, device=None, timeout=15)` blocks and returns `data` or raises a `PhoneError` subclass: not connected, ambiguous (several phones and no name: it refuses and lists them, never picks the first), rejected, timeout, disconnected, or failed. A drop, revoke or replacing connection fails pending calls at once. It only works inside the `kravein serve` process. Calling it on the event-loop thread raises. State-changing commands still need `safety.enforce()` in the calling skill.
- **TLS (#65):** `serve --lan` is HTTPS/WSS by default (`--no-tls` = plain HTTP, with the old open-wifi warning); loopback stays HTTP. `webauth.ensure_tls_cert(ip)` keeps an ECDSA P-256 self-signed cert (10 y, SAN = the bound IP) in `DATA_DIR/tls/` (key 0600, written first). It is reused while it parses and expiry is more than 30 days away — the SAN is *not* checked, so a changed LAN IP never regenerates it (the app pins the fingerprint, not the address). Otherwise (missing/corrupt/near expiry) it is regenerated, the log says so, and the fingerprint changes so phones must re-pair. The offer gains `scheme` and `cert_sha256` (lowercase hex SHA-256 of the DER; only when https; `v` unchanged); `app.state.cert_sha256` is set by `serve()`. The app pins that fingerprint instead of checking a hostname. The Phones view shows the short form next to the QR; the QR is still the offer JSON verbatim. Browsers warn once about the self-signed cert. Tests `tests/web/test_tls.py` (includes a real TLS socket whose DER hash equals the fingerprint).

## Confirmation policy (#39)
- **API:** `GET /api/safety/policy`, `POST /api/safety/policy {mode, seconds?}`, `POST /api/safety/auto/off`; all token-gated, call `safety.*` with `by="web"`, bad input is 400 with a message. The engine enforces every guardrail; the web layer only carries the owner's choice.
- **One write path:** `POST /api/settings` returns 400 for a `safety` that is not an object or that carries `confirmation_mode`/`allow_destructive`. The Settings list hides those two rows.
- **UI invariant:** the selector and banner show only what the server last answered (`parsePolicy` rejects malformed replies); no optimistic state. The banner counts down locally from `seconds_left` re-anchored on each answer, polls every 5 s, and re-asks the server when its own countdown hits 0. Another tab's change or an expiry shows within 5 s.
- `config._deep_merge` shares nested dicts with `DEFAULT_SETTINGS`, so writing the `safety` section mutates the defaults in-process; `test_policy_api.py` deep-copies them. Reported to the orchestrator (config.py is not ours).

## Design tokens (#54, "aurora")
Single source: `web/app/tokens.css` (CSS variables; Tailwind maps `bg-surface` etc. via `@theme inline`). No hex/rgba in components. Dark first; `prefers-color-scheme: light` overrides. The Android shell should copy these values.

| Token | Dark | Light |
|---|---|---|
| `--bg` / `--surface` / `--surface-raised` / `--surface-sunken` | `#04060c` / `#0b1019` / `#121a28` / `#070b13` | `#eef2f9` / `#f8faff` / `#ffffff` / `#e4eaf4` |
| `--border` / `--border-strong` | `#1b2638` / `#2b3a54` | `#d5deec` / `#b3c0d6` |
| `--glass` / `--glass-strong` / `--glass-border` | `rgba(14,21,34,.72)` / `rgba(18,27,43,.88)` / `rgba(140,180,255,.14)` | white .72 / .92 / `rgba(40,70,120,.14)` |
| `--blur` | 18px (+ saturate 140%) | same |
| `--text` / `--text-muted` / `--text-faint` | `#eaf0fb` / `#a6b3c8` / `#8493ab` | `#0c1424` / `#44536b` / `#566580` |
| `--accent` (cyan→teal) / `--accent-contrast` | `#3de3ff` / `#031016` | `#006b82` / `#ffffff` |
| `--accent-2` (violet) | `#9d8cff` | `#5a45d6` |
| `--gradient-accent` | `135deg #3de3ff → #7b8cff` | `135deg #006b82 → #4a47d0` |
| `--glow` / `--glow-soft` | 1px accent-line ring + 18px cyan halo / 14px halo | hairline ring / none |
| danger `--moderate` / `--destructive` / `--critical` | `#ffc94d` / `#ff8f4a` / `#ff6b7a` | `#8a5a00` / `#a63f06` / `#b3182f` |
| radii `--radius-sm/--radius/-md/-lg/-xl` | 8 / 12 / 14 / 18 / 24 px, `-full` 999 | same |
| type | Inter stack (never `system-ui`); xs .6875 sm .8125 base .9375 lg 1.125 xl 1.5 2xl 2 stat 2.125 rem; label tracking .1em | same |
| motion | fast 110 / base 200 / slow 360 ms; `--ease` (.2,0,.2,1), `--ease-out` (.16,1,.3,1) | same |

- Classes (globals.css, `@layer components` so Tailwind utilities still win): `.glass`, `.glass-strong`, `.glow`, `.text-gradient`, `.btn-primary`, `.field-glow`. Keyframes `think` (agent thinking dots, `Thinking` in `TurnGraph.tsx`), `halo`, `node-in`, `view-in`. The reduced-motion rule in globals.css zeroes all animation.
- Contrast: `node web/scripts/check_contrast.mjs` reads tokens.css and fails below WCAG AA (4.5:1) for every text pair in both themes; run it after any token change.
- Why `@layer components`: an unlayered `.glass { border }` silently overrode every Tailwind utility (`border-x-0`, `hover:bg-*`) on the same element.

## Gotchas
- **Phone width (<640px, Android WebView):** the rail is `hidden sm:flex`; `NavDrawer` (Shell.tsx, opened by the top bar's menu button) replaces it. Touch targets are enforced by one `@media (max-width: 639px)` rule in `globals.css` (min 40px on buttons/inputs), not per component. `layout.tsx` sets `interactiveWidget: "resizes-content"` so the on-screen keyboard shrinks the layout instead of covering the composer. Desktop is unchanged above the breakpoint.
- No `from __future__ import annotations` in `server.py`. Every request gets a reply frame. Confirmation frames are never dropped.
- Never pass `host=` to `audit.record`: it overwrites the record's own machine-name field.
- A test that expects a refused phone socket must probe it (`ping`); an accepted socket waits for `hello` forever, so a plain `receive_text()` hangs instead of failing.
