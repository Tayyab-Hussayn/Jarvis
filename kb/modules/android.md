---
type: module
owner: android
updated: 2026-10-22
---

# android

## Code map
- `android/app/src/main/java/com/kravein/connect/` (package/app id `com.kravein.connect`): `MainActivity`, `KraveinConnectApplication`, `KraveinConnectForegroundService`, `accessibility/`, `commands/` (`DeviceCommandHandler` = Android glue; `CommandRouting.kt` = pure `DeviceAction` vocabulary + argument parsing), `network/` (WebSocket client, gateway discovery), `pairing/` (payload parser, storage), `ui/`, `device/`, `core/` (models, `KraveinProtocol`).
- `android/gradle/libs.versions.toml` — version catalog (AGP 9.4.1, Kotlin 2.4.21, same as `~/code/smart-sticky-wallpaper`).
- Tests: `android/app/src/test/java/com/kravein/connect/{commands,pairing}/`.

## Verify
```bash
android/gradlew -p android :app:assembleDebug       # -> android/app/build/outputs/apk/debug/app-debug.apk
android/gradlew -p android :app:testDebugUnitTest
```
Needs gitignored `android/local.properties` with `sdk.dir=/home/krawin/Android/Sdk` (create it in a fresh worktree). First build downloads the app's libraries from Maven (~8 min); later ones hit `~/.gradle`.

## Gotchas
- Moved from `brahma-connect-android/` and renamed 2026-10-22; Gradle 8.7/AGP 8.5.2/SDK 35 became Gradle 9.8.1/AGP 9.4.1/compileSdk 37/targetSdk 36/build-tools 36.0.0.
- AGP 9 has Kotlin built in: no `org.jetbrains.kotlin.android` plugin and no `kotlinOptions {}` (jvmTarget follows `compileOptions`). The Compose compiler plugin is still applied.
- `android.jar`'s `org.json` is a stub that throws in JVM tests; `testImplementation(libs.json)` (`org.json:json`, BSD-like JSON license, tiny) supplies the real one.
- `gradlew` must keep its executable bit; `.gradle/` build cache had been committed before the move and is now gitignored.
- Capability names (`battery`) differ from action names (`get_battery`); `music`, `app`, `wifi_state` advertised without full handlers (see `pending.md`). `PairingPayloadParser` accepts an offer with missing fields (blank values) — validation belongs to the protocol task.
