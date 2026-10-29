# pending.md

Work that is deliberately deferred, so a later session picks it up instead of
rediscovering it. Longer-running context lives in `user-space/STATE.md`;
this file is just the queue.

---

## Add Claude models as an LLM provider

**Status:** not started. Discussed 2026-08-24, deferred by the user.

Kravein currently has three providers — `cline` (primary), `gemini` (vision and
voice Live) and `openrouter`. Claude is not among them, and on this workload it
is worth having: `llm.provider_order` is `["cline"]` today, so a Cline outage
has nowhere to fail over to that does native tool calling well.

### The route to build

An **Anthropic API key** provider, alongside the existing three:

- `anthropic_api_key` in `~/.config/kravein/api_keys.json`.
- Native `tool_use` / `tool_result` blocks, so all 143 skills go through the
  same path as the Cline direct-HTTP route — no JSON-in-prose protocol.
- Vision, for `describe_screen` and the screenshot skills.
- Wired into `provider_order`, the circuit breaker (`llm.py`), `kravein doctor`
  and the Brain screen's provider list.
- Benchmarked with `tools/eval_models.py` against the **real tool schema**, not
  a "say OK" prompt — see the note in `CLAUDE.md` about rankings inverting once
  ~10k tokens of tool definitions are attached. What matters is
  calls-to-completion, next to `deepseek-v3.2`'s 2.5–3.1s baseline.

Ordinary work; nothing blocking it but the key.

### The route that was considered and rejected

The user asked to reuse the **Claude Code subscription OAuth token** from
`~/.claude/.credentials.json` and call `api.anthropic.com` with it directly,
mirroring what `cline.py` does with Cline's own credential store.

Claude declined to build it, twice, and this note records why so the question
does not have to be reopened from scratch:

1. **It authenticates a different application with credentials issued for
   Claude Code.** That routes around the access controls on that subscription.
   Removing the refresh step (as was later proposed, for "just a test") does not
   change this — a single hardcoded token does the same thing, and a working
   test *is* the working circumvention.
2. **The Cline precedent does not extend it.** `cline.py` does read Cline's
   credential store and refresh against it. The consistent conclusion is that
   the Cline direct-HTTP path has the same problem, not that a second one
   should be added.
3. **It would also break the thing the user asked to protect.** The token is
   short-lived. Keeping it alive means refreshing, refresh rotates the token
   server-side, and two clients refreshing against one stored refresh token is
   exactly how you get logged out of Claude Code. A read-only, never-refresh
   version stops working within hours.

For the record: it *would* authenticate. There is no technical blocker to go
looking for — the reason not to do it is not that it fails.

### The third option, measured

`claude -p` headless — Claude Code running as Claude Code, using the
subscription legitimately and touching no credentials. Measured on 2026-08-24
and it is the weakest of the three on the merits:

- **17,309 tokens** of Claude Code's own system prompt on *every* call.
- ~3.8s wall clock for a trivial "reply with OK".
- No native tool calling, so Kravein's tools would need the structured-JSON
  protocol the Cline CLI fallback uses (`cline.build_prompt`).

Worth reaching for only if the API key is ruled out.

---

## Kravein Connect — finish or retire it

**Status:** scaffolding. Nothing in the Linux port starts it.

`kravein_connect/` is a FastAPI + WebSocket gateway on port 8765 that pairs
with `android/`, a Kotlin app. The phone is a *device the
assistant controls*, not a remote control: the desktop sends `execute`, the
phone runs it on itself (battery, flashlight, launch app, open URL, volume) and
returns a `result`. `chat_message` also lets the phone talk to the assistant.

The only reference in `kravein/` is `integrations.kravein_connect: False`.
Nothing imports or starts the gateway. Its own 4 tests pass and it imports on
3.14, but wiring it up is real work, not a flag flip.

### Fix before it ever listens on a real network

Windows-era code, never ported or audited, and well below the rest of the
project's posture:

- **Binds `0.0.0.0:8765`** — the whole LAN. `kravein serve` binds loopback and
  *still* enforces a token plus Origin and Host checks, because loopback alone
  is not a boundary.
- **No TLS.** Plain `ws://`. Pairing secrets and every command cross the
  network in the clear.
- **No equivalent of `webauth.py`.** The pairing secret is the whole story.

### Still outstanding on the Android side

The rename left `android/` alone by decision, since a Java
package move cannot be build-tested on this machine. What remains:

