---
paths:
  - "web/**"
  - "kravein/server.py"
  - "kravein/events.py"
  - "kravein/webauth.py"
  - "tools/check_layout.py"
---

# Hard-won knowledge: Web transport and frontend

Moved verbatim from `CLAUDE.md`. Each paragraph is a bug that already happened; do not rediscover it.

**Every client request must produce a reply frame.** Clicking "Text" while voice was already off
left the toggle reading "Stopping…" until the page was reloaded: the client sets that flag
optimistically and clears it when the server confirms, but with no session to end the server did
nothing and sent nothing. Optimistic UI state cleared only by a remote event needs three things,
and this bug needed all three — the server always answering, the client not asking for work that
is not needed, and an invariant on the client (you cannot be "stopping" something already stopped)
so a dropped frame cannot wedge the control.

**A confirmation frame must never be silently dropped.** `events.Session.confirm` blocks a worker
thread until it is answered, so a card that fails to render is not a cosmetic bug — the session
freezes until the 120s timeout, then denies. Voice sessions never set `session.turn_id`, so their
confirmations arrived with an empty id, matched no message, and the client's inline branch threw
them away: the transcript showed the tool call and then nothing at all. The client now routes any
confirmation whose turn has no live message to the global overlay, so there is no id for which a
card can vanish.

**`server.py` must not use `from __future__ import annotations`.** FastAPI resolves route type
hints to tell a request object from a query parameter. Postponed annotations are strings resolved
against module scope, where the lazily-imported `Request`/`WebSocket` names don't exist, so every
handler's request argument gets misread as a required query parameter and the route 422s.

**`overflow-y-auto` does nothing on a flex child without `min-h-0`.** A flex item will not shrink
below its content, so the element grows to fit and never scrolls; the overflow then escapes into
whatever ancestor is `overflow-hidden` and the content is simply unreachable. Every panel had
added its own `overflow-y-auto` to a div with no height constraint, so **five screens clipped** —
over 1,000px of the Apps list could not be reached at all. The scroll container is now `Screen`'s
content wrapper (`min-h-0 flex-1 overflow-y-auto`), once, so every current and future panel
inherits it. `tools/check_layout.py` walks every view and fails if anything overflows with nothing
able to scroll it — there is no JS test runner here, so that script is the only thing watching.

**A stale bundle will make a correct fix look broken.** Kravein's Playwright profile is
persistent and caches `index.html`, so re-measuring after a rebuild reported the identical numbers
and the layout fix looked like a no-op. Always cache-bust (`?r=<time>`) when verifying a frontend
change in that browser.

**A cancel arriving while a confirmation is open must answer it.** `events.Session.confirm` blocks
the worker, so without `deny_pending()` the stop does nothing visible until the 120s timeout.
Denial is the only safe resolution: a cancelled request must not go on to perform the action it
was waiting to be allowed to do.

**LAN exposure allows IP literals, never hostnames.** `kravein serve --lan` extends the Host and
Origin allowlist with the literal address being served and nothing else, which is what keeps the
DNS-rebinding defence intact — a rebound name still arrives carrying the attacker's hostname.
Verified against a real LAN bind: a **valid token** with `Host: evil.example.com` is still
rejected 400, as is another private address this machine holds but is not serving. Note that
`ipaddress` calls `0.0.0.0` private (it is `0.0.0.0/8`), so `is_private_address()` rejects
unspecified addresses explicitly rather than leaning on a string check upstream.

**Voice and text share one transcript, and one rendering path.** Spoken turns are built into
the same `messages` array as typed ones (`s…` ids alongside `t…`) and render through `TurnGraph`
like everything else — there is no voice-specific component. The parallel `voiceLines` array and
its side panel are gone. Two things made this work: the user's message is created on the *first*
`heard_partial` and rewritten in place, so the late-arriving final transcript does not file the
question after the answer it prompted; and `listening` is the turn boundary, which both pipelines
emit even though local sends it before the next utterance and live sends it after `turn_complete`.
Session chatter ("connecting to…", "ended") is *not* conversation and stays in a status line —
folding it into the transcript would render infrastructure as something Kravein said.

**Client-side voice turn ids must not be `v…`.** The server already uses that prefix for the voice
confirmer's `session.turn_id`. They are unrelated namespaces that would silently collide, so
spoken turns are `s…`.
