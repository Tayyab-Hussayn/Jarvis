---
type: module
owner: android
updated: 2026-10-24
---

# android

## Code map
Package `com.kravein.connect` under `android/app/src/main/java/com/kravein/connect/`:
- `protocol/` — pure Kotlin copy of `kravein/phone_protocol.json`: `Vocabulary.kt` (`PhoneProtocol`, `ErrorCode`, `Field`/`FrameSpecs`, strict `FieldValidator`), `FrameCodec.kt` (decode/validate, builders for `hello`/`result`/`pong`/`error`, `CommandResult`).
- `commands/` — `CommandRouting.kt`: `DeviceAction` (the commands + arg specs), `CommandRouter` (validates, never runs unknown/invalid), `resolveApp` (refuses ambiguous app names). `DeviceCommandHandler` is the Android backend (`DeviceBackend`).
- `network/` — `PhoneSession` (pure per-connection state machine), `ConnectionPolicy` (backoff, refused-credential → `NEEDS_PAIRING`), `PhoneClient` (OkHttp websocket glue), `PairingClient` (`POST /phone/pair`).
- `core/` — `AppModels.kt` (`PairingOfferParser`, `DeviceCredential`, `ConnectionState`), `AgentStateStore` (UI state; no secrets).
- `network/Http` — the one shared `OkHttpClient`; pairing, `UiSessionClient` (`POST /phone/ui-session` → `UiToken`, 401 = `Refused`) and the phone socket derive from it.
- `web/` — `WebPolicies.kt` (pure: `UiTokenHolder` in-memory token cache with refresh margin, `NavigationPolicy` paired-origin allow-list + token-free URL rebuild, `PagePermissionPolicy` mic/camera, `WebViewLifecyclePolicy` create-on-first-show / destroy after 5 min idle); `KraveinWebController` (the hardened WebView, 401 → new token + reload, Offline/NeedsPairing state, driven by Activity `onStart/onStop`).
- `ui/theme/` — `KraveinTokens.kt` (pure: the web aurora palette dark+light, radii, type scale, `background(dark)`, `NOTIFICATION_ACCENT`, WCAG `contrast`), `Theme.kt` (`KraveinConnectTheme` follows the system theme; `GradientButton`, `glassBorder`, `KraveinTheme.colors` for glass/danger). Res: `values{,-night}/colors.xml` + `themes.xml` (window ground = web `--bg`), `values-v31` splash attrs, `mipmap-anydpi-v26/ic_launcher` (adaptive + monochrome), `drawable/ic_stat_kravein` (status-bar glyph).
- `ui/MainShell.kt` — bottom nav Kravein (WebView) · Phone (`ConnectedScreen`) · Settings (server, permissions, unpair, about); `ui/KraveinConnectApp.kt` holds onboarding and wires the shell.
- `confirm/` — `Confirmations.kt` (pure: `PendingConfirmation`, `ConfirmationBook` open questions that outlive a socket, `ConfirmationText`, `ReplyOutcome`, `ConfirmationListener`); `ConfirmationNotifier` (HIGH channel `kravein_confirmations`, Approve/Decline via `PendingIntent.getForegroundService`, body opens the Kravein tab through `core/ShellNavigation`).
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

## Web UI tab (#44)
- `POST /phone/ui-session` with the device header → `{token, expires_at, ttl_seconds}`; the page loads at `/?token=…`. The token is memory-only; the web UI moves it into sessionStorage and strips it from the URL, so `lastUrl` kept for rebuilds is token-free.
- Hardening: JS on, no `addJavascriptInterface`, file/content access off, `shouldOverrideUrlLoading` sends other hosts to the system browser, `shouldInterceptRequest` answers 403 for any sub-resource outside the paired origin. Mic/camera only for the paired origin and only with the Android permission held (else it asks, the page retries).
- Cleartext: the paired host is only known at runtime, so a static network-security-config cannot name it; the manifest keeps `usesCleartextTraffic` (LAN IP, TLS is #22) and the WebView enforces paired-origin-only. WebSocket traffic from the page is not visible to `shouldInterceptRequest`.
- Lifecycle: WebView built when the Kravein tab is first composed; `onStop` pauses it and schedules destruction after 5 min; `onStart` destroys it if the limit passed. The three.js `HolographicBackground` (a second WebView) shows on onboarding screens only.
- Refresh: token reloaded on Retry/Reload, on a main-frame 401 (once, then "pair again"), and on return to the foreground when the cached token is inside its 60 s margin. No timer polls.

## Confirmation notifications (#51)
- Frames: `confirm_request` (action, detail, danger destructive|critical, expires_at) → notification; `confirm_reply{approve}` back with the same request_id; `confirm_closed{outcome}` removes it. A phone that connects while one is pending also receives it (server side, #52).
- A tap goes notification → `KraveinConnectForegroundService` (`ACTION_CONFIRM_REPLY`) → `PhoneClient.sendConfirmReply` → `PhoneSession.planReply`. A frame is built only on the welcomed live socket, for an open, unexpired id; the book entry is closed once it was sent, so a second tap sends nothing. Otherwise the notification becomes "Not sent – PC unreachable" / "Expired – not sent". Missing `approve` extra never reads as approval.
- The server answers a late/duplicate/settled reply with `error unknown_request`; the session turns that (for an id we answered) into `ReplyRejected` → "Not accepted", not a session error.
- Notification expiry: `setTimeoutAfter(expires_at - now)`; a request already expired on arrival is dropped.
- POST_NOTIFICATIONS: asked once (setup screen explains why; `UiPrefs.notificationsPrompted`); refusal never blocks setup or the service.

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