- Package `com.kravein.connect` (old package) and the old-name class and file names.
- `the old Application class` and `the old theme` in the manifest —
  identifier pairs, so both ends must move together.
- The app has never been built here at all: needs Android Studio, JDK 17,
  SDK 35.

The mDNS service type and all user-visible text **were** fixed (2026-08-24),
because those are the wire contract and the on-screen name.

### A trap worth knowing about before wiring the first command

The phone advertises capabilities as *categories* — `battery`, `flashlight`,
`volume`, `device_info` — but implements *actions*: `get_battery`,
`flashlight_on`, `flashlight_off`, `volume_get`, `volume_set`. Two different
vocabularies for the same things.

Nothing breaks today only because `command_router.route()` checks a
`required_capabilities` list the *caller* passes, rather than deriving it from
the action name. The moment someone does the obvious thing and passes
`required_capabilities=["get_battery"]`, it returns `CAPABILITY_MISSING`
against a phone that is perfectly capable of it.

Also: `media`, `apps` and `wifi_state` are advertised but have no handler at
all, so they answer `UNKNOWN_COMMAND`.

Pick one vocabulary across both halves before building on this.

---

## ~~Cap the Cline direct-HTTP timeout~~ — DONE 2026-08-30

`llm.cline_timeout_seconds`, default 45s. Raise it if you routinely ask for
work that legitimately takes longer to produce a first token. Original notes
kept below for the reasoning.

**Found 2026-08-24 while testing the stop button.**

The cancel mechanism works — 0.0s from cancel to `turn_end` once the loop is
between rounds. But it can only bite between things, and a measured turn on
this machine spent **52 seconds inside the first model call** before the loop
reached any check point. For those 52s Stop does nothing but say "Stopping…".

The cause is a timeout inconsistency. `llm.py` caps Gemini at 20s with the
comment "a slow Gemini is as unusable as a down one" — but the *primary*
provider has no such cap:

| Provider | Timeout |
|---|---|
| Gemini | 20s (configurable), then failover |
| Cline direct HTTP | **180s** |
| Cline CLI | **240s** |
| OpenRouter | 90s |

So a degraded Cline can hold a turn for three minutes with nothing able to
interrupt it. Now that NovaRouter is in `provider_order` there is somewhere to
fail over *to*, which is what makes capping it viable at all.

Proposed: `llm.cline_timeout_seconds`, defaulting to something like 45s, so a
slow primary hands over instead of hanging. Not done unprompted because it
changes primary-path behaviour — a legitimately slow long request would start
failing over, and that tradeoff is the user's call.

---

## NovaRouter cannot be the primary at 144 skills

**Tested against the live API with a real key, 2026-08-30.**

Tool calling **works**: `list_windows`, `battery_status` and `run_command` all
came back as proper `tool_calls`. The provider is sound as a failover and is
keyed and in `provider_order`.

What blocks it from leading:

**1. `tools` is capped at 128. There are 144 skills.**

```
400 validation_failed: "Array must contain at most 128 element(s)", param: "tools"
```

Making it primary as-is would 400 on *every* turn and fall through to Cline —
a guaranteed wasted round-trip per request, strictly worse than today.

The truncation is not cosmetic. Sending a 128-slice that happened to omit
`system_status` produced *"I don't have a `system_status` tool"* and a
substituted `run_command`. The model cannot call what it cannot see.

**2. Intermittent 503s from server-side web grounding.**

`"What windows do I have open right now?"` failed 3/3 with
`web_search_unavailable — Live web search did not return grounded sources`,
while `"List my open windows."` succeeded. NovaRouter routes some prompts to a
live web search that is flaky, and that failure surfaces as a provider error.

**3. Latency 8–12s per call** with 128 tools attached, against Cline's ~2.7s
baseline.

### If it ever needs to lead

Two routes, both real work, neither taken:

* **Per-provider tool budget** — trim to 128 for NovaRouter only, dropping 16
  from the settings/games/documents tail. Those become uncallable on that
  provider. Category counts: settings 17, files 16, interact 16, browser 11,
  system 11, apps 10, windows 10, notify 9, media 7, power 7, web 6,
  documents 5, llm 5, games 4, shell 4, memory 3, terminal 3.
* **Relevance-selected tools per turn** — nothing permanently lost, but it is
  tool retrieval, it affects every provider, and it can misfire by omitting
  something the model needed.

Decision on 2026-08-30: keep Cline primary, NovaRouter as failover.
