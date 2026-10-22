---
paths:
  - "kravein/agent.py"
  - "kravein/llm.py"
  - "kravein/cline.py"
  - "kravein/toolselect.py"
  - "kravein/config.py"
  - "tools/**"
---

# Hard-won knowledge: LLM providers and the agent loop

Moved verbatim from `CLAUDE.md`. Each paragraph is a bug that already happened; do not rediscover it.

**Gemini 3.x requires `thought_signature`.** Function calls replayed in history without it fail with `400 … missing a thought_signature`. `ToolCall.signature` carries it. The symptom is not an error you see — the facade silently fails over to the next provider and every multi-turn request gets slow.

**Cline has two paths.** Direct HTTP (`api.cline.bot/api/v1`, OpenAI-compatible, native tool calling) is the fast one. The CLI costs ~5s of Node boot *per call* — `cline --version` alone takes 5s. Only models gated to "Cline product surfaces" (403) use the CLI.

**Read the `cost` field, not `upstream_inference_cost`.** They mean different things and mixing them up inverted a whole model comparison. `cost: 0` with `is_byok: true` means "billed to a key on that account", not "free". Only ids ending in `:free` work at a zero balance.

**Model ids are not the catalogue.** `@cline/llms/dist/models.js` lists every model Cline knows about across all providers — most are not callable on a given account. Test before claiming a model exists.

**The clock belongs in the messages, not the system prompt.** A model asked to schedule
"next Tuesday" resolves it against whatever date it believes today to be — its training cutoff.
The obvious fix is a timestamp in the system prompt, and that is the wrong place: the system
prompt is the cached prefix, and a value that changes every second invalidates it on every
request. `Agent._with_context()` appends the current ISO time and zone as a trailing `[context]`
message instead, so the whole prefix still matches and only the tail differs — which it does every
turn anyway. It is never stored in `self.messages`, because replayed history carrying an old
timestamp is worse than none. `jobs.parse_when()` then validates ISO and plain durations only: an
earlier version understood "5pm" and "tomorrow 17:00", which was a second NL parser competing with
the model's and would have failed on "next Tuesday" regardless. Verified with both.

**A timeout cap is only as good as the failover behind it.** Capping Cline at 45s looked like a
straight win — a slow primary hands over instead of holding a turn for 180s while Stop can do
nothing. It was a regression: Cline timed out, the facade failed over, and NovaRouter rejected all
144 tools with `400 Array must contain at most 128 element(s)`, so the request failed outright
where before it merely hung. Verified end to end before and after. Two rules fell out of it:
**measure the failover path, not just the primary**, and a provider with a tool cap needs a
budget (`NovaRouterProvider._fit_tools`) or it cannot serve an agent turn at all. The budget drops
by category, least load-bearing last — you are already degraded, and "cannot change the volume
right now" beats "the request failed".

**Cancelling has to be cooperative, and it can only bite between things.** The agent loop runs on
a worker thread, so `turn_task.cancel()` on the asyncio wrapper never reached it — the thread ran
all eight tool rounds regardless. `Agent.ask(text, stop)` now checks a `threading.Event` before
each model call, before each tool call and after each tool result. Measured **0.0s** from cancel
to `turn_end` when the loop is between rounds. What it cannot interrupt is work already in flight,
and that is not a footnote: a slow provider call is exactly when you want to stop. A cancelled
turn must still append a `tool` message for every call the assistant named, or the next request
sees a malformed transcript.

**Benchmark with the real tool schema.** Latency rankings invert once ~10k tokens of tool definitions are attached; a "say OK" benchmark is actively misleading. What matters is calls-to-completion, not single-call speed. `tools/eval_models.py` exists for this and retries 5xx so server hiccups are not scored as model failures.
