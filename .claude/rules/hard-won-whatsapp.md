---
paths:
  - "kravein/whatsapp_service.py"
  - "kravein/platform/whatsapp.py"
  - "install-linux.sh"
  - "systemd/**"
---

# Hard-won knowledge: WhatsApp channel and installer

Moved verbatim from `CLAUDE.md`. Each paragraph is a bug that already happened; do not rediscover it.

**WhatsApp's pairing QR does not have one lifetime.** The first code of a session is valid 60s
and every refresh after it 20s — measured, twice, because the first measurement started *after*
the first code and concluded "20s" for everything. A single TTL is wrong in both directions: 60s
serves a code that died forty seconds ago, 25s blanks the screen for 35s waiting for a refresh
that is not due yet. `whatsapp_service` treats the server's rotation as the authority — the most
recent code *is* the current one — and uses a constant only to notice the pushes have stopped.

**A unix socket on disk says nothing about whether anything is behind it.** The WhatsApp service
unlinked the path before binding and again on exit, so a second instance stole the channel from a
working one, and that duplicate exiting deleted the socket the survivor was still listening on —
the channel disappears while a healthy daemon is still running, and `Restart=always` makes that
routine. Now: probe the socket and refuse to start if something answers, and on shutdown unlink
only the **inode** this process bound. Path comparison cannot work; the path is identical for
every instance.

**A live thread is not evidence that pairing still works.** whatsmeow stops pushing QR codes a
couple of minutes after a pairing session opens, but the thread inside `client.connect()` stays
alive and the state stays `pairing`. `Session.start()` short-circuited on a live thread, so asking
to link again did nothing at all and `kravein whatsapp link` sat on "Waiting for a code…" for a
channel that had closed twenty minutes earlier. Linking now always tears the client down and
builds a new one — and does it on a background thread, because waiting for `connect()` to unwind
outlasts the caller's socket timeout and a socket handler blocked on a join stalls every request
behind it.

**A session file is not a linked device.** neonize creates `session.db` the moment a client is
constructed, so `SESSION_DB.exists()` is true after any *attempted* pairing. The service used that
to decide whether to auto-connect at boot, which meant it opened a pairing session nobody was
watching, burned the window, and sat in `pairing` with a dead code — which is what the user then
runs `whatsapp link` into. `linked_devices()` asks the store for registered devices instead, and
answers without opening a connection.

**Ship the unit, do not describe it.** `install-linux.sh` wrote `kravein-whatsapp.service` into
`~/.config/systemd/user/` and then *printed* the `systemctl --user enable --now` line for the user
to run. The installer had never been run, so the unit was not on disk either, and enabling it
failed with "Unit does not exist" — telling someone to enable a unit you never deployed is the
same as not shipping it. The installer now creates the directory, substitutes the real repo path
(copy, not symlink: the unit is a template carrying `%h/code/Kravein`), reloads, and enables. Never
`sudo systemctl --user` — that targets root's absent session bus, not the user's.

**A QR's polarity has to be set, never inherited.** The terminal renderer printed `█` for a dark
module and a space for a light one, leaving the actual colours to the terminal theme. On a dark
theme `█` draws in the *light* foreground and the spaces show the *dark* background, so every
pairing code came out inverted and WhatsApp refused all of them — while looking perfect on a light
theme, which is the worst way for this to fail. Each row now carries an explicit black-on-white
SGR pair (`\033[30;47m`). The quiet zone is also four modules, per spec, not the one it shipped
with. Asserted by decoding the rendered half-blocks back into a matrix and comparing against the
encoder, because "it looks like a QR" is not a test.

**Inbound WhatsApp is a confirmation channel, not a chat, and the narrowness is the feature.**
A message arriving there is untrusted network input reaching a process that changes local state, so
it never touches the model — an LLM asked to "work out what they meant" turns the reminder channel
into a remote prompt-injection route into an assistant that runs shell commands, and prompting does
not close that. Five gates before any parsing (configured, not from us, not a group, owner
allowlist matched on digits, recent), then a four-word vocabulary matched with `==`. Two of those
gates are non-obvious and both are real: **our own replies come back through the same handler**, so
a confirmation containing "done" would feed itself; and **whatsmeow replays undelivered messages on
reconnect**, so this morning's "done" can arrive at teatime and silently complete a step nobody
did. Everything that fails a gate is dropped in **silence** — replying even with an error confirms
to a stranger that something automated is reading the number — and nothing rejected is logged with
its contents, or the audit trail becomes a record of other people's messages.
