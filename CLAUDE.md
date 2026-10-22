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

The owner writes Roman Urdu — reply in simple Roman Urdu with a clear recommendation. Code, docs,
issues, PRs and commits are in English.

**The team.** One **orchestrator** session (main checkout, `main`) and four module agents —
**engine**, **skills**, **web**, **qa** — each its own Claude Code session in its own worktree
(`.claude/worktrees/<name>` on `agent/<name>`). An agent edits only the paths it owns
(`.claude/team/roster.json`, enforced by hooks) and reaches `main` only through a reviewed,
squash-merged PR on the private GitHub remote. The Windows-original tree and
`brahma-connect-android/` are **frozen** — nobody edits them until the roster unfreezes them.
Your SessionStart briefing says which role you are. Protocol: `.claude/rules/team-protocol.md`;
overview: `kb/how-the-team-works.md`.

**If you are the orchestrator:** your persona (`.claude/team/personas/orchestrator.md`) governs.
Talk to the owner; for anything non-trivial brainstorm and plan with them (plan mode), then turn the
plan into task packets (`/assign-task`). Review (`/review-pr`) and merge (`/merge-pr`); do not write
module feature code. Run `/orient` at session start.

**If you are a module agent:** the task packet *is* the approved plan — do not enter plan mode and do
not wait for an owner "nod". Follow `/task-start` → work → `/verify` → `/ship-pr`. When the packet is
unclear or wrong, use `/conflict-report` instead of guessing.

**Everyone:**

- **Correct bad requests, don't silently comply.** If a request or a task packet conflicts with the
  established architecture — bypassing `safety.enforce()`, shelling out instead of going through
  `platform/`, editing a frozen path, anything "Hard-won knowledge" warns against — or is a worse
  way to do the thing asked, say so plainly and propose the better approach *before* implementing.
- **Verify light, don't loop.** Run the tests you touched plus your module's Verify commands once,
  then report with the real output. Don't re-run the whole suite repeatedly.
- **Durable knowledge goes in files** (`kb/`, PR text) — chat is lost at compaction.
  `kb/state/project-state.md` is the tracked state (orchestrator-edited); `user-space/STATE.md` is
  an optional, gitignored scratch log.

## Current state
@kb/state/project-state.md

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
.venv/bin/python -m pytest tests/test_linux_port.py -q     # the legacy suite (314 tests)
.venv/bin/python -m pytest tests/test_linux_port.py::test_name -q   # single test
.venv/bin/python -m pytest tests/engine tests/skills tests/web -q   # per-module suites
python3 .claude/team/tests/test_team_engine.py   # team guards (122)
.claude/team/bin/team status                 # agents, worktrees, guard blocks
.venv/bin/python -m compileall -q kravein/   # fast syntax check

.venv/bin/python -m kravein                  # interactive REPL
.venv/bin/python -m kravein doctor           # environment + provider health
.venv/bin/python -m kravein run <skill> k=v  # run one skill, no model involved
.venv/bin/python -m kravein ask "..."        # one request
.venv/bin/python -m kravein audit -n 30      # what the assistant actually did
.venv/bin/python -m kravein voice test       # prove the whole voice chain
.venv/bin/python tools/eval_models.py MODEL # score a model on this workload

.venv/bin/python -m kravein serve            # web UI on 127.0.0.1:8787
.venv/bin/python -m pytest tests/test_web_transport.py -q   # transport suite (87 tests)
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

These cost real debugging time. The full write-ups live in path-scoped rules that load when you
touch the matching files — `.claude/rules/hard-won-{desktop,safety,llm-agent,voice,scheduler,whatsapp,web}.md`.
Read the relevant one **before** changing that area, even if it has not loaded yet.

The principles behind all of them:

- **A prompt instruction is not enforcement.** Three times a model worked around a sentence in the
  system prompt. Anything that must hold goes in code, with a test.
- **Every failure path of a confirmation denies.** Timeouts, dropped sockets, cancels, unknown ids,
  unattended sessions. Nothing may approve its own destructive action.
- **Never resolve an ambiguous target by taking the first match** — refuse and list candidates, and
  make every confirmation name what it acts on.
- **Verify the effect, not the acknowledgement.** Hyprland answering `ok`, ydotool exiting 0 and a
  form submitting all looked like success while nothing happened.
- **The docstring is the tool schema.** An ambiguous skill name or a truncated `Args:` line is a
  model-facing bug.
- **Measure the real thing:** the failover path, the real tool schema, the pass that does nothing
  separately from the pass that does something.
- **Every client request gets a reply**, and optimistic UI state needs an invariant so a dropped
  frame cannot wedge a control.

## Testing conventions

Tests stub **all three** providers (`_llm.cline`, `.gemini`, `.openrouter`) — the stub's `.name` must match a real provider name because ordering is name-based. Tests touching breaker state must `monkeypatch.setattr(config, "STATE_DIR", tmp_path)`, or a real outage on the dev machine leaks in.

## Style

Follow the surrounding code: comments explain *why*, especially where a workaround encodes a platform quirk. Every non-obvious platform decision in `kravein/platform/` carries a comment naming the failure it prevents. Keep that.
