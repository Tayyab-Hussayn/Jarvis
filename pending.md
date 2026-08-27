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
