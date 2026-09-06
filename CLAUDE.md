# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## The assistant is called Kravein

Renamed from **Brahma / Brahma-Echo** on 2026-08-24. Any surviving `brahma` name, import, path or
string is a leftover, not a decision — replace it with `kravein` on sight rather than working
around it. The `brahma-echo` XDG directories were migrated, and `config.py` still falls back to
them; delete that fallback and the old paths once nothing is using them. The one deliberate
exception is `brahma-connect-android/`, left untouched because its Java package move cannot be
build-tested here.

## How we work

Solo, fully local project — just the user and Claude Code. No task list, no third-party review,
no GitHub workflow (no branches, no PRs) — this isn't hosted or shared, just work directly. The
user describes what they want in plain language; Claude interprets intent, drives the rest, and
owns the outcome.

**Brainstorm → plan → build.** For anything non-trivial — a new feature, a behavior change
touching more than a file or two, an architectural call — use plan mode before writing code:
brainstorm the approach, write the plan, get a quick nod, then build. Skip the ceremony for small,
obvious fixes.

**Correct bad requests, don't silently comply.** If a request conflicts with this repo's
established architecture — bypassing `safety.enforce()`, shelling out directly instead of through
`platform/`, editing the Windows-original tree, anything the "Hard-won knowledge" section below
already warns against — or is just a worse way to do the specific thing being asked for, say so
plainly and propose the better approach *before* implementing. Silent compliance with a flawed
approach is the failure mode being guarded against here.

**Verify light, don't loop.** After implementing, check the work with one or two targeted runs —
the specific test(s) touched, or a quick manual check — then report back. Don't re-run the whole
suite repeatedly or keep testing past the point of confidence; this is a two-person project, not
a CI pipeline.

**Session start**: the `orient` skill runs first, every session — re-checks git state, doesn't
trust a stale mental model of what's done.

**`user-space/STATE.md` is a living task log, not just a snapshot.** For any big or long-running
task, update it after each phase completes — what was done, what's next — so progress survives
a session boundary even without the user asking. `/update-state` does a full manual refresh; the
per-phase updates during active work happen on their own, without that command.

## Two codebases live here

| Tree | Status |
|---|---|
| `kravein/` | **The live Linux port. Work here.** |
| `web/` | **The frontend (Next.js). Work here.** Built output lands in `kravein/web-dist/`. |
| `main.py`, `ui.py`, `actions/`, `agent/`, `smart_home/`, `kravein_connect/`, `dashboard/`, `extra/` | Original Windows build. Kept for reference; **do not assume it runs.** |

`actions/attention_monitor.py` imports `ctypes.wintypes` and `pyautogui` at module top level, so `main.py` cannot even import on Linux. The Windows tree was deliberately left untouched rather than half-migrated. `README-LINUX.md` documents what was ported and what was not.

## Commands

Everything runs from the venv; there is no global install.

```bash
./install-linux.sh                          # venv + native tool check + optional systemd unit
.venv/bin/python -m pytest tests/test_linux_port.py -q     # the port's own suite (130 tests)
.venv/bin/python -m pytest tests/test_linux_port.py::test_name -q   # single test
.venv/bin/python -m compileall -q kravein/   # fast syntax check

.venv/bin/python -m kravein                  # interactive REPL
.venv/bin/python -m kravein doctor           # environment + provider health
.venv/bin/python -m kravein run <skill> k=v  # run one skill, no model involved
.venv/bin/python -m kravein ask "..."        # one request
.venv/bin/python -m kravein audit -n 30      # what the assistant actually did
.venv/bin/python -m kravein voice test       # prove the whole voice chain
.venv/bin/python tools/eval_models.py MODEL # score a model on this workload

.venv/bin/python -m kravein serve            # web UI on 127.0.0.1:8787
.venv/bin/python -m pytest tests/test_web_transport.py -q   # transport suite (48 tests)
cd web && npm run dev                       # frontend dev server on :3000 (talks to :8787)
cd web && npm run build                     # static export -> kravein/web-dist/
```

`tests/test_kravein_connect*.py` are from the Windows tree. `tests/test_screen_processor.py` and `test_gesture_utils.py` need `cv2`/`mediapipe`, which have no Python 3.14 wheels — those failures are expected.

## Architecture

Request → `agent.Agent.ask()` → `llm.LLM.complete()` → model returns tool calls →
`registry.dispatch()` → skill function → `safety.enforce()` → result fed back → repeat.

