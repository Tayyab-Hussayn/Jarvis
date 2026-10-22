---
type: module
owner: android
updated: 2026-10-22
---

# android

## Code map
Package `com.kravein.connect` under `android/app/src/main/java/com/kravein/connect/`:
- `protocol/` — pure Kotlin copy of `kravein/phone_protocol.json`: `Vocabulary.kt` (`PhoneProtocol`, `ErrorCode`, `Field`/`FrameSpecs`, strict `FieldValidator`), `FrameCodec.kt` (decode/validate, builders for `hello`/`result`/`pong`/`error`, `CommandResult`).
- `commands/` — `CommandRouting.kt`: `DeviceAction` (the commands + arg specs), `CommandRouter` (validates, never runs unknown/invalid), `resolveApp` (refuses ambiguous app names). `DeviceCommandHandler` is the Android backend (`DeviceBackend`).
- `network/` — `PhoneSession` (pure per-connection state machine), `ConnectionPolicy` (backoff, refused-credential → `NEEDS_PAIRING`), `PhoneClient` (OkHttp websocket glue), `PairingClient` (`POST /phone/pair`).
- `core/` — `AppModels.kt` (`PairingOfferParser`, `DeviceCredential`, `ConnectionState`), `AgentStateStore` (UI state; no secrets).
- `pairing/PairingStorage` — encrypted credential. `ui/` Compose screens. `MainActivity`, `KraveinConnectForegroundService`, `accessibility/`.
- `android/gradle/libs.versions.toml` — version catalog (AGP 9.4.1, Kotlin 2.4.21, same as `~/code/smart-sticky-wallpaper`).
- Tests: `android/app/src/test/java/com/kravein/connect/{protocol,commands,network,pairing}/`.

## Verify
```bash
android/gradlew -p android :app:assembleDebug       # -> android/app/build/outputs/apk/debug/app-debug.apk
android/gradlew -p android :app:testDebugUnitTest
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

## Pair against a local server
`.venv/bin/python -m kravein serve --lan` → get an offer with the browser token (`POST /api/phone/offer`; render its JSON as a QR) → scan it in the app (or enter host/port/code). `GET /api/phones` lists the device; `DELETE /api/phones/{id}` revokes it and the app shows the pairing screen.

## Gotchas
- Moved from `brahma-connect-android/` and renamed 2026-10-22; Gradle 8.7/AGP 8.5.2/SDK 35 became Gradle 9.8.1/AGP 9.4.1/compileSdk 37/targetSdk 36/build-tools 36.0.0.
- AGP 9 has Kotlin built in: no `org.jetbrains.kotlin.android` plugin and no `kotlinOptions {}`. The Compose compiler plugin is still applied.
- `android.jar`'s `org.json` is a stub that throws in JVM tests; `testImplementation(libs.json)` supplies the real one.
- A KDoc/comment containing `/*` (e.g. "/phone/*") opens a nested comment in Kotlin and breaks the file ("Unclosed comment").
- `gradlew` must keep its executable bit; `.gradle/` build cache had been committed before the move and is now gitignored.
- Old gateway protocol (`kravein_connect`: pair_request/authenticate/chat/mDNS discovery) is gone; stale `device_credential`/`last_pairing_offer` prefs are wiped on first start.
