---
type: module
owner: android
updated: 2026-10-22
---

# android

## Code map
- `brahma-connect-android/app/src/main/java/com/brahma/connect/` (to move to `android/`): `MainActivity`, `BrahmaConnectApplication`, `BrahmaConnectForegroundService`, `accessibility/`, `commands/DeviceCommandHandler`, `network/` (WebSocket client, gateway discovery), `pairing/` (payload parser, storage), `ui/`, `device/`, `core/`.

## Verify
```bash
brahma-connect-android/gradlew -p brahma-connect-android :app:assembleDebug        # path changes after the move
brahma-connect-android/gradlew -p brahma-connect-android :app:testDebugUnitTest
```
Needs `local.properties` with `sdk.dir=/home/krawin/Android/Sdk` in the app root (gitignored, create it in your worktree).

## Gotchas
- Never built before 2026-10-22; toolchain was Gradle 8.7 / AGP 8.5.2 / SDK 35 (not installed here).
- Capability names (`battery`) differ from action names (`get_battery`); `music`, `app`, `wifi_state` advertised without full handlers (see `pending.md`).