**`registry.py` is the spine.** A skill is a plain function with `@skill(...)`. The decorator reads the signature, type hints and docstring `Args:` block to build the LLM tool schema — there is no hand-written tool list anywhere. Adding a capability means dropping a function into `kravein/skills/`; no core file changes. This replaced a 1,000-line `TOOL_DECLARATIONS` array plus an `if name == ...` chain in the old `main.py`.

Parameters named `_source`, `_confirm`, `_context` are injected by the dispatcher and hidden from the model.

**`safety.py` is not advisory.** Skills that change state must call `safety.enforce(verdict, ...)` themselves — the registry will not do it for them, because only the skill knows what its arguments mean. Four levels; `DESTRUCTIVE` and above require a confirmer callback. Some things are refused regardless of confirmation (protected paths, PID 1, the compositor, `rm -rf /`).

The confirmer is **thread-local** (`safety.confirmer_scope`). The REPL supplies a terminal prompt; the daemon supplies none, so background requests cannot approve their own destructive actions.

**`platform/` wraps every OS concern.** Skills never shell out directly. Each module picks a backend at runtime (`detect.py`), so the same skill works on Hyprland, Sway or X11. `platform/shell.py` is the only place subprocesses are spawned, which is what makes the audit trail complete.

**`server.py` + `events.py` are the web transport.** `kravein serve` only. The agent loop is
blocking, so it runs on a worker thread; `events.Session` carries frames out to the websocket and
confirmations back in. `safety.Confirmer` is a *blocking* call, so the worker parks on a
`threading.Event` while the browser is asked — and every failure path (timeout, dropped socket,
unknown id) resolves to **deny**. `webauth.py` gates access on a token plus Origin and Host
checks; loopback binding alone is not a security boundary.

**`audit.py`** appends JSONL to `~/.local/state/kravein/audit.jsonl` with key-like fields redacted. When debugging "why did it do that", read the audit log first — it records tool calls, arguments, results, confirmations and provider failovers.

Config is XDG (`~/.config/kravein/settings.json`), not repo-local. `config.py` still reads the old `config/*.json` as a fallback.

## Hard-won knowledge

These cost real debugging time. Do not rediscover them.

**Hyprland 0.56 replaced dispatchers with a Lua API.** `hyprctl dispatch closewindow address:0x…` no longer parses. Worse: in the Lua API `window.close()`, `kill()`, `float()` and `move()` act on the **focused** window and *silently ignore* an `"address:…"` argument. Passing an address looks like it targets that window and actually destroys whatever is focused. `desktop.HyprlandBackend._act_on()` focuses the target, confirms focus landed, and refuses if it did not. Never bypass it.

**Verify a window actually closed.** Hyprland answering `ok` only means the request was accepted. `close_app`/`close_window` poll afterwards and report honestly when an app is showing a save prompt.

**Gemini 3.x requires `thought_signature`.** Function calls replayed in history without it fail with `400 … missing a thought_signature`. `ToolCall.signature` carries it. The symptom is not an error you see — the facade silently fails over to the next provider and every multi-turn request gets slow.

**Cline has two paths.** Direct HTTP (`api.cline.bot/api/v1`, OpenAI-compatible, native tool calling) is the fast one. The CLI costs ~5s of Node boot *per call* — `cline --version` alone takes 5s. Only models gated to "Cline product surfaces" (403) use the CLI.

**Read the `cost` field, not `upstream_inference_cost`.** They mean different things and mixing them up inverted a whole model comparison. `cost: 0` with `is_byok: true` means "billed to a key on that account", not "free". Only ids ending in `:free` work at a zero balance.

**Model ids are not the catalogue.** `@cline/llms/dist/models.js` lists every model Cline knows about across all providers — most are not callable on a given account. Test before claiming a model exists.

**TTS runs mpv with `--no-config`.** A user `mpv.conf` with `keep-open=yes` hangs playback forever otherwise.

**A model will hunt for a gap in the safety verb list.** Asked to delete a file through the web
UI and denied four times (`delete_path`, `delete_path permanent`, `rm -f`, `rm`), it tried
`unlink` — which matched no pattern in `safety.py` — and deleted the file unconfirmed. The system
prompt says "never try to work around a confirmation"; that is not enforcement. Two fixes, and
both matter: the missing verbs (`unlink`, `find -delete`, `xargs rm`, `rsync --delete`, `sed -i`,
interpreter one-liners) are now classified, and `safety.enforce()` keeps a **per-turn denial
memory** keyed on the *target*, not the verb — once you decline an action on a path, any further
destructive action on that same path in that request is refused outright without re-asking. The
memory is scoped by `confirmer_scope`, so it lasts exactly one turn. Never widen it to a blanket
per-turn lockout: "delete A, keep B" must still work.

