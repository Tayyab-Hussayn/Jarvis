# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
| `brahma/` | **The live Linux port. Work here.** |
| `web/` | **The frontend (Next.js). Work here.** Built output lands in `brahma/web-dist/`. |
| `main.py`, `ui.py`, `actions/`, `agent/`, `smart_home/`, `brahma_connect/`, `dashboard/`, `extra/` | Original Windows build. Kept for reference; **do not assume it runs.** |

`actions/attention_monitor.py` imports `ctypes.wintypes` and `pyautogui` at module top level, so `main.py` cannot even import on Linux. The Windows tree was deliberately left untouched rather than half-migrated. `README-LINUX.md` documents what was ported and what was not.

## Commands

Everything runs from the venv; there is no global install.

```bash
./install-linux.sh                          # venv + native tool check + optional systemd unit
.venv/bin/python -m pytest tests/test_linux_port.py -q     # the port's own suite (130 tests)
.venv/bin/python -m pytest tests/test_linux_port.py::test_name -q   # single test
.venv/bin/python -m compileall -q brahma/   # fast syntax check

.venv/bin/python -m brahma                  # interactive REPL
.venv/bin/python -m brahma doctor           # environment + provider health
.venv/bin/python -m brahma run <skill> k=v  # run one skill, no model involved
.venv/bin/python -m brahma ask "..."        # one request
.venv/bin/python -m brahma audit -n 30      # what the assistant actually did
.venv/bin/python -m brahma voice test       # prove the whole voice chain
.venv/bin/python tools/eval_models.py MODEL # score a model on this workload

.venv/bin/python -m brahma serve            # web UI on 127.0.0.1:8787
.venv/bin/python -m pytest tests/test_web_transport.py -q   # transport suite (48 tests)
cd web && npm run dev                       # frontend dev server on :3000 (talks to :8787)
cd web && npm run build                     # static export -> brahma/web-dist/
```

`tests/test_brahma_connect*.py` are from the Windows tree. `tests/test_screen_processor.py` and `test_gesture_utils.py` need `cv2`/`mediapipe`, which have no Python 3.14 wheels — those failures are expected.

## Architecture

Request → `agent.Agent.ask()` → `llm.LLM.complete()` → model returns tool calls →
`registry.dispatch()` → skill function → `safety.enforce()` → result fed back → repeat.

**`registry.py` is the spine.** A skill is a plain function with `@skill(...)`. The decorator reads the signature, type hints and docstring `Args:` block to build the LLM tool schema — there is no hand-written tool list anywhere. Adding a capability means dropping a function into `brahma/skills/`; no core file changes. This replaced a 1,000-line `TOOL_DECLARATIONS` array plus an `if name == ...` chain in the old `main.py`.

Parameters named `_source`, `_confirm`, `_context` are injected by the dispatcher and hidden from the model.

**`safety.py` is not advisory.** Skills that change state must call `safety.enforce(verdict, ...)` themselves — the registry will not do it for them, because only the skill knows what its arguments mean. Four levels; `DESTRUCTIVE` and above require a confirmer callback. Some things are refused regardless of confirmation (protected paths, PID 1, the compositor, `rm -rf /`).

The confirmer is **thread-local** (`safety.confirmer_scope`). The REPL supplies a terminal prompt; the daemon supplies none, so background requests cannot approve their own destructive actions.

**`platform/` wraps every OS concern.** Skills never shell out directly. Each module picks a backend at runtime (`detect.py`), so the same skill works on Hyprland, Sway or X11. `platform/shell.py` is the only place subprocesses are spawned, which is what makes the audit trail complete.

**`server.py` + `events.py` are the web transport.** `brahma serve` only. The agent loop is
blocking, so it runs on a worker thread; `events.Session` carries frames out to the websocket and
confirmations back in. `safety.Confirmer` is a *blocking* call, so the worker parks on a
`threading.Event` while the browser is asked — and every failure path (timeout, dropped socket,
unknown id) resolves to **deny**. `webauth.py` gates access on a token plus Origin and Host
checks; loopback binding alone is not a security boundary.

**`audit.py`** appends JSONL to `~/.local/state/brahma-echo/audit.jsonl` with key-like fields redacted. When debugging "why did it do that", read the audit log first — it records tool calls, arguments, results, confirmations and provider failovers.

Config is XDG (`~/.config/brahma-echo/settings.json`), not repo-local. `config.py` still reads the old `config/*.json` as a fallback.

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

**A bulk window operation must exclude Brahma's own session.** `close_workspace` filters out any
window whose pid is this process or one of its ancestors — normally the terminal running
`brahma serve` — and says it skipped them. This is structural, not a confirmation: it holds even
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

**Benchmark with the real tool schema.** Latency rankings invert once ~10k tokens of tool definitions are attached; a "say OK" benchmark is actively misleading. What matters is calls-to-completion, not single-call speed. `tools/eval_models.py` exists for this and retries 5xx so server hiccups are not scored as model failures.

## Testing conventions

Tests stub **all three** providers (`_llm.cline`, `.gemini`, `.openrouter`) — the stub's `.name` must match a real provider name because ordering is name-based. Tests touching breaker state must `monkeypatch.setattr(config, "STATE_DIR", tmp_path)`, or a real outage on the dev machine leaks in.

## Style

Follow the surrounding code: comments explain *why*, especially where a workaround encodes a platform quirk. Every non-obvious platform decision in `brahma/platform/` carries a comment naming the failure it prevents. Keep that.
