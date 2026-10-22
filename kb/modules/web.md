---
type: module
owner: web
updated: 2026-10-22
---

# web

## Code map
- `web/` Next.js app (screens, `TurnGraph`, `Screen`) → `npm run build` → `kravein/web-dist/` · `kravein/server.py` FastAPI + ws · `events.py` Session frames · `webauth.py` token/Origin/Host
- `kravein/phone.py` phone channel: offers, device store, `PhoneLink`, `call()` · `kravein/phone_protocol.json` the phone vocabulary · tests in `tests/web/test_phone_channel.py`

## Verify
```bash
.venv/bin/python -m pytest tests/web tests/test_web_transport.py -q     # 42 + 87
cd web && npm run build
.venv/bin/python tools/check_layout.py     # only when the UI changed
```

## Phone channel
The Android app is a device Kravein controls. It connects to the same server and port as the browser, under the same Host rules (`--lan` adds the served IP literal, never a hostname).

- **Vocabulary:** `kravein/phone_protocol.json` is the only definition of frames, commands and arguments. Envelope `{type, request_id, payload}`. A capability name **is** a command name (`get_battery`, not `battery`). The server validates every inbound frame and every outbound `execute`, and the app implements exactly this file.
- **Pair:** the owner calls `POST /api/phone/offer` (browser token) and gets `{v, service, host, port, code, expires_at}`, shown as a QR. The phone calls `POST /phone/pair {code, device_name}` and gets `{device_id, device_secret}`, once. Each offer is single-use and lasts 120 s (`phone.offer_ttl_seconds`). A new offer replaces the old one. A wrong code doesn't burn the offer, so nobody can cancel someone else's pairing. Offers live only in memory.
- **Store:** `DATA_DIR/phones.json` (0600) keeps only the SHA-256 of each secret. `GET /api/phones` lists devices; `DELETE /api/phones/{id}` revokes one and drops its live socket.
- **Connect:** `WS /phone/ws` with `Authorization: Device <id>:<secret>`, checked before `accept()`. The browser `Bearer` token is refused there, and a device credential is refused on `/ws` and `/api/*`.
- **Instead of Origin:** any `/phone/*` request that **carries** `Origin` is refused (403, or the websocket closes). Browsers always send it on websocket handshakes and cross-site POSTs, and phones never do.
- **Frames:** phone `hello{protocol, capabilities}` → `welcome` · server `execute{command,args}` → phone `result{ok,data | error_code,error}` (or `error`) · `ping`→`pong` · bad frame → `error{code,message}`. A frame over 64 KiB → error, then close. A late or invented `result` gets `error unknown_request`.
- **API:** `phone.call(command, args, device=None, timeout=15)` blocks and returns `data` or raises a `PhoneError` subclass: not connected, ambiguous (several phones and no name: it refuses and lists them, never picks the first), rejected, timeout, disconnected, or failed. A drop, revoke or replacing connection fails pending calls at once. It only works inside the `kravein serve` process. Calling it on the event-loop thread raises. State-changing commands still need `safety.enforce()` in the calling skill.
- **No TLS** (risk accepted for now): the secret crosses the LAN in plain text on every connect, the same exposure as the browser token under `--lan`. WPA home network only. Follow-up: a self-signed cert with its fingerprint pinned in the QR.

## Gotchas
- No `from __future__ import annotations` in `server.py`. Every request gets a reply frame. Confirmation frames are never dropped.
- Never pass `host=` to `audit.record`: it overwrites the record's own machine-name field.
- A test that expects a refused phone socket must probe it (`ping`); an accepted socket waits for `hello` forever, so a plain `receive_text()` hangs instead of failing.