**The denial memory only works if the target is *declared*.** It originally read the target back
out of the human-readable action string, which found file paths and pids and **nothing else** — so
declining `close window 'foot'` recorded no key at all and the model could retry forever. Reported
from a real session: one "no" to closing a window produced two more prompts, the second about an
unrelated window on a different workspace. Skills now pass `targets=describe_targets(...)` to
`safety.enforce()`; prose parsing is the fallback, not the design.

Two rules for `targets`: include a stable id (`window:0x…`, `pid:42`, `service:nginx`) so a retry
under any verb is caught, and when the skill resolved its target **implicitly** — `close_window()`
with no query acts on whatever is focused — add a marker like `close_window:implicit`. Focus moves
between calls, so without the marker the retry prompts about a different window and looks like the
assistant ignoring you.

**ydotool needs numeric key codes, and lies about it.** `ydotool key ENTER:1` and
`KEY_ENTER:1` are both silently ignored — and both exit 0, so nothing surfaces. Only Linux input
event codes work (`28:1 28:0` for Enter). The port shipped symbolic names, which meant every
`press_keys` call and every hotkey did nothing at all while reporting success. `_KEYMAP` in
`platform/inputs.py` is numeric for this reason; do not "tidy" it back to names.

**Clear the prompt before typing into a terminal.** Synthetic input appends to whatever is
already staged on the line, so a half-typed command or a previous failed attempt gets
concatenated and the shell runs the result. `echo DIRECT_OK` arrived as
`/tmp/kravein-term-test. OK DIRECT_OK`. `skills/terminal.py` sends Ctrl+C then Ctrl+U first.

**Synthetic input cannot be confirmed.** Keystrokes go to the compositor and nothing comes back,
so a skill that types must report what it *did* rather than assert the command ran. The first
version claimed success while delivering nothing — the exact failure mode worth never shipping.

**Never resolve an ambiguous target by taking the first match.** `find_windows` returns *every*
match ranked by quality, and `close_window`/`focus_window` used to take `matches[0]`. With three
`foot` terminals open that picked an arbitrary one: a user who asked to close the terminal on
workspace 4, from workspace 1, had the workspace-2 terminal closed instead — the one running the
server. Skills now refuse and list the candidates (`_resolve_one` in `skills/windows.py`), accept
a `workspace=` scope, and re-check the target still exists between confirming and acting.

**A confirmation has to be reviewable.** The card for that incident said `close window 'foot'`,
which is identical for all three terminals — approving it could not be an informed decision.
Destructive confirmations name the workspace, title and pid.

**Acting on a window focuses it; put the user back.** `_act_on` must focus a target before
closing it, so the user's view is dragged along. `close_window` records the origin workspace and
restores it afterwards.

**A tool call must never hold a voice session open indefinitely.** The model keeps its turn open
until every call it made returns a `FunctionResponse`; one that never comes leaves the session
listening but unable to answer, recoverable only by stopping it by hand. `live.py` bounds each
call at `TOOL_TIMEOUT` and always returns *something*, and records
`voice.live_tool_start`/`_end`/`_timeout` so a real stall leaves evidence.

**A bulk window operation must exclude Kravein's own session.** `close_workspace` filters out any
window whose pid is this process or one of its ancestors — normally the terminal running
`kravein serve` — and says it skipped them. This is structural, not a confirmation: it holds even
when the user answers yes, because "close workspace 2" is not a request to kill the thing doing
the closing. An earlier session killed the user's terminal exactly this way.

**Every client request must produce a reply frame.** Clicking "Text" while voice was already off
left the toggle reading "Stopping…" until the page was reloaded: the client sets that flag
optimistically and clears it when the server confirms, but with no session to end the server did
nothing and sent nothing. Optimistic UI state cleared only by a remote event needs three things,
and this bug needed all three — the server always answering, the client not asking for work that
is not needed, and an invariant on the client (you cannot be "stopping" something already stopped)
so a dropped frame cannot wedge the control.

**Gemini Live transcription auto-detects the language, and gets it wrong.** English speech came
back rendered in Malayalam and Hindi script. The audio itself reached the model intact — replies
were correct while the transcript was gibberish, which is a confusing way to fail. Pass
`AudioTranscriptionConfig(language_codes=[...])`; `language_auto` is a nested config object, not a
flag, so supplying codes is what disables detection. `voice.language` in settings overrides the
`en-US` default.

