# Kravein Connect Android

Companion app for Kravein: it pairs with `kravein serve --lan` by QR code and then answers the phone commands
defined in `kravein/phone_protocol.json` (battery, flashlight, volume, launch app, open URL, device info).

- Pairing: `POST /phone/pair` with the code from the QR offer; the device credential is kept only in encrypted storage.
- Connection: `WS /phone/ws` with `Authorization: Device <id>:<secret>`; reconnects with backoff, asks to pair again if refused.
- One vocabulary: the app's command table is checked against the JSON by a unit test.

Build and test: see `kb/modules/android.md` (Verify). JDK 21, Android SDK 36/37.
