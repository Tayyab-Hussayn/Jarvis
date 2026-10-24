---
type: plan
owner: orchestrator
updated: 2026-10-24
---

# Android app as a full Kravein client

Owner request 2026-10-24: the Kotlin app controls Kravein like the web UI does (live chat, system info, window switching, every feature), looks the same, stays lightweight, and Kravein keeps controlling the phone. Owner delegated all decisions (autonomous mode).

## Decision: native shell + the served web UI in a WebView
- The web UI already has every feature; a native Compose copy of 12+ views would duplicate thousands of lines and drift on every web change. A WebView of `http://<host>:<port>/` is the same UI by construction and gets new features for free.
- Native Kotlin keeps what a page cannot do well: pairing (QR), the `/phone/ws` foreground service (Kravein → phone control), connection state, and later native confirmation notifications.
- Auth: the paired device trades its device credential for a short-lived **UI token** (`POST /phone/ui-session`), then loads `/?token=<ui token>` — the web UI already reads `?token=` into sessionStorage. Revoking the device revokes its UI tokens.

## Phases (one PR each)
1. **web — UI token** (`POST /phone/ui-session`; webauth accepts it on `/ws` + `/api/*`; revoked with the device).
2. **android — client shell** (WebView tab on the UI token, native tabs for phone status/settings; hardening; lightweight lifecycle). Runs in parallel with 1 against the spec in its packet.
3. **web — phone-width layout** (360–412 px: nav drawer/bottom bar, no horizontal scroll, overlays fit).
4. **android — native confirmations** (pending DESTRUCTIVE confirmation → notification with Approve/Decline when the app is in background; needs a protocol frame, designed after 2).

## Lightweight rules (all android tasks)
One OkHttp client; no polling where a socket exists; no wakelocks; WebView created only when its tab is shown and destroyed after the app is in background for a while; release build with R8 + resource shrinking; no new heavy dependency.