**Live input transcription lags the audio.** Deltas keep arriving *after* the model has begun
answering, so flushing the user's words on the first output chunk truncates them — "running in
for" instead of "what's running in workspace four". Flush at `turn_complete`, and stream the
partial into a line that is rewritten in place, or the finished sentence lands underneath the
reply it prompted.

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

**"Search YouTube for X" is not a web search, and the tool name decided it.** The only search
skill was `browser_search`, whose name reads exactly like "search in the browser" and whose
docstring never said it navigates away to a search engine. Asked to search inside an open YouTube
tab, the model picked it, landed on Google, and got the "unusual traffic" captcha. Two fixes:
`browser_search_site` finds the page's *own* search box, and the web one is now
`browser_search_web` with a docstring that names the other. **The docstring is the tool schema —
a name that reads ambiguously is a bug, not a style question.** Also: Google will not serve an
automated Chrome profile, so the web default is DuckDuckGo.

**Find a site's search box structurally, never by name.** Sites call it "Search", "Search Amazon",
"Search or jump to…", "What do you want to play?" — asking the model to guess the label is what
made `browser_type` unreliable for this. `platform/browser.search_field()` goes by
`input[type=search]`, `role=searchbox`, `form[role=search]`, then aria-label/placeholder, and
**checks visibility**, because responsive layouts routinely ship two search inputs and hide one.
When nothing is on show it clicks a control whose accessible name contains "search" and *polls*
for the field: GitHub's is a bare `<button aria-label="Search or jump to, type / to search">`
opening a dialog that takes over a second to render, so a fixed short sleep found nothing and the
whole site reported "no search box".

**A site search that submits has not necessarily searched.** It is usually a client-side route
change, so `wait_for_load_state` can fire instantly having gone nowhere. Wait for the URL to
actually move and report "typed it but the page did not change" when it does not — asserting
"searched" there is claiming a result nothing has seen.

**One blip made the microphone useless.** A single 30ms chunk crossing the VAD threshold — a fan
surge, a key click — set `speaking`, and 1.2s of quiet then closed the "utterance". The 30ms was
below `min_speech_seconds` so the whole listen was discarded as **"nothing said"**, and
`start_timeout` was never consulted. Every listen died in ~1.3s, so local voice gave up with
"heard nothing usable 8 times in a row" in about five seconds while `pw-record` was capturing
perfectly. A false start now resets to waiting instead of ending the listen. Related: EOF on the
capture pipe is reported as *the recorder stopped*, not as silence — conflating a dead recorder
with a quiet room sends you debugging the wrong end.

**`voice.mode` used to be read by nothing.** It was displayed in `kravein voice status` and
consumed nowhere: the CLI hardcoded local, and the browser guessed a pipeline from a readiness
snapshot and sent it as a decision. That snapshot only covered the *local* halves, so a machine
with a Gemini key but no local speech model had voice disabled in the UI while `kravein voice live`
worked fine. `voice.resolve_mode()` is now the single resolver for every surface; the browser
sends a preference and the server answers with the pipeline it actually started. An explicit
`live` or `local` that cannot run is refused rather than downgraded — silently switching to the
metered pipeline, or away from it, is not a decision to make on someone's behalf.

**The model could confirm its own destructive actions.** `registry.dispatch()` harvested a
`confirm` flag out of the model's own arguments and ORed it into `pre_confirmed`, which
short-circuits `safety.enforce()` before the no-confirmer check. Caught by a scheduled job:
refused on `delete_path`, it retried the same delete as `run_command "rm -f …"` with
`confirm=true` and the file was gone — from an unattended session with no confirmer at all. A
`confirm` **keyword argument** still works, because that comes from a trusted caller; a `confirm`
key inside `params` is now ignored and audited. This is the third time a prompt instruction has
turned out not to be enforcement.

**An unattended refusal has to be remembered too.** `enforce()` raised `ConfirmationRequired` at
the `confirmer is _deny` branch *before* recording anything in the per-turn denial memory, so the
mechanism built exactly to catch a retry under a different verb never saw the first refusal.

**A refused confirmation is not a failed turn, and a scheduled job must treat it as one.** The
agent answers normally, explaining it could not act — which reads as success to anything watching
`turn.error`. For a job the user is not sitting in front of, that is the wrong signal: the whole
point is finding out it did not happen. `jobs.run_job()` watches tool results for a refusal and
reports the run as failed.

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

