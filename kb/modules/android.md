---
type: module
owner: android
updated: 2026-10-25
---

# android

## Code map
Package `com.kravein.connect` under `android/app/src/main/java/com/kravein/connect/`:
- `protocol/` — pure Kotlin copy of `kravein/phone_protocol.json`: `Vocabulary.kt` (`PhoneProtocol`, `ErrorCode`, `Field`/`FrameSpecs`, strict `FieldValidator`), `FrameCodec.kt` (decode/validate, builders for `hello`/`result`/`pong`/`error`, `CommandResult`).
- `commands/` — `CommandRouting.kt`: `DeviceAction` (the commands + arg specs), `CommandRouter` (validates, never runs unknown/invalid), `resolveApp` (refuses ambiguous app names). `DeviceCommandHandler` is the Android backend (`DeviceBackend`).
- `network/` — `PhoneSession` (pure per-connection state machine), `ConnectionPolicy` (backoff, refused-credential → `NEEDS_PAIRING`), `PhoneClient` (OkHttp websocket glue), `PairingClient` (`POST /phone/pair`). `LoopbackTlsPipe` (WebView → loopback → pinned TLS to the PC), `PinnedHttp`/`PinTrustManager`. `HostRecovery` (pure) + `NsdPcDiscovery` (NsdManager) re-find the PC after an IP change.
- `core/` — `AppModels.kt` (`PairingOfferParser`, `DeviceCredential`, `ConnectionState`), `AgentStateStore` (UI state; no secrets).
- `network/Http` — the one shared `OkHttpClient`; pairing, `UiSessionClient` (`POST /phone/ui-session` → `UiToken`, 401 = `Refused`) and the phone socket derive from it.
- `web/` — `WebPolicies.kt` (pure: `UiTokenHolder` in-memory token cache with refresh margin, `NavigationPolicy` paired-origin allow-list + token-free URL rebuild, `PagePermissionPolicy` mic/camera, `VoiceSessionPolicy` (mic active → audio focus; open mic at background → stop) in `VoiceSessionPolicy.kt`, `WebViewLifecyclePolicy` create-on-first-show / destroy after 5 min idle); `KraveinWebController` (the hardened WebView, 401 → new token + reload, Offline/NeedsPairing state, driven by Activity `onStart/onStop`).
- `ui/theme/` — `KraveinTokens.kt` (pure: the web aurora palette dark+light, radii, type scale, `background(dark)`, `NOTIFICATION_ACCENT`, WCAG `contrast`), `Theme.kt` (`KraveinConnectTheme` follows the system theme; `GradientButton`, `glassBorder`, `KraveinTheme.colors` for glass/danger). Res: `values{,-night}/colors.xml` + `themes.xml` (window ground = web `--bg`), `values-v31` splash attrs, `mipmap-anydpi-v26/ic_launcher` (adaptive + monochrome), `drawable/ic_stat_kravein` (status-bar glyph).
- `chat/` (#109, native Chat, speaks `/ws` exactly like `web/lib/useKravein.ts`) — pure: `ChatFrames` (strict parse of server frames, builders; unknown frame kinds -> `Ignored`, bad ones -> `Malformed` and dropped), `ChatModel`, `ChatReducer` (turn/timeline state machine: `confirm_resolved` closes cards, `onDisconnected` turns running cards into UNKNOWN and denies pending confirms, cancel flag cleared by `cancelled`), `ChatTimeline` (rows for the connecting-line), `ChatRetry` (backoff 0.5 s..15 s), `Markdown` (bold/code/lists/fences/http(s) links only). Glue: `ChatClient` (UI token via `UiSessionClient`, `WS /ws?token=` over `PinnedHttp`, ping 25 s, reconnect; 401 on ui-session = unpaired), `ChatHub` (process singleton; socket open only while app visible AND paired; shared-text prefill).
- `ui/ChatScreen.kt` — LazyColumn timeline with the drawn connecting line, pulse on live nodes (off when animator scale = 0), tool cards, inline confirm card, Stop, composer. `ui/MainShell.kt` — bottom nav Chat (default) · Phone · More · Settings; `ui/KraveinConnectApp.kt` holds onboarding and wires the shell.
- `web/WebActivity` (+ `ui/WebScreen.kt`, `WebProcessPolicy`) — the WebView in `android:process=":web"`, opened from the More tab; the process kills itself 2 min after the activity stops (the only way to free Chromium's renderer).
- `confirm/` — `Confirmations.kt` (pure: `PendingConfirmation`, `ConfirmationBook` open questions that outlive a socket, `ConfirmationText`, `ReplyOutcome`, `ConfirmationListener`); `ConfirmationNotifier` (HIGH channel `kravein_confirmations`, Approve/Decline via `PendingIntent.getForegroundService`, body opens the Kravein tab through `core/ShellNavigation`).
- `notice/` — `Notice.kt` (pure: `Notice`, `NoticeKind` reminder|job, `NoticeDedupe`, `NoticeListener`), `NoticeNotifier` (DEFAULT channel `kravein_reminders` "Kravein reminders", tap → Kravein tab).
- `web/ShareText.kt` (pure: clean/cap/encode shared text), `tile/` (`TileModel` pure, `KraveinTileService`).
- `pairing/PairingStorage` — encrypted credential. `MainActivity`, `KraveinConnectForegroundService`, `accessibility/`.
- `android/gradle/libs.versions.toml` — version catalog (AGP 9.4.1, Kotlin 2.4.21, same as `~/code/smart-sticky-wallpaper`).
- Tests: `android/app/src/test/java/com/kravein/connect/{protocol,commands,network,pairing,web,confirm,ui}/`.

## Verify
```bash
android/gradlew -p android :app:assembleDebug       # -> android/app/build/outputs/apk/debug/app-debug.apk
android/gradlew -p android :app:testDebugUnitTest
android/gradlew -p android :app:assembleRelease     # R8 + shrinkResources, debug-signed; first run needs network (compose-group-mapping)
```
Needs gitignored `android/local.properties` with `sdk.dir=/home/krawin/Android/Sdk` (create it in a fresh worktree). The first build downloads libraries from Maven (~8 min); later ones hit `~/.gradle`.

## Protocol notes (implements `kravein/phone_protocol.json`, owned by web)
- QR offer: `{v, service:"kravein", host, port, code, expires_at}` → `PairingOfferParser` rejects anything missing, wrong-typed, expired, or with a host that could redirect the request. Manual entry (host/port/code) goes through the same parser. The offer is never stored.
- Pair: `POST http://host:port/phone/pair {code, device_name}` → `{device_id, device_secret, name}`; 403 = code used/expired. Cleartext HTTP/WS on the LAN until TLS (#22).
- Connect: `WS /phone/ws`, header `Authorization: Device <id>:<secret>`, no `Origin`. Phone sends `hello{protocol, capabilities, device_name, os_version, app_version}`; capabilities = every `DeviceAction` wire name (a capability IS a command name). `execute` before `welcome` → `error not_ready`.
- Frames are strict like the server: unknown/missing payload fields are errors; the app answers a bad frame with an `error` frame and never runs it. Unknown command → `error unknown_command`; bad args → `result{ok:false, error_code:invalid_args}`.
- Refused credential: the server closes before accepting, OkHttp reports HTTP 401/403 on the handshake → state `NEEDS_PAIRING`, no more reconnects, UI goes to the pairing screen. The stored credential is kept until a new pairing replaces it (a 403 can also be a Host-check failure; do not destroy a good secret over it).
- Result data (JSON only says "object"): `get_battery {percent, charging}`, `volume_get/set {percent}`, `get_device_info {model, os_version, app_version}`, `flashlight_* {flashlight}`, `launch_app {package}`, `open_url {url}`.
- Vocabulary guard: `PhoneProtocolJsonTest` reads the JSON via the `kravein.protocol.json` system property (set in `app/build.gradle.kts`, also a task input) and fails if commands, args, frames, error codes or limits differ.

## Web UI tab (#44; since #109 it is the More tab, in the `:web` process)
- `POST /phone/ui-session` with the device header → `{token, expires_at, ttl_seconds}`; the page loads at `/?token=…`. The token is memory-only; the web UI moves it into sessionStorage and strips it from the URL, so `lastUrl` kept for rebuilds is token-free.
- Hardening: JS on, no `addJavascriptInterface`, file/content access off, `shouldOverrideUrlLoading` sends other hosts to the system browser, `shouldInterceptRequest` answers 403 for any sub-resource outside the paired origin. Mic/camera only for the paired origin and only with the Android permission held (else it asks, the page retries).
- Cleartext: the paired host is only known at runtime, so a static network-security-config cannot name it; the manifest keeps `usesCleartextTraffic` (LAN IP, TLS is #22) and the WebView enforces paired-origin-only. WebSocket traffic from the page is not visible to `shouldInterceptRequest`.
- Lifecycle: WebView built when the Kravein tab is first composed; `onStop` pauses it and schedules destruction after 5 min; `onStart` destroys it if the limit passed. The three.js `HolographicBackground` (a second WebView) shows on onboarding screens only.
- Refresh: token reloaded on Retry/Reload, on a main-frame 401 (once, then "pair again"), and on return to the foreground when the cached token is inside its 60 s margin. No timer polls.

## Live voice on the phone (#92)
- Web page (#91) streams PCM over `/ws`; the app only provides `getUserMedia` and the speaker. `onPermissionRequest` grants AUDIO_CAPTURE only for the paired/pipe origin and only with RECORD_AUDIO held; otherwise `deny()` (page sees NotAllowedError). A missing permission first shows a rationale dialog (`mic_rationale_*`), then the system prompt; "Not now" leaves the page with the clean error.
- Audio focus (`USAGE_ASSISTANT`/speech, `GAIN_TRANSIENT`) is taken when `AudioRecordingCallback` reports this app recording and released when it stops or the WebView is destroyed. Replies play on the media stream (speaker, or headset/BT when connected).
- Background: the simple safe choice, no notification-backed session. `onStop` with an open mic loads `about:blank` (closes capture); `onStart` reloads the page.
- Device-verified 2026-10-24 (Vivo V2254, `kravein serve --lan` TLS): rationale + system prompt, refusal → clean page error, `web.voice_start audio=device` + `voice.live_start` in the audit, focus on the stack while recording (`dumpsys audio`), Home → rec stop + focus released + `web.voice_stop`, and the owner heard Kravein's reply from the phone speaker. A transient Live `1011` once hit the first attempt (server side).

## Finding the PC again (#106)
- Server advertises `_kravein._tcp` (TXT `fp` = first 16 hex of the cert SHA-256, `v=1`). `network/HostRecovery.kt` is pure: `candidates()` keeps services with `v=1`, a matching `fp` (case-insensitive), a usable host/port and a different address than the stored one. A credential **without a pin never gets candidates** (nothing to match, and a broadcast must not redirect it).
- `HostRecovery` holds the saved credential and one `trial`; `trialSucceeded()` is the only way the saved host changes, `trialFailed()` drops that address for the current search and falls back to the stored one, `networkChanged()` forgets rejections. mDNS only suggests: the full pin is still checked by TLS (`PinMismatch` on a trial is just a failed trial, never a stop).
- `PhoneClient`: first stored-host failure (`Retry`) → `startSearch()` (`NsdPcDiscovery`, platform `NsdManager`, no dependency); a found service is tried at once; on `Ready` the new host goes to `HostListener` (service: encrypted `PairingStorage`, only if the same `deviceId` is still paired, then `AgentStateStore.setCredential`) and the search stops. `networkChanged()` (service `registerDefaultNetworkCallback.onAvailable`) re-tries the stored host immediately and restarts the search; ignored while connected.
- UI: `AgentStateStore.discovery` (IDLE/SEARCHING/NOT_FOUND after 10 s) → Phone tab shows `DiscoveryText.SEARCHING` and, when not found, the same-Wi-Fi hint. The Kravein tab rebuilds the WebView/pipe when host/port change (`KraveinWebController.endpointChanged`, `key(host, port)`).
- Not verified on a device yet (needs the server side, engine, in parallel).

## Confirmation notifications (#51)
- Frames: `confirm_request` (action, detail, danger destructive|critical, expires_at) → notification; `confirm_reply{approve}` back with the same request_id; `confirm_closed{outcome}` removes it. A phone that connects while one is pending also receives it (server side, #52).
- A tap goes notification → `KraveinConnectForegroundService` (`ACTION_CONFIRM_REPLY`) → `PhoneClient.sendConfirmReply` → `PhoneSession.planReply`. A frame is built only on the welcomed live socket, for an open, unexpired id; the book entry is closed once it was sent, so a second tap sends nothing. Otherwise the notification becomes "Not sent – PC unreachable" / "Expired – not sent". Missing `approve` extra never reads as approval.
- The server answers a late/duplicate/settled reply with `error unknown_request`; the session turns that (for an id we answered) into `ReplyRejected` → "Not accepted", not a session error.
- Notification expiry: `setTimeoutAfter(expires_at - now)`; a request already expired on arrival is dropped.
- POST_NOTIFICATIONS: asked once (setup screen explains why; `UiPrefs.notificationsPrompted`); refusal never blocks setup or the service.

## TLS pin (#66, closes #22 on the app side)
- Offer: optional `scheme` (`http` default | `https`) + `cert_sha256`. https without a well-formed 64-hex fingerprint, any other scheme, or a non-string value is rejected; the pin is normalised to lower-case hex (`CertPin.normalize` also accepts `AA:BB:…`). A pin on an http offer is dropped. Manual entry is always http.
- `DeviceCredential` and the encrypted credential carry `scheme` + `certSha256`; entries stored before TLS read back as plain http.
- `network/PinnedHttp.clientFor(scheme, pin)`: http → the shared plain client; https → one cached client per pin whose `PinTrustManager` accepts only a leaf whose DER SHA-256 equals the pin (no CA, no expiry check, no client certs) and whose hostname verifier is replaced by the pin (the server is an IP). That relaxed verifier exists only on that client. Pair, `/phone/ws` (derived from it with the long timeouts) and `/phone/ui-session` all use it.
- A different certificate throws `PinMismatchException`; `TlsFailures.isPinMismatch` walks the cause chain. The socket stops (state `NEEDS_PAIRING`, `MISMATCH_MESSAGE`) instead of retrying; pairing and ui-session show the same message.
- WebView over TLS (#70): Chromium rejects the self-signed certificate on sub-resources and websockets (`net_error -202`) whatever `onReceivedSslError` does, and a WebView cannot take a trust manager. So `network/LoopbackTlsPipe` listens on `127.0.0.1:<random>` while the WebView exists; each connection opens a socket through `PinnedHttp.socketFactoryFor(pin)` and copies bytes both ways (HTTP + websocket upgrade untouched). The WebView loads `http://127.0.0.1:<port>/?token=…`; `webPolicy()` builds the `NavigationPolicy` for that origin; the server already allows `127.0.0.1` in Host/Origin. There is no SSL bypass in the app any more (`SslErrorPolicy` and `onReceivedSslError` are gone; the default cancels). A different certificate fails the handshake and the WebView connection just closes. Other apps on the phone can reach the loopback port but still need the UI token. Plain-http credentials load directly, no pipe. After a WebView rebuild the port changes, so the remembered `lastUrl` no longer matches and the tab returns to `/`.
- Pre-TLS credential vs https server: a plain-http request to a TLS port shows up as EOF / reset / bad status line, a downed server as refused/timeout. After 3 consecutive such failures on an unpinned credential `ConnectionPolicy` returns `NeedsTlsRepair` → `NEEDS_PAIRING` with "The PC now uses a secure connection…". Heuristic; the stored credential is kept until a new pairing replaces it.
- Tests: `PinnedHttpTest` runs a real JDK `HttpsServer` with a keytool PKCS12 (`src/test/resources/pin-test.p12`, `pin-other.p12`, password `changeit`, test-only) for match / mismatch / plain-client-refused.
- Device-verified 2026-10-24 (Vivo V2254, `kravein serve --lan` TLS): pairing, phone socket, WebView UI, live chat websocket, `phone_status`, confirmation Approve/Decline, share, reminder; the real EC P-256 certificate pins fine.

## Reminder notifications (#63)
- Frame `notify` (server → phone, no reply): `title` ≤ 120, `body` ≤ 1000, `kind` reminder|job, `at` unix s. Strict like every frame; unknown `kind` → `error malformed_frame`; before `welcome` → `not_ready`.
- Dedupe by `request_id` in `NoticeDedupe` (200 ids, oldest dropped), owned by `PhoneClient` so a replay after reconnect is also dropped. A repeat produces no event and no error frame.
- Own channel, separate from confirmations (muting one leaves the other). Needs POST_NOTIFICATIONS like confirmations; without it nothing shows.
- `PhoneProtocolJsonTest.framesMatch` checks `notify` against `kravein/phone_protocol.json` (web #64).
- Not verified on a device.

## Share + Quick Settings tile (#59)
- Share target: `MainActivity` has a `SEND text/plain` filter. `ShareText.clean` trims, drops blank, caps at 4000 chars (never splits a surrogate pair); `NavigationPolicy.authenticatedUrl(last, token, ask)` adds `ask=` (percent-encoded, `%20` for spaces) next to `token=`. `KraveinWebController.shareText` keeps the text in memory until a page load carries it once; a failed load keeps it for Retry.
- `ask` and `token` are both stripped from the remembered `lastUrl`, so a rebuilt page never refills the composer. The shared text is never logged or stored; the extra is removed from the intent after reading, and `onCreate` only reads the intent when `savedInstanceState == null`.
- Not paired: a Toast ("Pair this phone with Kravein first…"); the pairing screen is already showing.
- Tile `tile/KraveinTileService` (+ pure `TileModel`): mirrors `AgentStateStore` only between `onStartListening`/`onStopListening` (no polling); tap opens `MainActivity` with `ACTION_OPEN_KRAVEIN` (PendingIntent overload on Android 14+). Subtitles need API 29.
- Not verified on a device (no emulator here): share sheet entry, tile appearance in the shade.

## Design (#56)
- Source of truth is `web/app/tokens.css`; `KraveinTokensTest` parses it (system property `kravein.tokens.css`, a task input) and fails when `KraveinTokens` or the `colors.xml` values drift. It also asserts WCAG AA on the text pairs in both themes. When web changes a token, change `KraveinTokens.kt` and `colors.xml` in the next android PR (the test tells you).
- Dark first, light follows the system. Onboarding screens sit over the three.js hologram (`assets/web_background`, recoloured to cyan/violet on the web bg) and always use the dark palette; the shell uses the system theme. The WebView and window ground equal `--bg`, so splash, WebView and page do not flash.
- Splash: platform SplashScreen attrs in `values-v31` (no `core-splashscreen` dependency); below Android 12 `windowBackground` shows. Font: platform sans (Inter not bundled, size).
- Not done: Inter font, blur (Compose has no cheap backdrop blur; glass is a translucent fill + hairline).

## Pair against a local server
`.venv/bin/python -m kravein serve --lan` → get an offer with the browser token (`POST /api/phone/offer`; render its JSON as a QR) → scan it in the app (or enter host/port/code). `GET /api/phones` lists the device; `DELETE /api/phones/{id}` revokes it and the app shows the pairing screen.

## Gotchas
- Moved from `brahma-connect-android/` and renamed 2026-10-22; Gradle 8.7/AGP 8.5.2/SDK 35 became Gradle 9.8.1/AGP 9.4.1/compileSdk 37/targetSdk 36/build-tools 36.0.0.
- AGP 9 has Kotlin built in: no `org.jetbrains.kotlin.android` plugin and no `kotlinOptions {}`. The Compose compiler plugin is still applied.
- `android.jar`'s `org.json` is a stub that throws in JVM tests; `testImplementation(libs.json)` supplies the real one.
- A KDoc/comment containing `/*` (e.g. "/phone/*") opens a nested comment in Kotlin and breaks the file ("Unclosed comment").
- `gradlew` must keep its executable bit; `.gradle/` build cache had been committed before the move and is now gitignored.
- Old gateway protocol (`kravein_connect`: pair_request/authenticate/chat/mDNS discovery) is gone; stale `device_credential`/`last_pairing_offer` prefs are wiped on first start.

## Gotchas (device)
- `:web` is a second process with its own copy of every singleton (`AgentStateStore`, `ChatHub`): `WebActivity` reads the credential from `PairingStorage` itself. Unpair does not reach it; it exits when started without a credential.
- Tool results carry no failure flag on the wire, so a chat tool card is RUNNING / DONE / UNKNOWN (lost on disconnect); `ToolStatus.FAILED` exists but nothing sets it yet.
- A WebView with no `layoutParams` is measured wrap-content-like and every `vh`/`dvh` unit is 0, so the web UI's `h-dvh` root collapsed and the tab was blank (DOM full, height 0). `build()` sets MATCH_PARENT.
- Closing an `SSLSocket` writes close_notify = network I/O. `LoopbackTlsPipe.close()` runs from `onDestroy` on the main thread, so it closes the sockets on a background thread (StrictMode crash `NetworkOnMainThreadException` otherwise).
- Debug builds enable WebView debugging (`FLAG_DEBUGGABLE` only): `adb forward tcp:9333 localabstract:webview_devtools_remote_<pid>`, then `curl localhost:9333/json` and the CDP websocket read the page console/DOM.
- adb tips: `adb shell 'am start … --es k "two words"'` (quote the whole command); notification actions: `service call statusbar 1`, expand the group, expand the item.
- Two processes must never share Chromium's data directory: the onboarding `HolographicBackground` is a WebView in the main process, so `WebActivity` calls `WebView.setDataDirectorySuffix("kravein_web")` before its first WebView (else `:web` dies with "Using WebView from more than one process at once" and the app drops to the launcher). `inShell` also counts "no route yet" so a paired launch never starts Chromium in the main process (main PSS 176 -> 115 MB).
- Keyboard: `MainActivity` sets `setDecorFitsSystemWindows(false)` + `adjustResize` so Compose gets the IME inset; `MainShell` hides the bottom nav while it is up (on the Vivo the system otherwise resized the window and the nav rode above the keys). The timeline list uses `animateItem(fadeOutSpec = null)`: the fading "Working…" row stayed drawn under the answer.
- Device pass #109 (Vivo V2254, USB, TLS): timeline with a tool call, confirm Decline (file kept) and Approve (trash failed server-side, then the CRITICAL permanent card, approved, file gone), Wi-Fi off/on -> "Offline - retrying" + Retry now -> Connected with the timeline intact, More -> `:web` process (206 MB) ends the moment you leave it. After a decline the model tries workarounds (`permanent=True`, `rm` via `run_command`) until the 8-round cap; every one still asks.
- `adb tcpip 5555` is refused by the auto-mode classifier (exposes a service); wireless adb needs the owner to run it.
- Memory (dumpsys meminfo, TOTAL PSS): Chat only 115 MB (no `:web` process); More -> web: main 107 MB + `:web` 206 MB. Before #109: Kravein tab open 209.6 MB; 6 min in background 162.9 MB (94 MB of it swapped). The WebView object is destroyed (devtools lists no pages) but Chromium keeps its sandboxed renderer process (~130 MB) alive; not fixed.
