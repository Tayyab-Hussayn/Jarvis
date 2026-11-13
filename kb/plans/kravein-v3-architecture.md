---
type: plan
owner: orchestrator
updated: 2026-11-14
---

> Plan of record for Kravein v3 (proposal v4, from the architecture advisor). Owner decisions D1–D23 are still open: until the owner rules, task packets follow the recommendation in section 19, except changes that remove an existing feature (D2, D3, D7), which wait for the owner.

# Kravein — architecture proposal (v4: "one brain, many hands")

Date: 2026-11-13. Status: **proposal, not approved**. Author: architecture advisor session.

**Changes in v4** (after the orchestrator's review of v3; nothing else was reopened):
- new section **8A Computer use** (GUI control of apps with no API);
- new section **8B Browser automation**;
- new section **12A Long-lived goals and projects**;
- section 1 gained points 11–13; section 3 gained a note; section 17 gained rows; section 18
  gained phases **3B, 4B and 6B** and one line in phase 1; section 19 gained **D15–D23**;
  section 20 gained the new unverified items.
Document version v3 named the *code* generation "v3"; that name is unchanged.
Inputs: the six reports in this folder, a direct read of the code at `7861229`
(`agent.py`, `llm.py`, `live.py`, `safety.py`, `registry.py`, `jobs.py`, `config.py`,
`skills/memory.py`, the Orchestrator profile, `kb/modules/engine.md`, `kb/plans/*`), the
hard-won rule files, and four answers from the owner (section 2).

How to read this: section 1 is the whole design in one page. Sections 4–16 give, per area, the
options, the recommendation, the reason and the risks. Section 18 is the migration plan.
Section 19 lists the decisions the owner still has to make.

Evidence labels used below: **[code]** = I read it in this checkout. **[report]** = taken from a
report in this folder, not re-verified. **[web]** = checked on the web on 2026-11-13; the source
quality is noted. **[judgment]** = my reasoning, not a measured fact.

---

## 1. The design in one page

1. **One brain.** A single text Orchestrator loop owns every decision: planning, delegation,
   memory, proposals. Today there are two brains (the text `Agent` loop and Gemini Live acting as
   the Orchestrator itself) and three bypass paths (jobs, daemon, daily review build their own
   agent). All of them move behind one entry point, `Runtime.submit(run)`.
2. **Voice is a thin front.** Gemini Live keeps the microphone, the speaker, turn-taking and
   barge-in. It gets a handful of tools: a non-blocking `handoff` into the Orchestrator, `status`,
   `cancel`, and a short code-defined list of instant actions (volume, media, time). A local wake
   word opens the session and silence closes it, so the metered session is not open all day.
3. **Workers are text-only profiles**, as today, with three fixes: a model per role, a
   schema-validated report instead of a regex-parsed one, and a way to ask a question mid-task.
4. **Capabilities come from outside the core.** New capability = an MCP server or a plugin
   folder, not a new Python file in the repo. Every external tool is registered into the same
   registry and passes the same `dispatch → safety.enforce → audit` path as a built-in skill.
5. **A small visible tool set plus search.** Each agent sees about 20 tools. Everything else
   (long-tail skills, all MCP tools) is reached through `tool_search` / `tool_describe` /
   `tool_call`, and `tool_call` goes through the same dispatch checks.
6. **Models are chosen per role, by the owner, in the web UI.** The code knows roles
   (`voice`, `orchestrator`, `worker:<name>`, `utility`, `judge`, `vision`), not model names.
   A usage ledger records tokens and cost per run, so the choice is made on numbers.
7. **Autonomy is a permission model in code, not a mode.** A new confirmation class sits between
   "just do it" and "destructive": actions that are risky but not destructive (outward messages,
   unknown MCP tools, spending). They ask once; the owner's **Always allow** click creates a
   scoped, revocable grant. Destructive and critical actions always ask. Nothing ever approves
   its own action, and every failure path denies.
8. **Proactivity is triggers feeding the same brain.** Cron, a heartbeat and events all become a
   `Trigger`. A trigger starts a read-only triage run that can end in exactly four ways: nothing,
   a notification, a proposal, or an action that an existing grant already covers.
9. **Learning goes through review.** After a run, a cheap background pass drafts memory facts and
   procedure skills. Facts from the owner's own words are saved (audited, undoable). Anything
   derived from web or tool content, and every skill change, waits for the owner's approval.
10. **"Done" is decided by code.** The run's outcome (`done` / `partial` / `not confirmed` /
    `failed` / `declined`) is computed from tool evidence and shown in the UI and spoken by voice,
    whatever the model's prose says.
11. **The browser is a first-class hand** (8B). Kravein keeps its own Chrome profile, reads pages
    as a text snapshot with element references, and classifies each action from the element
    itself: reading is free, posting or submitting on an account is `GUARDED`, paying is always
    an owner-only card. Outward content goes out as an owner-approved draft.
12. **GUI control is the last resort, and a session** (8A). An API, MCP server, shell command or
    the browser is tried first. When none exists, a `desktop` worker operates **one named
    window** in an observe → act → re-observe loop with step, time and cost caps, a stop key,
    and no access to secrets. A run that has looked at the screen is tainted.
13. **Goals outlive runs** (12A). The owner sets an objective with a metric, a deadline, a budget
    and a scope. Kravein plans it, works it through ordinary runs and triggers, and reviews it
    on a cadence. Progress numbers are collected by code, never written by the model, and a goal
    can never widen its own scope or budget.

What this is not: a rewrite, a framework migration, or an embedded third-party agent. About 80%
of the current code stays (section 17).

---

## 2. Owner decisions already taken (2026-11-13)

These came from the owner in this session and the design is built on them.

| Topic | Owner's answer | Effect on the design |
|---|---|---|
| Business work | All kinds: client hunting and outreach, email, web/software/AI-automation work, content and social media, new business ideas, revenue strategy. Use outreach, web/software and content as the worked examples. The owner's **existing outreach agent** (scraping + outreach) will be connected **through MCP**; Kravein will not grow its own outreach tools. For any new kind of work, MCP and plugins come first. | MCP client and plugin system move to the front of the plan (phase 1). No `growth` tools in the core; a `growth` worker profile is a thin user of MCP tools. |
| Budget and models | No fixed monthly number. Several model options must exist: very cheap ones (DeepSeek V4.1 Flash, GLM 5.3 Flash) for most work, Claude Sonnet and Opus where quality is needed. **Do not fix a model per job now**; the owner will decide later, and the web frontend must offer model selection for every agent. | Roles-based model config, a provider layer that can reach all of these, a per-agent model picker in the UI, a cost ledger and scorecards to inform the choice. This document names candidates and prices only, never a final assignment. |
| Autonomy | Ask on every risky action. If the owner clicks **Always allow** in the frontend, do not ask again for that action. **Destructive shell commands and actions always ask.** | The grant model in section 13. It also creates two conflicts with existing code that need a ruling (section 19, D2 and D3). |
| Voice | Local wake word, session opens on "Kravein", closes by itself after silence. | Section 4. Proactive speech needs a rule of its own (section 12). |

---

## 3. Target architecture

```
                                    OWNER
        ┌──────────────┬───────────────┬───────────────┬──────────────┐
        │ voice        │ web UI        │ phone app     │ CLI          │
        ▼              ▼               ▼               ▼              │
 ┌──────────────┐   ┌───────────────────────────────────────────┐    │
 │ VOICE FRONT  │   │ TRANSPORT  server.py / events.py / phone  │◄───┘
 │ wake word    │   │ websocket frames, confirmation cards,     │
 │ (local, free)│   │ grants UI, proposals, model picker        │
 │ Gemini Live  │   └───────────────────┬───────────────────────┘
 │ turn-taking, │                       │
 │ barge-in     │   handoff(task)       │        TRIGGERS (unattended)
 │ ~8 tools     ├──────────┐            │   ┌──────────┬───────────┬─────────┐
 └──────▲───────┘          │            │   │ cron     │ heartbeat │ events  │
        │ results:         ▼            ▼   ▼ (jobs.py)│ (idle)    │ (bus)   │
        │ WHEN_IDLE /   ┌──────────────────────────────┴───────────┴─────────┐
        │ INTERRUPT /   │ RUNTIME   Runtime.submit(Run)                      │
        │ SILENT        │ one queue, durable run state (SQLite), budgets,    │
        └───────────────┤ stop/cancel, attended vs unattended, usage ledger  │
                        └───────────────────────┬────────────────────────────┘
                                                ▼
                        ┌────────────────────────────────────────────────────┐
                        │ ORCHESTRATOR  (the one brain, text loop)           │
                        │ plan · decide · delegate · propose · remember      │
                        │ ~20 visible tools + tool_search/describe/call      │
                        └──────┬───────────────┬──────────────────┬──────────┘
                     delegate  │               │                  │ delegate
                               ▼               ▼                  ▼
                        ┌────────────┐  ┌────────────┐     ┌──────────────┐
                        │ system-ops │  │ research   │ ... │ growth (MCP) │  text-only
                        │ worker     │  │ worker     │     │ worker       │  workers
                        └─────┬──────┘  └─────┬──────┘     └──────┬───────┘
                              └───────────────┼───────────────────┘
                                              ▼   every tool call, every agent
   ┌───────────────────────────────────────────────────────────────────────────────┐
   │ TOOL LAYER   registry.dispatch                                                │
   │   allowed-set check → hooks(pre) → skill → safety.enforce → hooks(post) → audit│
   │                                                                               │
   │   built-in @skill functions │ MCP tools (pinned, untrusted output) │ plugins  │
   └───────────────┬───────────────────────────┬───────────────────────────────────┘
                   ▼                           ▼
   ┌───────────────────────────┐   ┌──────────────────────────────────────────────┐
   │ SAFETY  safety.py         │   │ platform/  (only place a subprocess starts)  │
   │ hard refusals → policy →  │   │ shell, desktop, browser, audio, MCP stdio    │
   │ denial memory → grants →  │   └──────────────────────────────────────────────┘
   │ confirmer (deny on fail)  │
   └───────────────────────────┘

   SIDE SERVICES (used by the Runtime and the Orchestrator, never by-passing the tool layer)
   ┌─────────────────┐ ┌──────────────────┐ ┌───────────────────┐ ┌──────────────────┐
   │ MODEL ROUTER    │ │ MEMORY           │ │ LEARNING          │ │ EVALS            │
   │ roles → models, │ │ profile files,   │ │ post-run review → │ │ outcome tasks,   │
   │ failover,       │ │ SQLite FTS5      │ │ proposals queue   │ │ prompt-surface   │
   │ usage ledger    │ │ facts + sessions │ │ (owner approves)  │ │ test, scorecards │
   └─────────────────┘ └──────────────────┘ └───────────────────┘ └──────────────────┘
```

v4 note on the diagram: the **browser** (8B) and the **desktop GUI operator** (8A) are tools and
worker profiles inside the tool layer, below `registry.dispatch` like everything else. **Goals**
(12A) sit beside the triggers: a goal owns jobs and a review trigger, and every piece of goal
work is an ordinary `Run` with a `goal_id`.

How the four named parts fit together:

- **Voice Orchestrator.** The owner experiences one assistant. Technically the voice model is
  the mouth and ears and the text Orchestrator is the mind. The voice front may answer small
  talk and run its instant list itself; everything else it hands to the Orchestrator and speaks
  the answer. Both write to the same conversation thread, as they already do **[code:
  `live.py:502-527`]**.
- **Text workers.** Reached only through the Orchestrator's `delegate`. Depth stays 1, enforced
  in code as today **[code: `agent.py:217-225`]**.
- **Model routing.** The Runtime asks the router for "the model for role X"; the router returns
  an ordered chain from settings. Voice is just another role with its own (Live) chain.
- **Safety layer.** Unchanged in position: below every agent, inside the tool layer. The voice
  front, MCP tools, plugins, triggers and the learning pass all reach the world only through
  `registry.dispatch`, so none of them can skip it.

---

## 4. Voice front

**Today [code].** `LiveSession` runs Gemini Live as the Orchestrator: it declares the full
Orchestrator tool set (about 60 schemas), builds the system prompt and a context seed once at
connect, and dispatches tools itself (`live.py:823-864`, `:948-969`). The default model is
`gemini-3.1-flash-live-preview` (`live.py:38-47`). Tool calls are blocking with a 90 s ceiling.
`SessionResumptionConfig()` is set, but there is no context-window compression and no GoAway
handling. A wake word exists only in the local STT chain (`voice.py:406`), not for Live.

**Options**

| | A. Live *is* the Orchestrator (today) | B. Thin Live front + text Orchestrator (recommended) | C. Cascade: STT → text Orchestrator → TTS |
|---|---|---|---|
| Brains | Two; rules built twice and drifting | One | One |
| Latency for a spoken reply | Lowest | Low for talk and instant actions; an acknowledgement at once and the result seconds later for real work | About 1–1.5 s per turn **[report 02]** |
| Roman Urdu / English mix | Good in, script drift out | Same | Local STT is poor (53% WER on code-switched audio **[report 02]**); cloud STT is fine |
| Tool load on the voice model | ~60 schemas; a missed tool call was already seen | ~8 schemas | none |
| Vendor lock | Google for voice and for the brain | Google for voice only | none |
| Cost | ~$0.023/min **[report 02]** | Same, plus text tokens for handed-off work | STT + TTS, cheaper per minute |
| Work to build | none | Medium | Large (turn detection, barge-in, echo) |

**Recommendation: B.** The evidence is that three independent stacks converged on it (OpenAI
chat-supervisor, LiveKit talker-reasoner, Google ADK streaming tools **[report 02]**), Hermes'
newest voice mode does the same thing **[report 05]**, and Kravein's own top weakness is the
two-brain split **[report 01, confirmed in code]**. C stays as the documented fallback if Live
becomes unusable; the `AudioSource`/`AudioSink` interfaces already make that swap possible.

**Design**

- **Tools declared to Live (target ≤ 8):** `handoff(task)` (non-blocking), `task_status`,
  `cancel_task`, and an **instant list** fixed in code: volume, brightness, media keys, time,
  battery. The instant list is a `frozenset` in the voice module, not a prompt rule. Instant
  tools still go through `registry.dispatch` with `source="voice-live"`.