**Check pid identity, not just liveness, before trusting a recorded pid.** Linux recycles pids, so
`os.kill(pid, 0)` on a number recorded an hour ago can succeed against something entirely
unrelated. `jobs._pid_alive()` reads `/proc/<pid>/cmdline` and requires "kravein" in it; otherwise
a crashed runner leaves its job pinned in `running` for ever — never reaped, never retried, and
invisible unless somebody reads the store by hand.

**The tick must not import the agent.** A systemd timer runs `python -m kravein.jobs tick` every
minute. Routing that through `cli.py` costs ~250ms because that module imports the Agent, the LLM
facade and the registry at import time; the dedicated entry point costs ~50ms and pulls the heavy
machinery in only once a job is due. Measured, and asserted by a test that fails if anything heavy
appears in `sys.modules` after an empty tick. An import added at the top of `jobs.py` is paid 1,440
times a day.

**Lock a file you do not replace.** `jobs.json` is written with the usual atomic tmp+rename, which
swaps the inode — a `flock` held on it guards a path that no longer exists, and two ticks can both
think they hold it. `jobs.lock` is never replaced, so it is a stable thing to lock. A job is also
marked `running` *before* the Agent is built, so the next minute's tick skips it; and `running`
records a pid, so a crashed run is reaped instead of wedging the job for ever.

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

**A tool that fails is not a dead voice session.** `live.py` emits tool timeouts and session
failures through the same `error` phase, so treating every one as fatal put the whole toggle into
an error state because one tool took too long. It is a turn error when a turn is open, a session
error only when there is none.

**A wheel event goes where the *pointer* is, not where focus is.** This is the whole difficulty
of scrolling a native window, and it is why `gentle_scroll` parks the pointer over its target and
stops the moment the pointer or focus moves. Focus-based reasoning is correct for typing and wrong
for scrolling.

**`ydotool mousemove --absolute` does not work.** Given any coordinates it parks the pointer in
the bottom-right corner — its uinput device has no usable absolute axis range — and exits 0, so
nothing surfaces. This is the same shape of failure as the symbolic key codes: ydotool reports
success for input it never delivered. On Hyprland use the compositor instead:
`hl.dsp.cursor.move({x=…,y=…})` — and note it *requires* the named table, since `cursor.move(x, y)`
and `cursor.move({x, y})` both fail with "'x' is required". `desktop.backend().move_cursor()`
wraps this, and `inputs.place_pointer()` reads the position back and raises if it missed. Never
trust a pointer move you have not verified.

**Focusing a window on Hyprland warps the cursor onto it.** So "remember where the pointer was,
then focus the target" records the position *after* the warp, and putting the pointer back puts it
exactly where it already is. Capture before focusing; `gentle.start_window()` takes `restore_to`
for this reason.

**A page-side scroller has to pick its element, and pick it eagerly.** Spotify, YouTube and most
app-shell sites scroll an inner div while `document.scrollingElement` never moves, so
`window.scrollBy` silently does nothing. Worse, the first version only chose the element inside its
`requestAnimationFrame` loop — but the caller reads the injected script's *return value* to decide
whether there is anything to scroll, and that happens before the first frame. Every page reported
"nothing can scroll" and then scrolled fine. Also keep the scroll offset as a float in JS: writing
`scrollTop` truncates, so a sub-pixel-per-frame pace accumulated in the DOM stalls at zero.

**The registry dropped wrapped `Args:` lines.** `_parse_param_docs` only kept the first line of
each parameter, so any description that wrapped reached the model cut off mid-sentence — "Omit for
the" instead of "Omit for the focused window." It now joins continuation lines by indentation.
Worth knowing that the docstring *is* the tool schema: sloppy `Args:` formatting is a model-facing
bug, not a style issue.

**Benchmark with the real tool schema.** Latency rankings invert once ~10k tokens of tool definitions are attached; a "say OK" benchmark is actively misleading. What matters is calls-to-completion, not single-call speed. `tools/eval_models.py` exists for this and retries 5xx so server hiccups are not scored as model failures.

## Testing conventions

Tests stub **all three** providers (`_llm.cline`, `.gemini`, `.openrouter`) — the stub's `.name` must match a real provider name because ordering is name-based. Tests touching breaker state must `monkeypatch.setattr(config, "STATE_DIR", tmp_path)`, or a real outage on the dev machine leaks in.

## Style

Follow the surrounding code: comments explain *why*, especially where a workaround encodes a platform quirk. Every non-obvious platform decision in `kravein/platform/` carries a comment naming the failure it prevents. Keep that.
