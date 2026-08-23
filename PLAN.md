# PLAN.md — reliability pass and agent audit

Supersedes the frontend build-out plan (that work is done; see git history and
`UI_AUDIT.md`). This plan covers three reported defects and a full audit of the
agent as an autonomous system.

**Status:** documentation complete, development in progress.

---

## Part 1 — the three reported defects

### D1 · Live voice wedges after a tool call · P0

**Reported:** the transcript ends at `run_command(command='foot &')` and never
advances. Speaking again changes nothing.

**What I confirmed:** nothing. I could not reproduce an unbounded hang.
`shell.run` honours its timeout even for a detached child holding the inherited
pipe — `setsid sleep 120 &` with `timeout=3` returned at 3.0s with
`timed_out=True`. `run_command` defaults to 60s, so it returns.

I am not going to invent a root cause from a screenshot. What the evidence does
establish is that the design **permits** this class of failure and gives no way
to see it or escape it:

- A tool call blocks the model's turn for as long as it takes. `run_command`
  allows up to 600s; `voice_confirmer` blocks up to 120s waiting on a card.
  During that window the session looks dead and is indistinguishable from one
  that will never recover.
- If `send_tool_response` never carries a result for a call, the model waits
  for ever. Nothing detects that.
- There is no per-tool timeout in the Live path, no watchdog, and no audit
  record of a tool starting — so the *next* occurrence is equally undiagnosable.

**Approach:** bound it, surface it, and make the next occurrence diagnosable.

1. Bound tool execution in the Live path. On expiry, send the model an error
   result so the conversation continues instead of stalling.
2. Guarantee a `FunctionResponse` for **every** call in a `tool_call`, including
   ones that raise. A missing response is an indefinite wait by construction.
3. Audit `voice.live_tool_start` / `_end` with durations, so a real hang leaves
   evidence rather than a screenshot.
4. Surface a stalled tool in the panel ("still running `run_command`, 42s") so
   the user can see what is happening rather than guessing.

### D2 · Only Stop → Start recovers it · P0

The session has no recovery path short of tearing it down by hand. That is the
same defect as D1 seen from the user's side: nothing self-heals and nothing
offers to.

1. A **Restart** control in the voice panel — stop and start in one action.
2. Automatic recovery when a tool exceeds its bound: report, unblock, continue.
3. The panel must always show *why* it is quiet — waiting on a tool, waiting on
   a confirmation, or genuinely idle.

### D3 · Closed workspace 2 instead of workspace 4 · P0, worst of the three

**Reported:** asked to close the `foot` terminal on workspace 4, having walked
back to workspace 1 by hand. Approving the card closed the terminal on
workspace 2 — the one running the Brahma server.

**Root cause — confirmed, not inferred.** `close_window(query='foot')` resolves
through `desktop.find_windows`, which returns *every* match ranked by how well
the string matches, and then:

```python
target = matches[0]      # skills/windows.py
```

Measured on this machine right now, `find_windows("foot")` returns **three**:

```
[0] ws3  pid=3155  foot: ⠐ brahma-echo-frontend-redesign     <- Claude Code
[1] ws4  pid=3641  foot: ~                                    <- the one meant
[2] ws2  pid=3984  foot: .venv/bin/python -m ~/c/Brahma-Echo  <- the server
```

`matches[0]` is arbitrary with respect to intent. At the time of the incident it
was the workspace-2 terminal. Right now it is the terminal running Claude Code —
so the same request today would kill this session.

Two independent failures compound:

- **Ambiguity is resolved by guessing.** Three candidates, no workspace filter,
  no disambiguation, silently take the first.
- **The confirmation cannot be reviewed.** The card said `close window 'foot'`.
  Nothing in it distinguishes three identical-looking terminals, so approving it
  was not an informed decision. A confirmation that cannot be checked is theatre.

The focus-then-act machinery in `HyprlandBackend._act_on` was *not* at fault —
it correctly focused and verified the window it was given. It was given the
wrong one, and the workspace switch the user saw was that machinery faithfully
focusing the wrong target.

**Approach:**

1. **Refuse ambiguity.** More than one match and no way to choose returns the
   candidates and asks which — it does not pick.
2. **Scope by workspace.** `close_window(query, workspace=…)`, and the same for
   `focus_window`.
3. **Confirmations must be reviewable.** Name the workspace, the title and the
   pid, not just the app class.
4. **Re-verify at action time.** The window list can change between resolving a
   target, asking, and acting. Confirm the id still exists and still matches
   before doing anything.
5. **Restore the user's workspace.** Acting on a window necessarily focuses it;
   the user should be put back where they were rather than left somewhere they
   did not ask to be.
6. **Never resolve to Brahma's own session by accident** — extend the
   `close_workspace` protection to single-window closes.

---

## Part 2 — full agent audit

Beyond the three defects, a systematic pass for loopholes and missing
capabilities. Findings and fixes go in `AGENT_AUDIT.md`.

### Areas to audit

| # | Area | Looking for |
|---|---|---|
| A1 | **Target resolution** | Every skill that turns a string into a thing it acts on. D3 is one instance; how many others take `matches[0]`? |
| A2 | **Blocking and timeouts** | Every path that can block a session unboundedly. Tool calls, confirmations, subprocesses, model requests. |
| A3 | **Confirmation quality** | Can each confirmation actually be reviewed? Does it name the specific target? |
| A4 | **Self-protection** | Which operations can kill Brahma's own session, terminal or server? |
| A5 | **Error surfacing** | Failures that vanish instead of reaching the user. |
| A6 | **State reconciliation** | Optimistic UI state cleared only by a remote event — the class behind two bugs already. |
| A7 | **Capability gaps** | What a full assistant should do that this cannot. |
| A8 | **Observability** | When something goes wrong, is there enough in the audit log to diagnose it? |

### Method

Read the code, not the docs. For each finding record: what breaks, the concrete
failure, severity, and the fix. Verify against the live system where possible
rather than asserting.

---

## Execution order

1. **D3 first.** It destroys the wrong thing, and today it would kill the
   Claude Code session.
2. **D1 + D2** together — same underlying gap.
3. **Audit**, written to `AGENT_AUDIT.md`.
4. **Fix audit findings** by severity.

Tests alongside each fix; nothing lands on a claim I have not checked.

## Honesty notes

- D1's exact trigger is unreproduced. The fixes bound and expose the failure
  class; they are not a claim that I found the specific cause. If it recurs
  there will now be audit records to read.
- D3 is fully understood and reproducible.