- **`handoff` behaviour.** Returns at once with a run id. The Orchestrator run streams progress
  frames to the UI. Its result returns to Live as a function response scheduled `WHEN_IDLE`;
  errors and "a confirmation card is waiting" use `INTERRUPT`; progress uses `SILENT`.
- **Model requirement.** Non-blocking functions and scheduling exist on `gemini-3.8-live` and
  not on `gemini-3.1-flash-live-preview` **[web: Google's Live capabilities guide; the tool-use
  page and the guide disagree on the enum spelling `INTERRUPT` vs `INTERRUPTED`, so check the
  SDK constant]**. The chain becomes `3.8-live → 3.1-flash-live (degraded) → 2.5`. In degraded
  mode `handoff` is blocking: the model says one short line first, and the 90 s ceiling stays.
- **Confirmations.** No spoken approval. Cards stay on the screen and the phone, as the
  Orchestrator profile already says. The voice front announces "I need you to confirm on
  screen". Reason: deciding whether an utterance meant "yes" is a model judgment (Stonic's known
  weakness **[report 04]**), and the rule is that code decides.
- **Wake word and session life.** A local detector (openWakeWord, or livekit-wakeword which is
  built on it **[web: project pages; accuracy claims are the maintainers' own]**) with a custom
  "Kravein" model runs in a small always-on process and costs nothing per minute. On detection:
  chime, open Live with the stored resumption handle (valid about 2 h on the Gemini API **[web:
  Google docs; the Vertex page says ~10 min, so measure]**), stream the audio that followed the
  wake word. Close after `voice.idle_close_seconds` of silence (default 20) unless a handed-off
  run is about to report.
- **Session hygiene.** Add `context_window_compression` (sliding window), handle `go_away` by
  reconnecting on the handle during silence, and rebuild the context seed on every reconnect so
  it is never frozen for long.
- **Typed text during a voice session** goes to the Orchestrator directly, not into Live.

**Risks**

- `gemini-3.8-live` transcript script is unmeasured here. The existing finding is that transcript
  language depends on the model, not the pin **[code: hard-won-voice]**. Acceptance for the voice
  phase includes re-running the 12-clip A/B on 3.8 before it becomes the default.
- The voice model may answer a delegated question itself or say "done" before the result
  arrives (seen on 2.5 **[code: engine.md #195]**). Mitigation in code: a reply produced while a
  handoff is pending is tagged, and the UI and the spoken status come from the run state.
- Wake-word false accepts open a metered session. Mitigation: require speech within 5 s or close;
  log every open with its trigger; a per-day cap on session minutes (section 16).
- Proactive audio cannot be switched off on 3.8 **[report 02]**; with wake-word gating the
  session is closed most of the time, which bounds this.

---

## 5. Agent loop and Runtime

**Today [code].** One synchronous `Agent._ask` loop. No streaming and no usage accounting
(`llm.py` has no `usage` handling at all). Tool calls run one at a time (`agent.py:759-792`).
The top-level cap of 8 rounds ends in a hard error (`:817-823`), while delegated runs degrade to
a partial report. History is trimmed by an extra summarising model call in the middle of a turn.
Background tasks die with the server (`fail_orphans`).

**Options**

| Option | For | Against |
|---|---|---|
| Adopt a framework (OpenAI Agents SDK, Google ADK, LangGraph, Pydantic AI) | Durable execution, tracing, MCP built in | None enforces Kravein's safety model; their human-in-the-loop is the same pause/resume Kravein already has; ADK shipped a confirmation-forgery fix in v2.6 **[report 03]**. The whole tool layer would be re-wrapped for no safety gain. |
| Claude Agent SDK as the core | Everything on the wish list exists | Claude-only, drives a bundled Claude Code binary, API-key billing only **[report 03]**. It contradicts the owner's multi-model decision. |
| Embed Hermes as the worker runtime (Stonic's route) | Gateways, cron, skills hub for free | A second safety system across a process boundary; Hermes' own SECURITY.md says nothing in-process is a boundary; ~400 MB; ≥12k tokens of overhead per call **[report 05]**. Stonic ended up switching approvals off. |
| **Evolve the custom loop (recommended)** | Keeps the enforced safety path, the audit trail and 1,200+ tests; each change ships alone | Kravein owns the maintenance |

**Recommendation: evolve.** Borrow designs, not dependencies.

**Changes**

1. **`Runtime`** (new, small): the only way a run starts. `Runtime.submit(Run(source, principal,
   text, profile, attended, trigger))`. Web, voice handoff, CLI, jobs, heartbeat, events and
   approved proposals all call it. `make_agent` and the ad-hoc `Agent(...)` constructions in
   `jobs.py:620`, `daemon.py:92` and `proposals.py:578` go away. Unattended runs use an
   `orchestrator-unattended` profile whose tool set is read-mostly, so "jobs get every tool" ends.
2. **Durable run state.** A `runs` table in `agents.db`: id, status
   (`queued|running|waiting_approval|waiting_owner|done|partial|failed|declined|cancelled`),
   step log, plan, budget used. A run parked on an approval survives a restart and resumes at a
   step boundary. Side-effecting steps carry an idempotency key so a resume cannot repeat them
   (the OpenAI Agents SDK and LangGraph docs both warn about exactly this **[report 03]**).
3. **Degrade, never hard-fail.** The top-level loop gets the same wrap-up the delegated loop has.
4. **Compaction without a mid-turn model call.** First clear old tool results (keep a one-line
   stub, full text stays in the audit log), then summarise only between turns on the `utility`
   role. This is the order Anthropic's context-engineering guidance and Hermes both use
   **[report 03, 05]**.
5. **Parallel read-only tool calls.** When a round contains only SAFE calls, run them
   concurrently. Anything else stays serial, because the confirmer and the denial memory are
   per-thread by design.
6. **Streaming and real cancel.** Providers stream; the loop can drop an in-flight request on
   Stop. This fixes the acknowledged gap in `_ask`'s docstring.
7. **Usage on every reply.** `Reply.usage = {input, cached, output, cost}` from the provider,
   written to the ledger (section 16).
8. **A loop guard in code:** the same tool with the same arguments three times in a run returns
   a model-facing error instead of running again.
9. **Prompt cleanup.** Remove the `confirm=true` sentence from `SYSTEM_PROMPT`
   (`agent.py:100-101`); dispatch already strips it, so it only teaches the model a dead path.

**Risks.** Streaming touches every provider and the thought-signature replay
(`ToolCall.signature`); do it per provider behind a flag with the existing test stubs. Durable
resume is the subtlest piece; restrict the first version to resuming *parked* runs, not runs
that were mid-tool when the server died (those still end `failed`, honestly).

---

## 6. Sub-agents

**Today [code].** Four profiles; no profile sets `model`; the child gets `task` + a `context`
string; the report is free text parsed by regex with code overriding the outcome; a child cannot
ask anything until its final report; max 3 parallel, depth 1.

**Options for the report and the handoff**

| | Keep free text + regex | **Report as a tool call (recommended)** | Provider "structured output" mode |
|---|---|---|---|
| Works on every model | yes | yes (any tool-calling model) | no; differs per provider, and Claude's newest models reject forced tool choice **[claude-api skill]** |
| Validation | weak | registry coercion + schema errors the model can fix | strong |
| Change size | none | small: `submit_report(outcome, summary, changed[], evidence[], left[], concerns[])` ends the run | medium |

**Recommendations**

- **`submit_report` tool** for workers. Code still overrides the outcome on decline, timeout,
  budget and crash, exactly as now. A run that ends without calling it gets the existing
  built-from-the-log fallback.
- **`ask_parent(question)`** for workers: parks the child (`waiting_owner`), surfaces the
  question through the Orchestrator to the owner, and resumes with the answer. Unattended: the
  run ends `needs-input` with the question in the report. This replaces guessing.
- **Handoff packet** instead of a context string: task, done criteria, constraints, the top
  memory hits for the task, a workspace folder for artifacts, and a list of file paths the parent
  wants read. Large material travels as files, not as prompt text.
- **Spill long reports to a file** in the workspace with a short summary in context (Hermes'
  safety net **[report 05]**).
- **Per-worker model** through the role `worker:<name>` (section 16), chosen in the UI.
- **Inactivity timeout** in place of the fixed 300 s wall clock, which today cannot interrupt a
  provider call or a confirmation wait **[code: engine.md #138]**.
- **Delegation stays a judgment, with one code rule kept:** the 3-round escalation. Multi-agent
  runs cost roughly 15× the tokens of a single chat **[report 03, Anthropic's own figure]**, so
  the default is "Orchestrator does it"; delegation is for long, parallel or read-heavy work.
- **New workers are profiles + MCP tool lists**, e.g. `growth` (the outreach MCP server),
  `dev` (git, GitHub MCP, shell in a project folder), `content` (social MCP servers, documents).
  None of them needs core code.

**Risks.** `ask_parent` can turn into a chatty worker; cap it at 2 questions per run. A cheap
worker model with a strong Orchestrator can still produce confident wrong reports; that is what
evidence requirements (section 14) and the reviewer are for.

---

## 7. Tools and tool search

**Today [code].** 135 skills in 22 categories; the Orchestrator sends 60 schemas (~29 KB) and
system-ops 80 (~41 KB) on every call. Keyword routing exists (`toolselect.py`) but is off and is
skipped whenever a profile is active (`agent.py:523-526`), which is always, in Orchestrator mode.

**Options**

| | A. Static per-profile sets (today) | B. Keyword category routing (`toolselect`) | C. Dynamic schema loading (add the found tool's schema next round) | **D. Bridge tools: `tool_search` / `tool_describe` / `tool_call` (recommended)** |
|---|---|---|---|---|
| Tokens per call | 29–41 KB | less, varies by turn | small, grows | small and constant |
| Prompt-cache friendly | yes | changes per turn | no: `tools[]` changes mid-run | **yes: `tools[]` is byte-stable** |
| Scales to hundreds of MCP tools | no | no | yes | yes |
| Risk | selection accuracy drops past 30–50 tools **[report 03, Anthropic's figure]** | a missed keyword hides the right tool | cache misses; some providers cap tool count | a weak model may fumble nested arguments |

**Recommendation: D, with a real "core" per profile.** Hermes ships 21 visible tools in about
9.5k tokens and defers the rest behind exactly these three bridge tools, with A/B tests
**[report 05]**. Anthropic's own measurements show large accuracy gains from deferring tools, on
their models **[report 03; vendor evals]**. Neither is proof for cheap models, so this ships
behind a flag and is decided by Kravein's own eval (acceptance in phase 3).

**Design**

- **Core set ≤ ~20 tools per profile**, listed in the profile. Everything else in the profile's
  allowed set is *deferred*: allowed, but not in `tools[]`.
- **`tool_search(query)`**: BM25 over name + description + category, plus the Roman Urdu hint
  words already in `toolselect.CATEGORY_HINTS`. Returns up to 8 names with one-line descriptions.
  No model call.
- **`tool_describe(name)`**: the full schema.
- **`tool_call(name, arguments)`**: calls `registry.dispatch(name, arguments, allowed=...)`.
  The profile's allowed set, alias presets, `confirm` stripping, type coercion,
  `safety.enforce` and the `skill.unenforced` tripwire all apply, because it is the same
  function. A deferred tool is never a way around a profile.
- **Promotion:** a deferred tool used in three separate runs in a week is suggested for the core
  set (a proposal, not an automatic change, so the prefix stays stable).
- **Prompt-surface snapshot test.** A CI test that fails when the stock system prompt or any
  profile's `tools[]` changes in bytes, with the token count printed. Cheap, and it stops schema
  bloat from creeping back.
- **Footprint ladder** as a written rule for new capability: (1) an existing tool + a SKILL.md
  procedure, (2) an MCP server, (3) a plugin tool, (4) a new core `@skill` only when it needs
  `platform/` or `safety` knowledge that an outside tool cannot have.
- **Retire `toolselect.py`** once D passes its eval; its hint table moves into the search index.

**Risks.** Two extra rounds (search, describe) for a long-tail tool: acceptable because the
common tools are core. Bridge-call argument errors on cheap models: the dispatcher already
returns model-facing errors; measure the retry rate per model in the scorecard.

---

## 8. MCP and the plugin system

This is the owner's first priority, and the piece with the most new attack surface.

### 8.1 MCP client

**Options**

| Option | For | Against |
|---|---|---|
| Provider-side MCP (OpenAI Realtime's built-in MCP, Anthropic's MCP connector) | No client code | Bypasses `registry.dispatch` and `safety.enforce` entirely; tied to one provider; cannot reach local stdio servers without a tunnel **[report 02]** |
| Hand-written JSON-RPC client | No dependency | The spec moved twice in a year (stateless since 2026-07-28 **[report 03]**); a moving target to maintain |
| **Official MCP Python SDK, client only, behind the registry (recommended)** | MIT, tracks the spec, stdio + Streamable HTTP | A new dependency; pin the version, the API changed between v1 and v2 **[report 03]** |

**Design**

- **`kravein/mcp/`**: `manager.py` (connect, list, health), `bridge.py` (turns each MCP tool into
  a registry `Skill` named `mcp__<server>__<tool>`, category `mcp:<server>`), `pins.py`.
- **Transport.** Streamable HTTP for remote servers. stdio servers are started through
  `platform/shell.py` (it stays the only place a subprocess starts), inside the bwrap sandbox
  from #227 where the server allows it, with only the environment variables its config names.
- **Configuration** lives in `~/.config/kravein/mcp.json`, inside the private tree the agent can
  neither read nor write **[code: `check_read`/`check_path`]**. Server secrets live beside
  `api_keys.json`. Adding, enabling or re-approving a server is an owner action in the web UI or
  CLI. The agent may *propose* a server; it can never install one.
- **Pinning.** On approval, each tool's name + description + input schema is hashed and stored.
  On every connect the hashes are compared; a changed or new tool is disabled and the owner sees
  a diff ("rug pull" and tool-poisoning defence **[report 03]**).
- **Classification.** Every MCP tool gets a confirmation class set by the owner at approval time,
  with a safe default: **`GUARDED`** (section 13) unless the owner marks it read-only (`SAFE`) or
  destructive (`DESTRUCTIVE`). A server's own `readOnlyHint` annotation is shown as a suggestion
  and never trusted as the classification.
- **Descriptions are data.** Descriptions are length-capped and shown to the owner at approval.
  They enter the model's context only through `tool_search`/`tool_describe` results.
- **Output is untrusted.** Results are capped, wrapped in an explicit "untrusted content from
  <server>" frame, and set a **taint flag** on the run (section 13.4).
- **Per-agent allow-lists.** A profile lists servers or single tools (`mcp:outreach`,
  `mcp__github__create_issue`). Not listed = not callable, enforced by `dispatch(allowed=)`.
- **Long calls** run as background tasks under the existing `TaskManager`, with the 90 s voice
  ceiling unaffected because voice only ever calls `handoff`.

**The outreach agent, concretely.** It is exposed as one MCP server, say `outreach`, with tools
like `find_leads`, `draft_message`, `send_message`, `campaign_status`. The owner approves the
server; marks `find_leads`, `draft_message` and `campaign_status` as `SAFE`; leaves
`send_message` as `GUARDED`. A `growth` worker profile lists `mcp:outreach`. The first send shows
a card naming the recipient and the message; the owner may click Always allow with a scope and a
daily cap (section 13.2). No Kravein code is written for outreach.

### 8.2 Plugins

A plugin is a folder, enabled by the owner, that bundles any of:

```
~/.local/share/kravein/plugins/<name>/
  plugin.json          name, version, description, what it contains, required secrets
  skills/<x>/SKILL.md  procedures (section 9)
  agents/<x>/AGENT.md  worker profiles
  mcp.json             MCP servers this plugin wants (still approved one by one)
  tools/*.py           optional Python @skill modules      ← "code plugins" only
  hooks.py             optional registry hooks             ← "code plugins" only
```

**Two trust tiers, decided by content, shown in the UI:**

- **Declarative plugins** (SKILL.md, profiles, MCP config): cannot execute code in Kravein's
  process. Enabling one is a normal owner click.
- **Code plugins** (Python tools or hooks): run inside Kravein with its privileges. Enabling one
  is a `CRITICAL`, owner-only card that shows the file list and a hash, and the hash is pinned.
  Code-plugin tools must still call `safety.enforce` themselves for state changes; the
  `skill.unenforced` tripwire covers them too, and a plugin tool can never register below the
  danger level its manifest declares.

**Hooks.** The earlier harness plan said "not doing: a hooks system". I recommend a deliberately
small one now, because plugins and MCP need a seam and the alternative is editing `registry.py`
for each: `pre_tool(call)`, `post_tool(call, result)`, `run_end(run)`. A hook may **deny** or
**annotate**; it can never approve, never change a verdict downward, and never see secrets.
Hook failures deny for `pre_tool` and are logged and ignored for the others.

**Not now:** a plugin marketplace, auto-install, auto-update. Hermes' community-skill scanner has
had three CVEs **[report 05; third-party mirrors]**; a regex scanner is not a boundary.

**Risks**

- The "lethal trifecta": private data + untrusted content + an outward channel in one agent
  **[report 02]**. MCP gives Kravein all three. The controls are: per-agent allow-lists so one
  worker rarely holds all three, `GUARDED` outward tools, and the taint rule that suspends grants.
- A malicious MCP server that behaves until approved. Pinning catches definition changes, not
  behaviour changes; outward and destructive effects still hit a card or a capped grant.
- Owner fatigue from classifying tools. Mitigation: the default class is safe to leave alone.

---

## 8A. Computer use (seeing the screen, operating apps with no API)

**Today [code].**

- Two vision skills exist. `look_at_screen` sends a downscaled screenshot (1280 px wide, JPEG) to
  `LLM.vision` and returns a description. `click_on_screen(description)` asks the vision model
  for "x,y", builds a `DESTRUCTIVE` verdict, asks the owner, then moves and clicks once
  (`skills/interact.py`).
- Plain input skills (`type_text`, `press_keys`, `mouse`, `scroll`) are `MODERATE` and never call
  `safety.enforce`.
- Coordinates are absolute logical pixels of the whole desktop. There is no per-monitor or
  scale handling in any skill.
- Pointer placement is only reliable on Hyprland: `place_pointer()` moves through the compositor
  and verifies the position, because `ydotool mousemove --absolute` parks the pointer in a corner
  and exits 0 **[code: `inputs.py:312-326`, hard-won-desktop]**. `click_on_screen` does not use
  `place_pointer`; it uses the broken path.
- On niri there is no `move_cursor`, `pointer_position()` returns nothing and `screen_size()`
  returns (0,0), so `click_on_screen` refuses. Niri has window and whole-screen capture only when
  `grim` is missing.
- There is no accessibility-tree use, no OCR, and no emergency stop. The nearest thing is
  `gentle.py`, which ends its scroll session when the owner takes the pointer back.
- `LLM.vision` tries Gemini, then three free OpenRouter vision models, then Cline.

So today Kravein can describe the screen anywhere, and click one described thing, with a card
per click, on Hyprland only.

### When to use the GUI at all: the capability ladder

A rule for the agent and for anyone adding capability, in this order. The `desktop` worker must
state which rungs it ruled out and why; that line goes into the run journal.

1. **An API or an MCP server** for the service.
2. **A shell command, a file, or a CLI** the app ships (most "apps" on Linux are files and
   commands underneath).
3. **The browser's DOM** if the thing is a website (section 8B).
4. **Compositor and desktop interfaces:** window actions through `platform/desktop`, D-Bus
   methods, and keyboard shortcuts typed into a focused, verified window.
5. **The accessibility tree** (AT-SPI) where the app exposes one: act on a named button, read a
   field's value.
6. **Screenshot + pointer:** only for what is left (canvas apps, some Electron apps, games,
   remote desktops).

Each rung down is slower, costs more per step, is less verifiable and reads more untrusted
content. Report 05's lesson applies here too: terminal and file tools cover most needs.

### Options for rung 6

| | A. One vision model returns coordinates (today) | B. Grounding model (UI-TARS style) | C. Planner + grounder split (Agent S3 style) | D. Provider computer-use tools (Claude, Gemini, OpenAI) | E. Accessibility tree (AT-SPI) |
|---|---|---|---|---|---|
| How | Ask a general vision model "where is X" | A small model trained to turn "the Send button" into pixels | A strong text planner decides the step; a grounder finds the pixels | The provider defines the action schema; the model returns `click`, `type`, `scroll`, `zoom` … and the client executes | Read the app's widget tree; act by element |
| Accuracy on small targets | Weak; general models miss by tens of pixels **[judgment; this is why grounders exist]** | Good on common UIs | Best reported open results: Stonic ships exactly this, gpt-5.6-sol + UI-TARS **[report 04]** | Strong on the newest models; vendor and blog figures disagree, so not quoted here **[web: weak]** | Exact where it exists |
| Cost per step | 1 vision call | 1 small call | 2 calls | 1 call on a mid or strong model | no model call to read; text tokens only |
| Lock-in | none | none (open weights, also hosted) | none | per provider; each has its own schema | none |
| Fit to Wayland | needs Kravein's capture and input | same | Agent S3 itself drives `pyautogui`, which is X11/Windows-oriented; Stonic's sidecar was ~300 MB **[report 04]** | fine: the client executes, so Kravein's capture and input are used | AT-SPI still works over D-Bus under Wayland; the Wayland-native replacement (Newton) is experimental **[web: GNOME a11y blog 2024, BRLTTY list 2026]** |
| Coverage | any pixels | any pixels | any pixels | any pixels | GTK and Qt apps; Chromium/Electron only when accessibility is switched on **[judgment, to verify]**; terminals and canvas apps poorly |

**Recommendation: build one provider-neutral executor in Kravein, and plug models into it
(D and B/C behind the same interface), with E as a helper.**

- Kravein owns **observe** (capture), **act** (input) and **verify**, because those are the
  parts tied to Wayland, to the hard-won desktop rules and to `safety`. No vendor tool or
  third-party agent gets to drive the pointer directly.
- The **model side is a role**, `computer` (default: inherits `vision`). Two adapters:
  (1) a provider computer-use tool, mapped to Kravein's action set. Claude's current tool type is
  `computer_toolset_20260801` **[claude-api skill]**; Gemini's built-in computer use returns
  actions on a 0–999 grid **[web: third-party blogs, unverified]**. (2) planner + grounder: the
  worker's own text model plans, a grounding model (for example UI-TARS through an
  OpenAI-compatible endpoint) returns the point.
  Which one wins on the owner's apps is an eval question, not a design question (D23).
- **Do not embed Agent S3 or a browser-use-style desktop agent.** They bring their own input
  layer and their own loop, which is the "second agent stack" every earlier plan ruled out.
- **AT-SPI is used opportunistically:** if the target window exposes a tree, the observation
  includes a short outline (role, name, bounds) and the model may act by element. That makes the
  step exact and lets code verify state. It is a helper, not a dependency.

### Design

**The session.** GUI work happens only inside a `desktop_session` opened on **one named window**
(app, title, workspace, pid: resolved through the existing `_resolve_one`, never the first
match). The session object holds the target, the caps and the stop flag. All GUI tools refuse
outside a session.

**The loop** (inside the `desktop` worker, a normal profile):

```
observe → model proposes ONE action + what it expects to change → pre-checks (code)
        → act → re-observe → verify (code where possible) → next step or stop
```

**Observe.**
- Capture **the target window only** (or one output), never the whole multi-monitor bounding
  box. `grim -g <geometry>` on Hyprland and Sway; `niri msg action screenshot-window` on niri.
- The observation given to the model is the image plus, when available, the AT-SPI outline and
  the window title.
- An owner **deny-list of windows** (password manager, banking, private chats) is checked before
  capture; a matching window is never captured and the session cannot target it.

**Coordinates, monitors and scaling.**
- The model works in **fractions of the captured image (0–1)**, whatever the adapter's native
  grid is. This is Stonic's choice and it removes DPI from the model's problem **[report 04]**.
- Code maps a fraction to a logical desktop point: `window origin + fraction × window logical
  size`. The scale factor is never used directly; the ratio between the image size and the
  window's logical size already contains it, including fractional scaling.
- The target window's geometry is re-read before every action. If it moved, resized, lost focus
  or closed, the action is dropped and the step re-observes.

**Act.**
- Pointer: only through a **verified placement backend**. Hyprland has one today
  (`place_pointer`). Niri and Sway need one before pointer actions are allowed there; the
  candidates are the `wlr-virtual-pointer` protocol (absolute motion) or a compositor action,
  to be checked on the niri laptop first, as `niri-desktop.md` requires. Until then the session
  on niri is **keyboard-only** (focus, shortcuts, typing), which already covers a lot.
- Keyboard: `wtype`/`ydotool` with numeric key codes; Ctrl+C then Ctrl+U before typing into a
  terminal; these hard-won rules stay.
- Before every input event: the focused window must be the session's target (code check). This
  is the guard against a popup or a focus steal receiving the keystrokes.
- After the session: restore the owner's workspace and focus, as the window skills already do.

**Verify the effect, not the acknowledgement.** Synthetic input cannot be confirmed by its exit
code, so every action carries the model's stated expectation and code checks what it can:

| Check | Source |
|---|---|
| A window opened, closed or changed title | compositor window list |
| A field now holds the typed text; a checkbox is ticked | AT-SPI value/state, when exposed |
| A file was saved or exported | filesystem (mtime, size, hash) |
| The view changed at all | image difference of the target window before/after |
| Anything else | `verified: unknown` |

An image difference proves that *something* changed, not that the right thing did; such steps
count as `unknown`, and a run whose last state-changing step is `unknown` is badged
`not confirmed` (section 14). The worker may ask the `look_at_screen` path a closed question
("is the dialog titled Export visible? yes/no") as evidence, recorded as model-read evidence,
which is weaker than a code check and labelled so.

**Safety.**

- **Danger classes.** Opening a session on an app is `GUARDED`: one card naming app, window
  title, workspace and pid. Always allow may be granted **per app**. Inside a session, pointer
  and key actions are `MODERATE`, with three code-enforced exceptions:
  1. the element or nearby text matches the irreversible list (send, pay, delete, publish,
     confirm, …; the list in `skills/browser.py` moves to `safety`) → a card with the cropped
     screenshot of the target;
  2. the session is about to leave its window (a new window or dialog appears from another
     app) → the session pauses and asks;
  3. anything matching payment or credential entry → refused (below).
  This replaces "every click is `DESTRUCTIVE`", which makes multi-step GUI work impossible, and
  it needs the owner's ruling because it touches "destructive always asks" (D15).
- **An honest limit.** A coordinate click is opaque: code cannot know what a pixel does. The
  keyword check is a heuristic, not a boundary. The real containment is the one-window scope,
  the taint rule, the caps, the stop key, the deny-list and the fact that money and credentials
  are refused outright.
- **Taint.** A screenshot is untrusted content (a web page, an email or a chat on screen can
  carry instructions). Any run that has observed the screen is **tainted** (section 13.4):
  outward `GUARDED` grants are suspended for the rest of the run.
- **Secrets are never typed.** The worker has no way to read a secret (`check_read`), typing
  into a password field is refused when AT-SPI reports one, and the profile's rule is: on a
  login prompt, stop and `ask_owner` to log in. The text passed to `type_text` is checked
  against the secret patterns `audit` already redacts.
- **Stop.** Three layers, strongest first: (1) a compositor key binding that runs
  `kravein stop` (the owner adds the bind; it needs no key-reading privilege; synthetic input
  can at worst trigger it, which only stops the agent); (2) pause when the owner's own pointer
  or keyboard activity is detected where the compositor exposes it (the `gentle.py` takeover
  check, generalised); (3) optionally, a reader on the physical evdev keyboard that ignores the
  ydotool uinput device, Stonic's physical-Esc idea **[report 04]**. Layer 3 needs read access
  to `/dev/input`, which is also the ability to log every keystroke, so it is an owner decision
  and not my default (D16).
- **Caps in code:** 40 steps and 10 minutes per session by default, one session at a time, a
  per-day cost cap on the `computer` budget line, and a visible on-screen indicator while a
  session holds the pointer.
- **Never unattended.** A GUI session takes over the owner's seat, so triggers and goals cannot
  open one; they park a proposal instead. (A later option is a nested or headless compositor so
  GUI work does not need the owner's pointer; not in this plan.)
- **Privacy.** Screenshots go to a cloud model. The deny-list, window-only capture and the
  per-app session card are the controls; the owner should decide this knowingly (D17).

**Cost and model role.**

- One step is one screenshot plus the running history. A 1280-px window capture is on the order
  of 1–1.5k input tokens on current vision models, and a 30-step task with history is roughly
  60–150k input tokens **[judgment; replace with ledger data]**. That is a few cents on a cheap
  vision model and roughly $0.15–0.40 on a Sonnet-class model at the prices in section 16.
  Third-party pages quote $0.02–0.15 per task for hosted computer use **[web: weak]**.
- Old screenshots are the first thing compaction drops: keep the last two, replace older ones
  with their one-line observation.
- Role: `computer`. It is probed like any model, plus a **click-target test**: 20 screenshots of
  the owner's own apps with a known target box, scored by whether the returned point lands
  inside it. A model that fails the test cannot be selected for the role.

**Risks**

- Niri has no verified pointer path today; the phase may ship keyboard-only there.
- Visual prompt injection is real and cheap to mount (text in a web page or an image). Taint and
  the one-window scope bound the damage; they do not stop a wrong click inside the window.
- GUI steps are slow (seconds each) and brittle to theme, language and layout changes. This is
  the reason for the ladder, and for turning a successful GUI procedure into a SKILL.md with
  shortcuts rather than replaying clicks.
- Session-level approval is weaker than a card per click. That is the trade D15 asks about.

---

## 8B. Browser automation

**Today [code].**

- `platform/browser.py`: Playwright (sync API on one dedicated thread), a **persistent profile
  of Kravein's own** (`~/.local/share/kravein/chrome-profile`), the real Chrome channel, headed,
  one shared context. The active page is simply the last open tab.
- Nine `browser_*` skills (open, two searches, read, click, type, scroll, tab, quit) plus the
  no-browser web skills (`web_search`, `fetch_page`, `open_url`).
- Elements are found from a text description; several matches return a numbered list instead of
  a guess.
- Safety: only `browser_click` calls `safety.enforce`, and only when the *description text*
  contains a word from an irreversible list (send, submit, buy, pay, …), as `DESTRUCTIVE`.
  **`browser_type(submit=True)` presses Enter with no check at all**, so a form can be submitted
  without a card today.
- `accept_downloads=True` is set but nothing handles a download. There are no uploads, no cookie
  import, no CDP attach. Errors are plain strings.

### Options

| | A. Playwright, Kravein's own profile (today) | B. Attach over CDP to the owner's real browser | C. Vision browser agent (browser-use style) | D. A browser MCP server (Playwright MCP, Chrome DevTools MCP) |
|---|---|---|---|---|
| Reliability | High: DOM selectors, auto-waiting | Same engine, but shares tabs with the owner | Lower and slower: every step is a screenshot | High; default view is an accessibility snapshot **[web: secondary]** |
| Logged-in sessions | The owner logs in once inside Kravein's profile; sessions persist | All of the owner's sessions at once | Depends on the launcher | Its own profile, or a bridge extension into the real browser **[web: secondary]** |
| Detection and blocking | A real headed Chrome with a lived-in profile is the least conspicuous automation setup, but still detectable | Often claimed to be the least detected; claims are vendor blogs, no controlled test found **[web: weak]** | Same as its launcher | Same as its launcher |
| Safety | **Best: Kravein sees the element (role, name, form, URL) before acting and can classify the action** | Worst: the agent holds every account the owner has, including banking and personal mail | Actions are pixels; nothing to classify | The server is a separate process; Kravein sees a tool name and arguments only |
| Notes | Already built and tested | Chrome 136+ refuses remote debugging on the default profile, so a real attach now means a copied profile or an extension **[web: Chrome developer blog]** | Needs a vision model on every step | Good as an optional plugin, weak as the core path |

**Recommendation: A, upgraded.** Keep the in-process Playwright path and Kravein's own profile,
and add what business work needs. The deciding argument is safety: in-process, code can read
the element and the form before the click, which is what makes per-action classification real
rather than a keyword match on the model's own description.

- **B only by explicit owner choice, later, per site** (D18). Giving the agent the owner's whole
  logged-in browser is the "one agent with broad authority" pattern report 02 warns about.
- **C is not a separate stack.** When a page is a canvas or resists the DOM, the browser tools
  hand a *page screenshot* to the same executor as section 8A, scoped to the browser window.
- **D stays possible** as a plugin (section 8), classed `GUARDED` like any MCP server.

### Design

**Reading the page: snapshot with references.** Add `browser_snapshot`: a compact text outline of
the page from the accessibility tree (role, name, value, state) where each interactive element
has a short reference (`e12`). `browser_click`, `browser_type` and the others accept a reference
as well as a description. This is the technique Playwright MCP and Chrome DevTools MCP use
**[web: secondary]**; it works with any text model, costs far fewer tokens than a screenshot,
and gives code a concrete element to classify. Description matching stays as the fallback.

**Accounts, cookies and sites.**
- The profile directory stays inside Kravein's private tree, so no file tool can read cookies.
- A **site table** owned by the owner: `site → {read, interact, post}`. Unknown site: read only.
  The first interaction on a logged-in site asks; Always allow is offered **per site and per
  action kind** ("post on example.com, up to 3 a day").
- The owner logs in himself, in Kravein's browser window. The agent never types a password or a
  one-time code: password fields are detected from the DOM (`type=password`,
  `autocomplete=current-password|one-time-code`) and typing into them is refused. A login wall
  or a CAPTCHA returns a structured `blocked_by_login` / `captcha` error and the run asks the
  owner.
- Optional second profile for a second identity (business vs personal); profiles are never mixed
  in one run.

**Action classes, decided from the element and the page, not from the model's words:**

| Action | Class | How code knows |
|---|---|---|
| Open a URL, read, snapshot, scroll, switch tab | `SAFE` / `MODERATE` | — |
| Type into a normal field; click a link or a non-submitting button | `MODERATE` | element role and type |
| Submit a form, press Enter inside a form, click a `type=submit` button, or a button whose name is on the irreversible list, **on a site where the owner is logged in** | `GUARDED` | DOM: `form`, `type=submit`, key event target; site table |
| Post, send, publish, comment, message | `GUARDED`, and it must reference an approved draft (below) | irreversible list + site table |
| Delete or remove data, close an account, cancel a subscription | `DESTRUCTIVE` | irreversible list (delete family) |
| Pay, check out, transfer money, enter card data | always an **owner-only** card, no grant possible; my default is to refuse and let the owner do the last click (D20) | payment fields (`autocomplete=cc-*`), known checkout URL patterns, pay/buy family |
| Upload a file | `GUARDED`; the file must pass `check_read` and live in the workspace or a path the owner named | `input[type=file]` |

The `submit=True` gap is closed as part of phase 1's re-classification, before anything else in
this section.

**Approved drafts (how posting stays safe when the run is tainted).** Reading any page taints
the run, and there is deliberately **no same-site exception** for the browser: a post on site X
that read site X is exactly how an injected instruction would arrive. Instead:

1. The worker writes the content as a **draft** (text, target site, account, attachments) in the
   workspace.
2. The owner approves the draft, or a batch of drafts (a card, the proposals queue, or a goal's
   content calendar).
3. `browser_post(draft_id)` types that draft. Code compares what is in the field with the
   approved draft's hash before the submit; a mismatch refuses.

A grant can then cover "publish approved drafts on example.com, up to N a day" even in a tainted
run, because both the content and the destination were fixed by the owner.

**Downloads and uploads.**
- Downloads land in `workspace/downloads/<run>/`, size-capped, with the source URL recorded.
  They are never executed or opened automatically; running or installing one is a separate
  action with its own verdict. A downloaded file is untrusted content for the taint rule.
- Uploads: table above. The card shows the file path, size and the site.

**Structured, retryable errors.** Browser tools return a small fixed shape, rendered as text for
the model (Stonic's idea **[report 04]**):

```
{ok, code, retryable, suggestion, evidence}
codes: element_not_found · ambiguous (with candidates) · not_interactable · navigation_timeout ·
       page_changed · blocked_by_login · captcha · rate_limited · download_started · refused
```

`retryable` tells the loop whether another attempt is sensible; `suggestion` says how
("take a new snapshot", "wait for the URL to change"). The loop guard (section 5) counts
retries. Existing honest messages such as "submitted but the page did not change" become
`verified: no` evidence.

**Verify the effect.** After an action: URL change, a specific element appearing or
disappearing, a field's value, a download file on disk, or for a post, the permalink of the new
item read back from the page. A post without a read-back permalink is `not confirmed`.

**Detection and bot-blocking, stated plainly.**
- No stealth plugins, fingerprint spoofing or CAPTCHA solving. They are an arms race, they put
  the owner's accounts at risk of bans, and they are evasion.
- Pace limits per site in code (actions per minute, posts per day), below human rates.
- Where a site forbids automation in its terms, the options are its official API (as an MCP
  server), or **assist mode**: Kravein prepares everything and the owner makes the final click
  (D19). For outreach specifically, the owner's outreach agent over MCP remains the path.

**Tabs and focus.** A run works in **its own tab**, identified by id, not "the last tab"; the
existing `pages[-1]` choice is replaced. Two runs never share a tab.

**Relation to the rest.**
- Ladder for web work: `web_search`/`fetch_page` (no browser) → browser DOM tools → page
  screenshot through the 8A executor.
- Research uses the first rung almost always. A `web-operator` worker profile holds the browser
  tools; the Orchestrator keeps only open/read.
- 8A's GUI session is never pointed at the browser window when the DOM path works.

**Risks**

- Sites change their markup; snapshots and references are regenerated each step, and failures
  come back as `element_not_found` with a suggestion rather than a wrong click.
- Account bans on platforms that forbid automation. Controls: pace limits, no evasion, API first.
- A logged-in profile is a store of account access. It sits in the private tree; the site table
  keeps unknown sites read-only; payment is refused.
- Classifying from the DOM is strong for real forms and weak for single-page apps that post from
  a `div` click; those fall back to the irreversible name list and to the approved-draft rule,
  which does not depend on detecting the button.

---

## 9. Skills (procedures, SKILL.md)

**Naming first.** Kravein uses "skill" for a Python tool function and `SKILL.md` for an agent
profile. With procedures added that is three meanings. Recommendation: in docs and UI, **tool** =
a callable function (the `@skill` decorator name can stay), **skill** = a SKILL.md procedure,
**agent profile** = `AGENT.md`. The loader accepts the old `SKILL.md` profile name during
migration.

**Options**

| Option | Notes |
|---|---|
| Put procedures in the system prompt | What the profiles do today; every procedure costs tokens on every call |
| **Agent Skills format with progressive disclosure (recommended)** | An open format; nothing model-specific; adopted by several tools **[report 03; adoption counts are secondary]**; Kravein's profile parser is already close |
| A custom recipe format | No reason to differ from a portable standard |

**Design**

- **Index in the prompt:** one line per skill, name + a description capped at 80 characters
  (Hermes caps at 60 for the same reason **[report 05]**). The index is part of the stable
  prefix and is rebuilt only at session start.
- **`load_skill(name)`** returns the body; bundled scripts and references are read with the
  normal file tools from the skill's folder, which is read-only to the agent.
- **Sources, in trust order:** `bundled` (repo), `plugin`, `owner` (written by the owner),
  `learned` (drafted by the learning pass, approved by the owner; section 11).
- **Per-profile visibility:** a profile lists skill names or tags, like tools.
- **Scripts inside a skill** are ordinary shell commands to `safety`: they get the same verdicts
  as anything else `run_command` runs. A skill is instructions, never a permission.
- **Usage telemetry:** load count, last used, runs that ended `done` after loading it.

Examples the owner's work needs early: "publish a blog post", "weekly client report", "set up a
new client project folder", "review yesterday's outreach replies".

**Risks.** A wrong skill makes the agent confidently wrong in a repeatable way. That is why
learned skills are proposals with a diff, and why telemetry and archive rules exist.

---

## 10. Memory

**Today [code].** One JSON file with five fixed categories; the first 40 items are dumped into
every system prompt (`skills/memory.py:62-82`); writes only when the model calls `remember`;
conversation history is SQLite per (profile, thread) with a prose summary; no FTS anywhere.

**Options**

| Option | For | Against |
|---|---|---|
| Keep JSON + dump | none | Unranked, truncated at 40, changes the cached prefix on every write |
| Hosted or heavy memory (Mem0, Zep/Graphiti, Letta server) | Rich features | Extra services and an LLM call per write; their benchmark claims are vendor-reported and disputed between vendors **[report 03]** |
| Embeddings-first vector store | Semantic recall | A model dependency and cost per write; weak on names, numbers and Roman Urdu spelling variants |
| **Files + SQLite FTS5, agent-driven search (recommended)** | Local, cheap, inspectable; a plain filesystem agent scored 74% on LoCoMo against Mem0's reported 68.5% **[report 03; Letta's own blog]** | Keyword recall misses paraphrases (Stonic measured 55–65% for keyword recall alone **[report 04]**) |

**Recommendation: four layers, all local.**

1. **Profile files, always in context, bounded.** `KRAVEIN.md` (owner's instructions, already
   exists, agent cannot write it) and a new `USER.md` (who the owner is, standing preferences;
   ≤ 2,000 chars) and `ENV.md` (machine and business facts; ≤ 3,000 chars). Loaded as a **frozen
   snapshot at session start**: a mid-session write goes to disk and applies next session, so the
   prompt prefix stays cacheable.
2. **Fact store.** SQLite table `facts(id, text, kind, subject, source, origin, confidence,
   created, last_used, superseded_by, status)`. Three FTS5 indexes: `unicode61`, `trigram`, and a
   **consonant-skeleton index for Roman Urdu** so "mausam" / "mosam" / "mosum" match (Stonic's
   idea **[report 04]**; small, local, and specific to this owner). Ranking: reciprocal-rank
   fusion × recency × confidence.
3. **Session search.** FTS5 over the existing `messages` table, plus run reports. A no-LLM
   `search_history(query, when)` tool. Trigger and cron sessions are ranked below owner sessions
   so they do not drown them.
4. **Run journal.** What each run tried, what failed, what worked (already mostly in the audit
   log and reports). This is the raw material for learning, not something put in the prompt.

**Retrieval.** Two paths: an automatic prefetch of the top 3–5 facts for the turn's text, placed
in the volatile `[context]` tail next to the clock (never in the system prompt), and a `recall`
tool the model calls when it needs more. If keyword recall proves weak in the eval, the fallback
is a "recall pass": the `utility` model reads the whole fact store in one cached call and returns
the relevant lines (Stonic's recall agent). Embeddings (`sqlite-vec`) are a later option behind
the same interface, only if the eval shows a gap.

**Writes, by origin (enforced in code, tagged on every row):**

| Origin | Example | Rule |
|---|---|---|
| `owner-said` | "remember that I invoice on the 1st" | saved at once |
| `owner-implied` | the learning pass infers a preference from the owner's own messages | saved as `candidate`, shown in a memory inbox, used only after approval or after N days without objection (owner's choice, D8) |
| `tool-derived` | a fact read from a web page, an email or an MCP result | **proposal only**; never saved without the owner |

Every write is audited with before/after and can be undone from the UI. `forget` stays
`DESTRUCTIVE`.

**Consolidation.** A nightly job, **rule-based**: merge exact and near-duplicates, mark a fact
superseded when a newer one has the same subject and kind, decay confidence of unused candidates.
No LLM rewriting of the store (Stonic's comments describe an LLM shrinking 18k tokens of memory
to 122 **[report 04]**).

**Migration.** The existing JSON is imported once as `owner-said` facts; identity and top
preferences go to `USER.md`. The `memory` tool keeps its name and actions.

**Risks.** Memory poisoning through tool-derived text: handled by the origin rule. A stale fact
followed forever: handled by supersede, `last_used` and the inbox. Private data in one SQLite
file: it sits in the private tree, 0600, which the agent's file tools cannot read.

---

## 11. Self-learning

"Self-taught" has to mean something checkable. The proposal is four loops, weakest and safest
first.

| Loop | What is learned | How | Gate |
|---|---|---|---|
| 1. Preferences | "the owner prefers X", "never do Y" | Deterministic signals first: a declined card, an explicit correction ("no, use …"), an undo. The learning pass turns them into candidate facts. | Memory rules in section 10 |
| 2. Procedures | A reusable way to do a class of task | After a run that ended `done` with ≥ N tool calls, the review pass may draft a SKILL.md (or a patch to one it loaded) | **Always a proposal with a diff** |
| 3. Failure lessons | "tool Z fails on this machine without W" | Runs that ended `failed`/`partial`, or `done` after a retry | Saved as `ENV.md` candidates or skill patches; proposal |
| 4. Capability gaps | "I could not do X; an MCP server for Y would cover it" | Already exists as `capability` proposals **[code: proposals.py]** | Proposal |

**The review pass** (Hermes' background review, tightened **[report 05]**):

- Runs after a turn ends normally, on the `utility` role, only for owner-live sources, at low
  priority and under the daily budget. Never inside a voice turn.
- A fork with a **dispatch-side tool whitelist**: read the run, read skills and memory, and
  `propose`. It has no write tools at all, so a prompt injection in the run's content can at most
  create a proposal the owner then reads.
- **Tainted runs** (section 13.4) may produce only `note` proposals, never skill or memory writes.
- The prompt is biased to **do nothing**: classes not incidents, no dates or one-off names, no
  environment failures presented as rules, no unverified workflow presented as validated. (Hermes
  tells its reviewer to "be ACTIVE"; with a human gate on every change that bias would just fill
  the owner's inbox.)
- **Curator**, deterministic: a learned skill unused for 30 days is flagged, for 60 days is
  archived; nothing is deleted; the owner can pin.
- **Tie to evals:** when a learned skill or lesson is approved, the run it came from can be saved
  as an eval scenario with one click, so "it got better" becomes a number over time.

**What I do not recommend:** automatic prompt or profile rewriting, fine-tuning, or any learning
that changes behaviour without an owner-visible diff. There is no independent evidence that
unsupervised skill writing improves outcomes; the 40% speed-up figure is the vendor's own
**[report 05]**.

**Risks.** Inbox fatigue (cap: 3 learning proposals per day, merged by topic). Drift from a
wrong approved skill (telemetry + archive + the linked eval scenario).

---

## 12. Proactivity: heartbeat, cron, events

**Today [code].** A systemd timer runs `jobs tick` every minute for jobs and routines. Agent
jobs build a profile-less all-tools agent with no confirmer, and refusals are detected by
matching strings in tool results (`jobs.py:574-640`). The only event source is a D-Bus
notification watcher that runs only when the model starts it. A daily review job exists, off by
default. The owner's standing rule is no autostart: Kravein runs when started by hand.

**Design: everything is a `Trigger`.**

```
cron job ─┐
heartbeat ├─► Trigger{kind, source, payload, dedupe_key} ─► pre-filter (code) ─► triage run
events ───┘                                                  dedupe, cooldown,    (Orchestrator-
                                                             quiet hours, budget   unattended)
```

- **Cron** = today's jobs and routines, unchanged in storage and timing rules (UTC, at-most-once
  claim, lock file, no heavy import on an empty tick **[code: hard-won-scheduler]**). The change:
  a due agent job calls `Runtime.submit` instead of building its own agent, and its outcome comes
  from the run's status, not from string matching.
- **Heartbeat** = one internal job, default every 30 min, **only while the owner is idle or
  away and no run is active**. Its prompt is an owner-editable `HEARTBEAT.md` checklist ("any
  outreach replies? anything failing? anything due today?"). Missed ticks coalesce into one. A
  reply of `[SILENT]` produces nothing at all. "Do not invent work" is in the prompt, and the
  budget cap is what actually enforces it.
- **Events** = a small in-process bus with adapters, each a plugin-style module: desktop
  notifications (existing watcher, started at boot when enabled), file changes in named folders
  (inotify), system state (battery, disk, a failed systemd user unit), task finished, phone
  events, and **MCP polling** (a server-declared "what is new since X" tool called on a
  schedule; this is how "a lead replied" arrives without Kravein owning email code).
- **The pre-filter is code:** dedupe by key, per-source cooldown, quiet hours, and a rules list
  the owner edits. Most events never reach a model.

**A triage run can end in exactly four ways:**

| Result | Example | Needs |
|---|---|---|
| Nothing | `[SILENT]` | — |
| Notify | "Disk is at 91%." | a notification, or a spoken line if a voice session is open |
| Propose | "3 leads replied; I drafted answers. Send?" | the proposals queue; owner approves on web, phone or by voice-then-card |
| Act within grants | file the daily report into the workspace; run `find_leads` (SAFE) | an existing grant or SAFE/MODERATE tools only |

Triage runs use the `orchestrator-unattended` profile: read-mostly tools, **no confirmer**.
Anything that needs a card becomes a **parked approval**: the run stops in `waiting_approval`
with the exact action recorded, the owner sees it later, and approving resumes the run under the
owner's live confirmer. Expiry (default 24 h) denies. Nothing about being unattended ever
approves.

**Anti-loop and cost rules, in code:**

- A triggered run cannot create jobs, triggers or heartbeats (Hermes' default **[report 05]**).
- Preflight before any model call: keys present, the delivery channel works, budget remains.
- Per-day caps on triggered runs and on tokens for the `triage` budget line; at the cap,
  triggers queue as a single digest for the owner.
- A triggered run cannot `delegate` to more than one worker and cannot use `GUARDED` grants that
  are not flagged for unattended use (section 13.2).

**Proactive speech.** Kravein never opens a voice session by itself. It notifies (and may chime);
if a session is already open, the line is delivered `WHEN_IDLE`.

**Dependency on "always on".** Proactivity only exists while the server runs. With the current
no-autostart rule, triggers work between `kravein start` and `kravein stop`. That is a coherent
choice; it just has to be a conscious one (D5).

**Risks.** A noisy event source burning budget (caps + cooldowns + digest). A proposal flood
(the queue already caps at 20 pending and refuses duplicates **[code]**). Stale parked approvals
running in a changed world: the action is re-validated by `safety` at resume time, and the card
shows when it was requested.

---

## 12A. Long-lived goals and projects

**Today [code].** Nothing outlives a run except scheduled jobs, daily routines (ordered habit
steps with soft locking) and proposals. There is no goal, objective or milestone object;
"projects" is only a free-text memory category; a proposal has no parent link
(`agents/proposals.py`). So "get 5 new clients this month" can today only be a reminder.

### Options

| Option | For | Against |
|---|---|---|
| A. Jobs + memory notes (stretch what exists) | No new object | Nothing holds the metric, the budget or the scope; progress is whatever the model says |
| B. A self-driving goal loop with a judge model (Hermes `/goal`, "Ralph" style) | Simple; works for a coding task in one session | The judge is a model grading a model; Hermes' judge fails open and relies on a turn cap **[report 05]**. Wrong shape for a month-long business goal |
| C. A kanban board with a worker process per card (Hermes Kanban, Agent Town) | Visible, parallel | A second scheduler and worker pool beside the Runtime; cost multiplies per card |
| **D. A durable Goal object driven by the existing triggers and Runtime (recommended)** | Reuses runs, jobs, proposals, grants, ledger; the goal only adds state, scope and a review cadence | New object, new UI pane; needs discipline to keep it small |

**Recommendation: D.** A goal is not a new kind of agent. It is a record that owns some jobs and
a review trigger and stamps a `goal_id` on ordinary runs.

### Design

**The Goal object** (table `goals` in `agents.db`):

| Field | Meaning |
|---|---|
| `objective` | The owner's words: "Get 5 new clients this month" |
| `metric` | `{kind, source, target, current, history}`; see "honest progress" |
| `deadline` | A date; optional for "keep X healthy" goals, which have a review period instead |
| `budget` | Money cap for the goal, tokens and runs per day, and per-tool caps (emails a day, posts a week) |
| `scope` | Which worker profiles, MCP servers, sites and **which existing grants** its runs may use |
| `cadence` | Review schedule (daily, weekly) and briefing schedule |
| `plan` | Milestones and tasks, each with done criteria; owner-approved |
| `status` | `draft → active ⇄ paused → achieved / missed / cancelled`, plus `blocked` |
| `journal` | Runs, evidence, decisions, spend |

**Who may do what (code-enforced).**
- Only the owner creates and activates a goal. The agent may suggest one as a proposal of kind
  `goal`.
- Activation shows a card with the scope and the budget. **Activating a goal grants nothing.**
  Grants are still made one by one on real cards; the goal's scope only names the *subset* of
  existing grants its runs may use, so a goal run cannot borrow an unrelated grant.
- A goal cannot change its own objective, metric, budget, scope or deadline. Each of those is a
  proposal to the owner.
- Pause and cancel are available to the owner in the UI, on the phone and by voice at any time,
  and take effect before the next step of any running goal run. Pausing is a low-risk action;
  resuming and widening are owner-only.

**From goal to work.**

```
owner sets goal ─► planning run (read-only) ─► plan proposal ─► owner approves
        ┌──────────────────────────────────────────────────────────┘
        ▼
  code creates the goal's jobs (recurring tasks) and its review trigger
        │
        ├─ task run (Runtime, goal_id, unattended rules) ─► evidence into the journal
        ├─ task run …
        └─ review (cadence) ─► continue │ re-plan proposal │ blocked: ask owner │ report
```

- **Planning** is one read-only run that produces milestones, recurring tasks, the metric
  definition and a budget estimate. The owner approves the plan as a whole.
- **Jobs are created by code at plan approval**, from the approved plan. This is the one place a
  goal causes jobs to exist, and it is the owner's click that does it; section 12's rule that a
  triggered run cannot create jobs or triggers is unchanged.
- **Task runs** go through `Runtime.submit` with `goal_id`. They are unattended runs: `SAFE` and
  `MODERATE` tools, plus grants that are both in the goal's scope and flagged for unattended
  use. Everything else parks as an approval the owner sees in the goal's card.
- **Review** is a trigger (section 12). Code first collects the metric and computes pace
  (expected by today vs actual), spend vs budget, failed runs and waiting approvals. Then a
  triage run reads that summary and the journal and picks one of the four trigger outcomes. A
  material change to the plan is a re-plan proposal; small reordering inside the approved plan
  is allowed.
- **Blocked is detected by code:** three failed runs in a row on the same task, an approval
  waiting longer than its limit, or a budget line exhausted sets `blocked` and notifies the
  owner once. The goal does not keep spending while blocked.
- **The heartbeat** (section 12) includes "active goals with something due or waiting" in its
  checklist input, so goals are how the heartbeat finds real work instead of inventing it.

**Honest progress: the metric is collected by code.** The model never writes `metric.current`.
A metric names a **collector**:

| Collector | Example | Evidence |
|---|---|---|
| `mcp` | Call a `SAFE` tool on an approved server and read a number: outreach `campaign_status.replies` | the tool result, stored |
| `http` | "Keep the client's site healthy": status code, response time, certificate days left, a text that must be on the page | the response facts |
| `journal_count` | "Post 3 times a week": count runs of the posting task whose outcome is `done` with a read-back permalink | the verified runs |
| `file` | A number in a sheet or file in the workspace | file hash and value |
| `owner_confirmed` | "A client signed": a real-world fact only the owner knows | the owner's click, with date |

- An **outcome metric** (clients signed) usually needs `owner_confirmed`. **Activity metrics**
  (emails sent, replies, calls booked) are shown beside it as leading indicators, labelled as
  such, and never count as the goal.
- A task counts as done only by the run outcome rule of section 14. "Sent 40 emails" means 40
  verified sends in the journal.
- `achieved` is set by code when the collector reaches the target; `missed` when the deadline
  passes. The model writes the commentary, not the status.

**Staying bounded.**
- Budget lines per goal in the usage ledger (section 16); at the cap the goal pauses itself and
  says so.
- Per-tool caps from the goal (emails a day, posts a week) are enforced where the grant cap is
  enforced; the lower of the two wins.
- A limit on active goals (start with 2) and on goal runs per day.
- A goal run cannot open a GUI session (8A) and cannot pay (8B).
- A deadline always ends the goal; "keep X healthy" goals have a renewal date at which the owner
  re-confirms.
- Nothing self-approved: every card, grant, plan change and scope change is the owner's act.

**What the owner sees and hears.**
- **Goals pane** in the web UI and the phone: one card per goal with the metric bar (number from
  the collector, with its timestamp), pace (ahead / on track / behind), spend vs budget, next
  planned tasks, waiting approvals, and the last three pieces of evidence, each linking to its
  run.
- **Daily briefing:** a short notification at a time the owner sets. By voice it is spoken when
  the owner asks ("Kravein, briefing") or when a session is already open; Kravein does not open
  a voice session by itself. The numbers in a briefing are inserted by code; the model writes
  the sentences around them.
- **Weekly report:** a document in the workspace per goal: what was done (with evidence links),
  the metric history, spend, what is blocked, and the proposed plan for next week, which the
  owner approves or edits.

**The three examples**

| Goal | Metric and collector | Work | Asks the owner for |
|---|---|---|---|
| "Get 5 new clients this month" | outcome: `owner_confirmed` signed clients; leading: `mcp` replies and calls booked | `growth` worker through the outreach MCP server: find leads, draft messages, send within grant and cap, follow up; weekly re-plan | the first sends (then a capped grant), message templates, each "signed" confirmation |
| "Post 3 times a week" | `journal_count` of verified posts per week | `content` worker drafts a week of posts; owner approves the batch; scheduled jobs publish approved drafts (8B) | the weekly draft batch |
| "Keep this client's site healthy" | `http` checks every N minutes; uptime and response time | a cheap check job with no model call; a model run only when a check fails, ending in a notification or a proposal | any fix that changes the site |

**Risks**

- **Optimising the wrong number.** An agent chasing "emails sent" becomes a spammer. Controls:
  the goal is the outcome metric, activity is capped not rewarded, and content goes out as
  approved drafts.
- **Reputation and account risk** from outward work at volume. Controls: caps, pace limits, no
  evasion, the owner's templates.
- **Cost creep** over weeks. Controls: per-goal budget line, pause at the cap, spend on the card.
- **A promise the system cannot keep.** No agent can guarantee five clients. The honest product
  is: the work gets done on schedule, the numbers are true, and the owner hears early when it is
  behind.
- **Stale goals** that nobody reads. Controls: the deadline, the renewal date, and a goal with no
  owner interaction for two review periods pauses itself.

---

## 13. Autonomy within the safety rules

### 13.1 What stays exactly as it is

Hard refusals (protected paths, PID 1, the compositor, Kravein's own config and state), the
decline policy, the per-turn denial memory keyed by target, the thread-local confirmer, deny on
timeout / dropped socket / unknown id / no confirmer, `CRITICAL` never auto-approved, secret
reads and owner-only cards never answered by a rule, the `skill.unenforced` tripwire, the audit
log. **[code: `safety.enforce`, lines 2113–2260]**

### 13.2 The new class: `GUARDED`

Today there are four danger levels and confirmation starts at `DESTRUCTIVE`. "Risky but not
destructive" has no home: an MCP `send_message` is not data loss, so it would either run
silently (`MODERATE`) or be lumped with `rm` (`DESTRUCTIVE`), where the owner said Always allow
must not exist.

```
SAFE        read-only                                   never asks
MODERATE    visible, reversible, local                  never asks
GUARDED     outward-facing, spends money or quota,      asks; "Always allow" creates a grant
  (new)     unknown MCP tool, acts on an account
DESTRUCTIVE data loss, process death, overwrite         always asks (owner decision 2026-11-13)
CRITICAL    whole system, package removal, sudo         always asks, owner-only
```

**A grant** is created only by the owner clicking Always allow on a real card (the server writes
it; no tool, plugin, hook or agent can). Fields:

- `principal`, `tool`, and a **scope signature computed by code** from the arguments the tool
  declares as scope-relevant (for `send_message`: channel + campaign, not the message text; for a
  file-writing MCP tool: the folder). The answer from the UI never supplies the signature, as
  with today's rules **[code: engine.md #199]**.
- `cap` (optional uses per day) and `expires` (optional). For outward-facing tools the card
  offers "Always allow, up to N per day" as the default choice.
- `unattended: false` by default. A separate, explicit toggle lets a grant apply to triggered
  runs. Without it, a triggered run parks the action for approval.
- Listed and revocable in the web UI (`/api/safety/rules` already exists), audited on create,
  on every use and on revoke.

### 13.3 Order of checks in `enforce` (changes in bold)

1. blocked → refuse
2. below `GUARDED` → allow
3. policy `decline` → refuse
4. target already declined this turn → refuse and end the turn
5. **`GUARDED` + matching grant + (attended, or grant flagged unattended) + run not tainted +
   under cap → allow, audit `safety.grant_used`**
6. confirmer present → ask; any failure denies
7. no confirmer → record in denial memory; **park the action for approval** if the run is
   durable, else refuse

### 13.4 The taint rule

A run becomes **tainted** when untrusted external content enters its context: web page text, an
MCP result, a file from outside the workspace, a notification body, a sub-agent report that was
itself tainted. In a tainted run, grants for **outward-facing** `GUARDED` tools are suspended and
the card comes back, unless the untrusted content came from the same MCP server the grant is for
(the outreach agent reading its own leads and then sending is the normal flow).

This is the code-level answer to prompt injection: injected text can ask for anything, and the
worst it gets without the owner is a card. It costs some convenience (a research-then-send flow
asks again); that is the intended trade.

### 13.5 Other autonomy tools that already exist and stay

- **Proposals** for anything the agent itself thinks of that is major.
- **Checkpoints and undo** for file changes. Extend `checkpoints.track` to MCP tools that
  declare a local path they write.
- **Sandbox** (#227, in flight): dry runs and inspection in bwrap with no network.
- **Workspace freedom:** inside `~/.local/share/kravein/workspace` file writes are `MODERATE`
  already; workers should do their drafting there by default.

### 13.6 Two conflicts with today's code (owner rulings needed)

- **Auto-approve mode** (#35): a 1-hour timer during which `DESTRUCTIVE` actions from live owner
  sources run without a card. This contradicts "destructive always asks" (D2).
- **The three DESTRUCTIVE "Always" families** (#199): `pkill NAME`, `mv` over an existing file in
  $HOME, non-recursive `chmod` in $HOME. Same contradiction (D3).

**Risks.** Grant creep: many broad grants add up to no confirmation. Mitigations: narrow
code-computed scopes, caps, a monthly "review your grants" proposal, and unused grants expiring
after 60 days. A wrong scope function for an MCP tool makes a grant too wide: scope fields are
chosen by the owner at tool approval, with the narrowest as default (all arguments).

---

## 14. Verification and self-correction

The owner's definition of "perfect" has four parts. Each gets a mechanism in code, because
"never say done unproven" is in the prompt today and is listed among the rules the model can
ignore **[report 01]**.

| Requirement | Mechanism |
|---|---|
| Verify the effect of its own work | **Typed tool results.** A state-changing tool returns `{ok, verified: yes \| no \| unknown, evidence}`. Many skills already check the effect (window really closed, file really written); this makes it a field. Synthetic input and MCP calls with no read-back report `unknown`, honestly. |
| Catch and correct mistakes | A failed or `verified: no` step returns a model-facing error naming what was observed. The loop allows a bounded retry (2) and the loop guard blocks the identical call. For workers, each plan item must be closed with evidence or marked failed; `update_plan` already holds the plan in code. Optional escalation: one retry of the failed step on the next model in the role's chain. |
| Know when unsure, and ask | `ask_owner(question, options[])`: a question card (text) or a spoken question (voice), with the run parked in `waiting_owner`. Ambiguous targets already refuse and list candidates **[code: hard-won-desktop]**; the same pattern applies to people, accounts and files for MCP tools: never the first match. |
| Never claim unproven success | **The outcome is computed by code** from the step log: `done` only if every state-changing step is `verified: yes` (or the tool is declared unverifiable and says so); otherwise `not confirmed` or `partial`. The UI badge and the voice front's closing line come from this status. The model's prose is displayed, but it cannot upgrade the badge. |

Also:

- **Reviewer** stays for high-risk work only (packages, services, deletions, anything sent to
  many recipients). It should run on a *different* model from the worker when one is configured,
  so the check is not the same blind spot twice.
- **Done criteria** are part of the handoff packet, so "done" is checked against something
  written before the work started.
- **Pre-flight for outward actions:** a draft is always produced and shown (or stored) before a
  send; the send tool takes the draft id, so what was approved is what goes out.

**Risks.** `verified` fields can be faked by a careless tool author; the eval suite includes
"claimed done but the fixture says otherwise" checks, which the current eval harness can already
express with its file and outcome checks **[code: `tools/eval_tasks.py`]**. Over-asking: cap
`ask_owner` per run and log every question so the rate is visible.

---

## 15. Evals

**Today [code].** `tools/eval_tasks.py`: 13 system-ops scenarios, isolated HOME, code-only checks
(14 check kinds), baseline 11/13 and 10/13 on flash-lite before the harness upgrade. First-tool
choice sets for four profiles. The post-harness run, the Orchestrator eval and the tool-routing
eval were all deferred for cost.

**Options**

| Option | For | Against |
|---|---|---|
| LLM-judge evals | Easy to write | Costs tokens, noisy, and the judge shares the models' blind spots |
| **Outcome evals with code checks (keep, recommended)** | Deterministic, cheap to grade, already built | Each scenario needs a fixture |
| Vendor benchmarks | Free | Not this workload; "benchmark with the real tool schema" is already a hard-won rule |

**Recommendation: three tiers, sized so that cost never blocks running them.**

1. **Tier 0, free, every commit.** The prompt-surface snapshot (bytes and token counts of each
   profile's prompt and `tools[]`), schema-size budgets, the safety test suite, and a
   **replay test**: recorded model replies fed through the loop to check outcome computation.
2. **Tier 1, smoke, cents per run, on every prompt / tool / model change.** About 12 scenarios
   across profiles, chosen to cover: a delegated task, a refusal with a safer alternative, an
   ambiguous target, a tool-search path, an MCP call against a **mock MCP server**, a
   `GUARDED` card, a taint case (injected text asks for a send; expected: card, not send), a
   "claimed done but not verified" case, a memory recall. Run on the role's configured model.
3. **Tier 2, full, on demand** and before changing a role's default model: all scenarios × the
   candidate models, three runs each (the two baseline runs already differ: 11 vs 10).

**Outputs that feed decisions**

- **Model scorecard** per (model, role): pass rate, rounds to completion, tokens, cost, latency,
  bridge-call retry rate. Shown next to the model picker in the web UI. This is how "which model
  for which job" is decided with the Orchestrator later, as the owner wants.
- **Scenarios from real life:** any run can be turned into a scenario from the audit log
  (prompt + fixture description + expected outcome), which is also where learned skills get
  their regression test.
- **Voice** has its own small set: the existing 13 spoken requests **[code: engine.md #195]**
  plus the 12-clip transcript A/B, run when the Live model changes.

**Risks.** Evals that only measure what is easy to check. The mitigation is the "from real
failures" pipeline: every bug the owner reports becomes a scenario before it is fixed (already
the engineering bar for code bugs).

---

## 16. Model routing and cost

**Today [code].** Five providers behind `LLM.complete` with sequential failover and a persisted
breaker. One chain for everything; a profile may state a preference (`prefer="provider:model"`)
but none does. No usage or cost is recorded anywhere. Settings on this machine lead with
`gemini-3.5-flash-lite`; the chain behind it is dead here **[report 01]**.

**Options**

| Option | For | Against |
|---|---|---|
| One model everywhere (today) | Simple | The cheap model's rate limit and latency break delegation; no way to spend more where it matters |
| An LLM gateway dependency (LiteLLM, OpenRouter only) | Many models at once | LiteLLM is a large dependency in the request path; OpenRouter-only adds a middleman and its own limits. Kravein already has provider classes and the hard-won fixes inside them (thought signatures, tool caps) |
| A "router model" that picks the model per request | Adaptive | An extra call per turn, hard to debug, and the owner wants to choose |
| **Roles → owner-chosen chains (recommended)** | Explicit, cheap, testable, matches the owner's decision | The owner has to choose; the scorecard is there to make that easy |

**Design**

- **Roles:** `voice`, `orchestrator`, `worker:<profile>`, `utility` (summaries, review pass,
  translation, triage pre-read), `judge` (reviewer), `vision`. Unset roles inherit:
  `worker:*` → `orchestrator`; `utility` → the cheapest configured.
- **Settings:** `models.roles.<role> = ["provider:model", ...]` (an ordered chain: first is the
  choice, the rest are failover). The web UI shows one picker per agent and one for each
  non-agent role, with the scorecard and the last 7 days of spend beside it.
- **Catalog with verified capabilities.** `models.catalog` holds, per model: provider, price
  (input / cached / output), context, max tools, tool calling **verified on this account**
  (yes / no / untested), supports streaming, needs opaque-block replay. `kravein models probe
  <id>` fills it by making real calls. A model that is not probed cannot be picked for an agent
  role. ("Model ids are not the catalogue" **[code: hard-won-llm-agent]**.)
- **Providers.** Keep Gemini. Replace the three near-identical OpenAI-wire classes (NovaRouter,
  Zen, OpenRouter) with one **generic OpenAI-compatible provider** configured per endpoint
  (base URL, key name, tool cap, timeout); that reaches DeepSeek, Z.ai (GLM), Zen, OpenRouter
  and local servers. Add an **Anthropic provider** on the official SDK for Claude. Keep Cline
  only if the owner still uses it (D7).
- **Provider-specific facts the adapters must hide** (each has bitten already or will):
  Gemini thought signatures **[code]**; Claude's current models reject forced tool choice and
  need their thinking blocks replayed unchanged, which is the same "opaque block" mechanism as
  `ToolCall.signature` **[claude-api skill]**; tool-count caps on some gateways **[code]**.
  History therefore stores an opaque per-provider blob per assistant step, and a run **stays on
  one model** unless it fails over, in which case the opaque blocks are flattened to text as the
  Gemini path already does.
- **Usage ledger.** Every model call writes `(ts, run, agent, role, provider, model, input,
  cached, output, cost, latency, ok)` to SQLite. Cost comes from the provider's reported usage
  × the catalog price ("read the `cost` field, not `upstream_inference_cost`" **[code]**).
  Voice minutes are logged the same way.
- **Budgets, enforced in code:** a daily cap per budget line (`interactive`, `workers`,
  `triage`, `learning`, `voice`). At 80% the owner is told; at 100% the line stops and says so
  (interactive never stops silently: it falls to the cheapest chain entry and shows a banner).
- **Escalation on failure, not by default:** a worker run that ends `failed`/`partial`, or a
  step that fails verification twice, may be retried once on the next entry of the chain if the
  owner enabled "escalate" for that role.
- **Prompt caching:** keep the prefix byte-stable (frozen memory snapshot, stable `tools[]`,
  clock in the tail: all already the design). Record `cached` tokens in the ledger so a broken
  cache is visible as a number.
- **Failover that works:** the chain is validated at start-up and in `kravein doctor`; a role
  whose chain has fewer than two live entries shows a warning in the UI.

**Candidates (prices per 1M tokens, input / output; not assignments)**

| Model | Price | Source quality | Notes for Kravein |
|---|---|---|---|
| `gemini-3.5-flash-lite` | $0.30 / $2.50 | [web: aggregators agree; one lists half, probably batch] | Current model. 15 req/min on the free tier is what breaks delegation [report 01] |
| DeepSeek V4.1 Flash | not verified today | — | Named by the owner; already wired through Zen in code defaults. Probe before use |
| GLM-5.3-Flash (Z.ai) | $0.15 / $0.50, cached $0.03 | [web: third-party; tool calling not confirmed from Z.ai docs] | Named by the owner. Tool calling must be probed with the real schema |
| `claude-haiku-5-5` | $0.10 / $0.50 (≤100K prompt) | [claude-api skill table, cached 2026-11-03] | At list price it is cheaper than flash-lite. Worth a Tier 2 run for `orchestrator` and `utility` |
| `gemini-3.8-flash` | $0.75 / $3.75 until 2026-12-31, then double | [web: third-party reports] | Mid tier; the price change matters for any long-term choice |
| `claude-sonnet-5-5` | $2 / $10 | [claude-api skill table] | Candidate for hard worker tasks and `judge` |
| `claude-opus-5-5` | $4 / $20 | [claude-api skill table] | Candidate for rare planning or review escalations |
| Gemini Live (`voice`) | ~$0.023 / min | [report 02] | 30 min a day ≈ $20 a month; wake-word gating is the main cost control |

A rough feel for scale **[judgment, to be replaced by ledger data]**: an Orchestrator turn with a
9k-token prefix and three rounds is about 30k input tokens. On a $0.15–0.30 model that is under
one cent uncached and far less with caching; on Sonnet 5.5 it is about 6 cents. A 25-round worker
task at 60k tokens is about 2 cents on a cheap model and 15–30 cents on Sonnet. Multi-agent work
multiplies this; triggers multiply it by their frequency. This is why the ledger ships first.

**Risks.** Cross-model behaviour differences make one prompt wrong for another model; profiles
may carry short per-model addenda, and Tier 1 runs on the configured model. Price tables go
stale; the catalog has an `as_of` date and `doctor` warns after 60 days.

---

## 17. Keep, change, remove

**Keep as is**

| Piece | Why |
|---|---|
| `registry.py` (`@skill`, schema from signature and docstring, `dispatch`, `allowed`, alias presets, tripwire) | It is the spine, and it is better than Hermes' hand-written schemas [report 05] |
| `safety.py` core: verdicts, argv classification, read/write path checks, denial memory, `confirmer_scope`, decline policy | Stricter than every system surveyed; every rule in it is a paid-for bug |
| `platform/` and the single-subprocess rule | Makes the audit trail complete; MCP stdio goes through it too |
| `audit.py`, `events.py`, `webauth.py`, `server.py` transport, phone channel | Working, tested, and the confirmation plumbing lives here |
| Proposals queue, checkpoints + undo, `update_plan`, `TaskManager`, per-principal threads | These are the autonomy primitives the new design leans on |
| `jobs.py` / `routines.py` timing and storage | The hard-won scheduler rules stay intact |
| Outcome eval harness | Extended, not replaced |
| WhatsApp inbound: five gates, fixed vocabulary, never touches the model | Do not widen it |

**Change**

| Piece | Change |
|---|---|
| `Agent` | Split: a `Run` engine (loop, budget, compaction, outcome) under a `Runtime`; streaming; usage; parallel SAFE calls; degrade on budget |
| `live.py` | From Orchestrator to thin front; `handoff`; 3.8 non-blocking; compression; GoAway; wake word |
| `llm.py` | Roles and chains; generic OpenAI-compatible provider; Anthropic provider; usage; catalog + probe |
| Profiles | `AGENT.md` name, core vs deferred tools, MCP and skill lists, role-based model |
| `delegate.py` | `submit_report` tool, handoff packet, `ask_parent`, inactivity timeout |
| `skills/memory.py` | Same tool, new store (files + SQLite FTS5), origin-tagged writes |
| `jobs.run_job`, `daemon.py`, daily review | Submit to the Runtime; outcome from run status |
| `safety.enforce` | `GUARDED` class, grants, taint, park-for-approval |
| Danger levels of existing outward skills (anything that sends or posts) | Re-classify to `GUARDED` |
| `platform/browser.py` + `skills/browser.py` (keep Playwright and the own profile) | Add snapshot-with-references, per-run tabs, DOM-based action classes, site table, drafts, downloads/uploads, structured errors (8B) |
| `skills/interact.py` input and vision skills | Become the executor of a `desktop_session` on one window: fractional coordinates, verified pointer backend, focus check, caps (8A) |
| `platform/screen.py`, `platform/inputs.py`, `platform/desktop.py` | Window-scoped capture on every compositor; a verified pointer path for niri and Sway; optional AT-SPI reader |
| Proposals | New kinds `goal` and `plan`; a `goal_id` link on proposals, jobs and runs (12A) |

**Add (new in v4)**

| Piece | Why |
|---|---|
| `goals` table, collectors, review trigger, Goals pane, briefing | Long-lived objectives with code-collected progress (12A) |
| `desktop` and `web-operator` worker profiles; `computer` model role | GUI and browser work stay out of the Orchestrator's tool set |
| Stop binding (`kravein stop`) and an on-screen session indicator | The owner can always take the machine back |

**Remove**

| Piece | Why |
|---|---|
| The `confirm=true` sentence in `SYSTEM_PROMPT` | Dispatch strips it; it teaches a dead path |
| The 40-item memory dump in the system prompt | Replaced by snapshot files + retrieval |
| `toolselect.py` keyword routing (after tool search passes its eval) | Superseded; hint words move to the search index |
| String-matched refusal detection in `jobs.py` | Replaced by run status |
| Duplicate OpenAI-wire provider classes | One generic provider |
| Profile-less all-tools agents on unattended paths | Replaced by `orchestrator-unattended` |
| Live voice's direct tool dispatch as Orchestrator (`_run_tool_as_orchestrator`, the 60-tool declaration) | The thin front replaces it; keep the old path one release behind a flag |
| `click_on_screen` as a one-shot skill with a `DESTRUCTIVE` card per click, and its `ydotool` absolute-move path | Replaced by the session executor (8A); the absolute move is the documented-broken path |
| The description-keyword gate as the *only* browser safety check, and the ungated `browser_type(submit=True)` | Replaced by DOM-based classes (8B); the gap closes in phase 1 |
| "Active page = last tab" (`pages[-1]`) | Replaced by a tab per run |
| Possibly: auto-approve mode and the DESTRUCTIVE "Always" families | Only if the owner rules so (D2, D3) |
| Possibly: the local STT→LLM→TTS chain as a default path | Keep as the offline fallback; decide after the voice phase |

---

## 18. Migration plan

Rules for every phase: it ships behind a setting that defaults to today's behaviour until its
acceptance criteria pass; it adds tests; it adds its Tier 1 scenarios; the hard-won rules hold.
Sizes: **S** = one PR, a few days of agent work. **M** = 2–4 PRs. **L** = 5 or more PRs across
modules. Phases 0–2 are strictly ordered; 3–5 can run in parallel across module agents; 6–8
depend on 2. The v4 phases: **3B** (browser) needs 1 and 2; **4B** (computer use) needs 3B and
the niri pointer check; **6B** (goals) needs 2 and 6, and is better after 5.

### Phase 0 — Models you can choose and see (M)

- **Goal.** The owner picks a model per agent in the web UI and sees what each one costs.
- **Main work.** Roles and chains in settings; generic OpenAI-compatible provider (DeepSeek,
  Z.ai, Zen, OpenRouter); Anthropic provider; `Reply.usage`; usage ledger; catalog and
  `kravein models probe`; model picker and spend view in the web UI; `doctor` chain check;
  prompt-surface snapshot test; remove the `confirm=true` prompt line.
- **Acceptance.** Changing the Orchestrator's or a worker's model in the UI takes effect on the
  next turn without a restart. Every model call appears in the ledger with tokens and cost. An
  unprobed model cannot be selected for an agent. A role with one live chain entry shows a
  warning. A live tool-calling turn passes on at least one cheap model and one Claude model.
  Existing suite green.
- **Usable result.** Cost visibility and working failover, today's behaviour otherwise.

### Phase 1 — MCP, permissions and plugins (L)

- **Goal.** The outreach agent works through Kravein, safely. New capability arrives as an MCP
  server or a plugin folder.
- **Main work.** `kravein/mcp/` on the official SDK (stdio via `platform/shell`, Streamable
  HTTP); bridge into the registry; pinning and diff; owner approval and classification UI;
  `GUARDED` class, grants with scope / cap / expiry / unattended flag, grants UI; taint flag and
  rule; re-classify outward skills, **including closing the ungated
  `browser_type(submit=True)` path (8B) with a regression test first**; plugin loader
  (declarative tier first, code tier behind the CRITICAL card); minimal hooks; `growth` profile;
  mock MCP server for tests and evals.
- **Acceptance.** With the outreach server approved: "find 10 leads for X" runs without a card;
  the first send shows a card naming recipient and campaign; after Always allow with a cap of N,
  sends inside the scope run without a card until N, then ask. A changed tool definition
  disables the tool and shows a diff. In a run tainted by a web page, an outward grant does not
  apply. A tool not in the profile's list is refused in dispatch (`skill.profile_refused`). No
  unattended path can create or use a grant not flagged unattended. Every failure path of the
  new card denies (timeout, dropped socket, unknown id). No subprocess outside `platform/shell`.
- **Usable result.** Client hunting and outreach by voice or text, with the owner in control of
  what is sent.

### Phase 2 — One brain: Runtime and loop upgrades (L)

- **Goal.** Every request and every trigger goes through the same Orchestrator loop, and long
  work survives pauses.
- **Main work.** `Runtime.submit`; `runs` table and parked approvals; `orchestrator-unattended`
  profile; jobs, daemon and daily review moved onto it; degrade-on-budget at top level;
  tool-result clearing and between-turn summaries; parallel SAFE calls; streaming and in-flight
  cancel; loop guard; `submit_report`, handoff packet, `ask_parent`, inactivity timeout; typed
  tool results and code-computed outcome with UI badge.
- **Acceptance.** No code path constructs an agent outside the Runtime (a test greps for it). A
  scheduled agent job that hits a card ends `waiting_approval`; approving it in the UI resumes
  it under the owner's confirmer; letting it expire ends `declined`; a restart in between
  changes neither. A 30-round Orchestrator task returns a partial answer, not an error. Stop
  ends an in-flight provider call within 2 s. A scenario where the model says "done" while the
  fixture shows no change is badged `not confirmed`. System-ops Tier 2 is not worse than the
  baseline on the same model.
- **Usable result.** Honest status badges, resumable approvals, no more "Stopped after 8 rounds".

### Phase 3 — Tool diet and tool search (M)

- **Goal.** About 20 visible tools per agent; hundreds reachable.
- **Main work.** Core vs deferred in profiles; `tool_search` / `tool_describe` / `tool_call`;
  BM25 index with Roman Urdu hints; snapshot budgets; promotion suggestions; retire
  `toolselect.py`.
- **Acceptance.** Orchestrator `tools[]` ≤ 12 KB and system-ops ≤ 15 KB (from 29 and 41). Tier 1
  pass rate with search on is at least equal to search off, on the configured model, over three
  runs. First-tool-choice sets do not regress. A deferred tool outside the profile is refused
  through `tool_call`. Cached-token share in the ledger rises on multi-round turns.
- **Usable result.** Cheaper and faster turns; cheap models choose tools better; MCP servers can
  be added without bloating every call.

### Phase 4 — Voice front and wake word (M–L)

- **Goal.** Say "Kravein", talk, and get one consistent assistant.
- **Main work.** Thin-front `LiveSession` mode; `handoff` with scheduling on `gemini-3.8-live`;
  degraded blocking mode on 3.1; compression, GoAway, seed rebuild; wake-word process and
  custom model; idle close; chime; per-day voice-minute cap; web and phone audio paths kept.
- **Acceptance.** The 13 spoken requests: right action at least as often as today's 13/13 on
  3.1. Median time to first audio after end of speech ≤ 2 s for instant actions and for the
  handoff acknowledgement. The 12-clip transcript A/B on 3.8 is recorded and the default pin
  chosen from it. A 25-minute session survives two reconnects without losing the thread. A
  handed-off task's result is spoken at the next pause; a waiting card is announced at once.
  The voice front never reports a run as done before the Runtime does (test with a slow fake
  run). Wake word: under one false open per hour in a recorded hour of the owner's normal room
  audio; a session with no speech closes in 5 s.
- **Usable result.** Hands-free Jarvis-style use at a bounded per-minute cost.

### Phase 5 — Memory (M)

- **Goal.** Kravein remembers the owner and past work, and can find it.
- **Main work.** `USER.md` / `ENV.md` snapshots; fact store with three FTS5 indexes; session
  search; prefetch in the context tail; origin-tagged writes; memory inbox and undo in the UI;
  rule-based nightly consolidation; one-time import of the JSON.
- **Acceptance.** The system prompt is byte-identical across a session in which memory was
  written. A recall set of 30 questions (10 English, 10 Roman Urdu, 10 spelling variants) built
  from the owner's real facts reaches ≥ 80% top-5. A fact from a web page cannot be saved
  without an approval (test). Every write can be undone. Old `memory` tool calls still work.
- **Usable result.** No more 40-item cap; "what did we decide about X last week" works.

### Phase 6 — Proactivity (M)

- **Goal.** Kravein notices and offers, inside a budget.
- **Main work.** `Trigger` type and pre-filter; heartbeat job and `HEARTBEAT.md`; event bus with
  notification, file, system and MCP-poll adapters; triage profile; four-outcome contract;
  digest at the cap; quiet hours; UI for triggers and their spend.
- **Acceptance.** With the server running and the owner idle: a new lead reply (mock MCP)
  produces one proposal with a draft within one heartbeat. A full disk produces one
  notification, not one per tick. A `[SILENT]` triage produces no frame, no notification and no
  history entry in the owner's thread. A triggered run cannot create a job or a trigger (test).
  The daily `triage` cap stops model calls and yields one digest. An empty tick still imports no
  heavy modules.
- **Usable result.** Morning briefing, reply alerts, "this needs your attention" without asking.

### Phase 7 — Learning (M)

- **Goal.** Kravein gets better at the owner's recurring work, visibly and reversibly.
- **Main work.** SKILL.md loader, index and `load_skill`; skill sources and telemetry; the review
  fork with a read + propose whitelist; proposal kinds `memory`, `skill`, `lesson` with diffs;
  curator; "save this run as an eval scenario".
- **Acceptance.** After the same multi-step task is done twice, a skill proposal appears with a
  readable diff; approving it makes the third run load the skill and finish in fewer rounds
  (measured in the ledger). The review fork cannot call any write tool (dispatch test). A
  tainted run yields only `note` proposals. At most 3 learning proposals a day.
- **Usable result.** A growing, owner-approved library of "how we do things here".

### Phase 8 — Eval habit and scorecards (S–M, starts in phase 0 and finishes here)

- **Goal.** Model and prompt choices are made on numbers.
- **Main work.** Tier 1 set completed across phases; Tier 2 runner over (model × role);
  scorecards in the UI; scenario-from-audit tool; voice set automation.
- **Acceptance.** One command runs Tier 1 and prints pass rate, cost and time; the full Tier 1
  run costs under $0.25 on the cheap models; the UI shows a scorecard for every probed model.
- **Usable result.** The owner and the Orchestrator can decide "which model for which job" from
  data, as planned.

### Phase 3B — Browser operator (M–L) *(v4)*

- **Goal.** Kravein can do real work on websites the owner is logged in to, with each outward
  step under the owner's control.
- **Main work.** `browser_snapshot` with element references; reference arguments on the action
  tools; a tab per run; DOM-based action classification (form submit, payment and password
  fields, irreversible names) in `safety`; the site table and per-site grants; drafts and
  `browser_post(draft_id)` with the hash check; download quarantine and upload checks;
  structured errors; pace limits; `web-operator` profile; a local test site (static pages with a
  login form, a post form, a payment form, a file input) for tests and Tier 1 scenarios.
- **Acceptance.** On the test site: reading and filling a form needs no card; submitting on a
  logged-in site shows a card that names the site and the action; with a per-site grant and cap
  it runs without a card until the cap. `browser_type(submit=True)` can no longer submit
  ungated (regression test). Typing into a password field is refused. A payment form always
  produces an owner-only card (or a refusal, per D20) and no grant can answer it. A post whose
  field content differs from the approved draft is refused. In a run that read a page, a posting
  grant does not apply unless the post is an approved draft. A download lands in the quarantine
  folder and is not opened. Every error carries `code` and `retryable`. Two concurrent runs use
  two tabs. Existing browser tests stay green.
- **Usable result.** "Post this week's approved posts", "fill in this client form", "pull the
  numbers from that dashboard" work by voice or text.

### Phase 4B — Computer use (L) *(v4)*

- **Goal.** Kravein can operate one app window that has no API, safely and visibly.
- **Main work.** `desktop_session` (one resolved window, caps, stop flag, indicator);
  window-scoped capture on Hyprland, niri and Sway; fractional coordinates and the mapping;
  focus check before every input; a verified pointer backend for niri (checked on the niri
  laptop first) or keyboard-only mode there; the `computer` role with one provider computer-use
  adapter and one planner + grounder adapter; optional AT-SPI outline; per-action verification;
  the irreversible-target card with a cropped image; window deny-list; `kravein stop` binding
  documented for the owner; `desktop` worker profile; click-target probe; screenshot compaction.
- **Acceptance.** A session cannot start without a card naming app, title, workspace and pid,
  and cannot be opened by an unattended run. Input is refused when the focused window is not the
  target (test with a stolen focus). On a two-monitor, fractional-scale layout, 20 scripted
  click targets in a test app land inside their boxes on Hyprland. On niri, either the same
  passes or pointer actions refuse with a clear message and keyboard-only tasks pass. A session
  stops within one step of `kravein stop`, at 40 steps and at 10 minutes. A deny-listed window
  is never captured. A run that observed the screen cannot use an outward grant. A scripted
  task in a test GUI app ("open Preferences, tick X, save") ends `done` with code-checked
  evidence; the same task with the save step blocked ends `not confirmed`. Typing a string that
  matches a secret pattern is refused. The click-target probe gates the `computer` role.
- **Usable result.** "Export this from the app that has no CLI" works, slowly and honestly.

### Phase 6B — Goals and projects (L) *(v4)*

- **Goal.** The owner can hand Kravein an objective for weeks, see true progress, and stop it at
  any time.
- **Main work.** `goals` table and state machine; `goal` and `plan` proposal kinds; planning
  run; code-side job creation at plan approval; `goal_id` on runs, jobs, proposals and ledger
  rows; collectors (`mcp`, `http`, `journal_count`, `file`, `owner_confirmed`); pace and blocked
  detection; review trigger; per-goal budget lines and caps; scope check for grants; Goals pane
  on web and phone; daily briefing and weekly report; pause and cancel by UI and voice.
- **Acceptance.** No agent path can create, activate, resume or widen a goal (tests for each).
  Activating a goal creates no grant. A goal run cannot use a grant outside the goal's scope.
  `metric.current` changes only through a collector (a model attempt to set it is refused). With
  a mock outreach server: a one-week goal produces its scheduled runs, parks the first send for
  approval, respects the daily cap, and its card shows numbers equal to the mock server's.
  Three failed runs set `blocked` and stop spending. Reaching the budget pauses the goal.
  Pause takes effect before the next step of a running goal run. The deadline ends the goal as
  `achieved` or `missed` by the collector's number. The briefing's numbers match the collectors.
  An `http` health goal runs its checks with zero model calls while healthy.
- **Usable result.** "Get 5 clients this month", "post 3 times a week" and "keep this site
  healthy" become things Kravein works on and reports on, not reminders.

**Order of value if work must stop early:** 0 → 1 → 2 give the owner model choice, the outreach
agent and honest, resumable runs. Everything after that is additive. For the JARVIS goal the
next most valuable are 6 → 6B (proactive work on objectives) and 3B (the browser); 4B is the
most expensive capability per unit of use and can come last.

---

## 19. Open decisions for the owner

| # | Decision | Options | My recommendation |
|---|---|---|---|
| D1 | Approve the overall direction: one text brain, thin voice front, MCP/plugins first, custom core | approve / change | **Approve.** It is the smallest change that removes the two-brain split and matches your MCP-first rule |
| D2 | Auto-approve mode (#35): a 1-hour window where destructive actions run without a card. It contradicts "destructive always asks" | remove / keep but hidden / keep | **Remove.** Grants cover the convenience case for non-destructive work, and the sandbox and workspace cover fast iteration |
| D3 | The three destructive "Always" families from #199 (`pkill NAME`, `mv` over a file, `chmod`) | remove / keep as a named exception | **Remove**, to keep one simple rule: destructive = card. If closing a hung app by name every day is painful, keep only `pkill NAME` as a written exception |
| D4 | May an Always-allow grant work when you are away (triggered runs)? | never / per grant, off by default / always | **Per grant, off by default**, with a daily cap required for outward-facing tools |
| D5 | Proactivity needs the server running. Today the rule is manual start only | keep manual / autostart the server at login / autostart only the wake word and triggers | **Keep manual for now**; revisit after phase 6 shows what proactivity is worth to you |
| D6 | Spoken approval ("yes, do it") for cards | never / only for `GUARDED` / also destructive | **Never for now.** Cards on screen and phone. Revisit for `GUARDED` only, with the yes/no decided by code on a fixed phrase list |
| D7 | Which providers to keep | (a) Gemini + generic OpenAI-compatible + Anthropic; drop Cline, NovaRouter classes (b) keep all | **(a).** Fewer adapters to keep correct; any OpenAI-compatible endpoint is then a settings entry |
| D8 | Facts inferred from your own messages | approve each / auto-accept after 7 days in the inbox / auto-accept at once | **Auto-accept after 7 days**, always undoable. Facts from web or tools always need approval |
| D9 | Hooks: the earlier plan said "no hooks system" | stay without / minimal deny-and-annotate hooks | **Minimal hooks**, because plugins need a seam and hooks that can only deny cannot weaken safety |
| D10 | Code plugins (Python inside Kravein) | allow behind a CRITICAL owner-only card / declarative plugins only | **Declarative only until a real need appears**; MCP covers most cases with a process boundary |
| D11 | Voice model default | move to `gemini-3.8-live` after the A/B / stay on 3.1 in degraded mode | **Move after the A/B**; same price, and non-blocking tools need it |
| D12 | Daily budget caps (numbers) | set now / set after two weeks of ledger data | **After two weeks of ledger data**; start with warn-only |
| D13 | Rename profile files to `AGENT.md` and use "tool / skill / agent" consistently | yes / no | **Yes**, done once in phase 3 with the loader accepting both names |
| D14 | This folder | delete after the approved plan is written into `kb/plans/` / keep | **Delete after approval**, as the README says |
| D15 | GUI approval model (8A). Today every vision click is a destructive card | (a) a card per click (b) one card to open a session on a named app, then cards only for irreversible-looking targets (c) no cards inside an approved app | **(b).** A card per click makes multi-step GUI work unusable; (c) is too loose for pixels. Per-app Always allow is allowed for opening the session only |
| D16 | Stop key for GUI sessions | (a) a compositor key binding you add that runs `kravein stop` (b) also a physical-keyboard (evdev) reader | **(a).** (b) needs read access to all keyboard input, which is keylogger-level access for the Kravein process. Add it only if (a) proves unreliable |
| D17 | Screenshots of your windows go to a cloud vision model | allow with a window deny-list / allow only for apps you list / never | **Allow only for apps you list, plus the deny-list.** Start narrow; widen when you trust it |
| D18 | Browser identity (8B) | (a) Kravein's own Chrome profile, where you log in to the business accounts once (b) also attach to your everyday browser | **(a).** (b) hands the agent every account you have; Chrome also blocks debugging on the default profile now |
| D19 | Sites whose terms forbid automation (LinkedIn and similar) | official API or MCP only / assist mode: Kravein prepares, you click / automate anyway | **API or MCP first, assist mode otherwise.** No stealth or CAPTCHA solving; a banned business account costs more than the time saved |
| D20 | Payments in the browser or a GUI | refuse and let you do the last click / owner-only card each time, no grant | **Refuse for now.** Revisit with an owner-only card after the browser phase has run for a while |
| D21 | Goals: how many, and how much re-planning freedom | start with 1–2 active goals, plan approved by you, material changes are proposals / more freedom | **Start with 1–2 and plan approval.** Reordering inside the approved plan is free; budget, scope, metric and deadline are always yours |
| D22 | Briefing | daily notification + spoken on request, weekly report file / spoken automatically at a set time | **Notification + on request.** It fits the wake-word decision: Kravein does not open the mic by itself |
| D23 | Model for the `computer` role | a provider computer-use tool (Claude, Gemini) / planner + open grounder (UI-TARS) | **Decide by the click-target probe** on your own apps in phase 4B; build both adapters behind one interface. Do not choose now |

---

## 20. What I could not verify

- `gemini-3.8-live` behaviour on Roman Urdu transcripts and the exact scheduling enum: needs the
  A/B and an SDK check. Google's own pages disagree on session-resumption lifetime (2 h vs ~10
  min) and on the stable/preview label.
- DeepSeek V4.1 Flash price and tool-calling quality; GLM-5.3-Flash tool calling: no primary
  source reached today. Both must be probed with Kravein's real tool schema.
- Gemini prices are from aggregators; Claude prices are from a table cached on 2026-11-03.
- livekit-wakeword's accuracy claims are the maintainers' own. Nothing here was measured on the
  owner's voice or room.
- The figures quoted from Hermes, Stonic and the Anthropic engineering posts were not re-read by
  me; they come from reports 02–05.
- I did not run the test suite or any live model call in this session; this document changes no
  code.
- PR #227 (bwrap sandbox) was not merged in this checkout; the design assumes it lands.

Added in v4:

- Provider computer-use tools: only Claude's tool type (`computer_toolset_20260801`) comes from a
  maintained reference. Gemini's built-in computer use, its 0–999 grid and all benchmark and
  per-task price figures come from third-party blogs that disagree with each other; I quote no
  benchmark number for that reason. OpenAI's current offering was not verified.
- Whether niri supports an absolute virtual pointer (`wlr-virtual-pointer`) or another verified
  pointer path was not checked on the machine. Phase 4B assumes it must be found out first.
- AT-SPI coverage for Chromium and Electron apps under Wayland, and the state of the
  Wayland-native accessibility work (Newton), rest on a 2024 GNOME post and a 2026 mailing-list
  thread. AT-SPI is treated as optional for that reason.
- "Attaching to a real profile is detected less" is a vendor claim; no controlled comparison was
  found. Chrome's block on remote debugging of the default profile (Chrome 136+) is from
  Chrome's own developer blog.
- Screenshot token counts and per-task costs in 8A are estimates, not measurements.
- UI-TARS availability and price on a hosted endpoint were not re-checked today; report 04 saw
  `bytedance/ui-tars-1.5-7b` used through OpenRouter.
