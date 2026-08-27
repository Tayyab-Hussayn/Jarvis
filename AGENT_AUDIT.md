# Agent audit

A pass over Kravein as an autonomous system, not just its frontend. Prompted by
a real incident: asked to close a terminal on workspace 4, it closed the one on
workspace 2 that was running its own server.

Method: read the code and measure against the live machine. Every finding below
was reproduced, not inferred. Severity is **P0** can destroy the wrong thing or
wedge the system, **P1** wrong or unusable behaviour, **P2** refinement.

---

## A1 · Target resolution

The class of bug behind the reported incident: turning a user's words into a
specific thing to act on, and guessing when the words are ambiguous.

### A1-1 · `close_window` / `focus_window` took `matches[0]` · **P0 · fixed**

`desktop.find_windows` returns *every* match ranked by quality. Callers took the
first. Measured live with three `foot` terminals open, `matches[0]` was the
terminal running Claude Code — so the same request would have killed this
session.

Fixed: `_resolve_one` refuses ambiguity and lists candidates; `workspace=` scope
added; target re-checked between confirming and acting.

### A1-2 · `close_app` closed every match everywhere · **P0 · fixed**

No workspace scope and no self-protection. `close_app("foot")` targeted all
three terminals — the server's, Claude Code's, and the intended one. The
confirmation said only "close 3 window(s) of 'foot'".

Fixed: `workspace=` scope, own-session windows excluded, and each target named
with workspace, title and pid.

### A1-3 · Non-destructive skills still guess · **P2 · not fixed**

`launch_game`, `apps.launch_by_name` and `web.open_url`'s browser choice all
take `matches[0]`. They are MODERATE — launching the wrong game is recoverable —
so they are noted rather than changed. If any becomes destructive, it inherits
the A1-1 bug.

---

## A2 · Blocking and timeouts

### A2-1 · Live tool calls were unbounded · **P0 · fixed**

The model holds its turn open until every call returns a `FunctionResponse`. A
call that never returned left the session listening but unable to answer, with
no recovery but tearing it down by hand.

Fixed: `TOOL_TIMEOUT`, a guaranteed result on timeout or exception, a reported
failure if results cannot be delivered, and audit records so the next stall is
diagnosable.

### A2-2 · `shell.run` timeouts · **checked, no finding**

Every caller inherits `DEFAULT_TIMEOUT`. Verified against the awkward case — a
detached child holding the inherited pipe (`setsid sleep 120 &`) returned at the
timeout, not later.

---

## A3 · Confirmation quality

### A3-1 · Confirmations that cannot be reviewed · **P0 · fixed**

`close window 'foot'` is identical for three different terminals. Approving it
was not an informed decision, which is what turned one bad resolution into a
destroyed terminal. Destructive window and app confirmations now carry
workspace, title and pid.

### A3-2 · Other destructive skills · **P2 · partially addressed**

`delete_path` names the full path; `kill_process` names the pid; `control_service`
names the unit. Those are reviewable. Not systematically swept.

---

## A4 · Self-protection

### A4-1 · `kill_process` could kill the terminal running the server · **P0 · fixed**

`check_process` blocked only `pid == os.getpid()`. The parent terminal running
`kravein serve` was fair game — killing it kills the assistant. This is the same
mistake as the window bug, one layer down.

Fixed: `safety.own_pids()` covers the whole ancestor chain and is now the single
definition used by `check_process`, `close_window`, `close_app` and
`close_workspace`. Verified live: killing the parent terminal is blocked.

### A4-2 · Nothing protects the server's own listening socket or state dir · **P2 · not fixed**

`delete_path` on `~/.local/state/kravein` is merely destructive, not
blocked. Losing the audit log is bad but recoverable, so it is noted.

---

## A5 · Error surfacing

### A5-1 · Voice ran silently while blocked · **P1 · fixed**

A pending confirmation was spoken but never printed; a running tool showed
nothing. Both now reach the transcript.

### A5-2 · `_emit` swallows observer exceptions · **P2 · accepted**

`live.py` and `voice.py` wrap observer calls in bare `except Exception: pass`.
Correct — a broken UI must not kill a voice session — but it means a client-side
rendering bug is invisible server-side. Acceptable; noted.

---

## A6 · State reconciliation

### A6-1 · Optimistic state cleared only by a remote event · **P0 · fixed earlier**

Two bugs came from this: the toggle stuck on "Stopping…", and confirmations
dropped when their turn id matched no message. Both now hold invariants rather
than trusting a frame to arrive.

---

## A7 · Capability gaps

Not bugs — things a full assistant should do that this cannot yet.

| Gap | Why it matters |
|---|---|
| **Cancel an in-flight turn** | Still unimplemented. The agent loop has no interruption point, so a long turn cannot be stopped. Longstanding, recorded in git history. |
| **No undo** | Every destructive action is one-way. `delete_path` uses the trash, but closed windows and killed processes are gone. A short "undo the last thing" window would change the risk profile more than any confirmation. |
| **No memory of the user's layout** | It cannot answer "put things back how they were" because it never records where they were. |
| **No proactive reporting** | Nothing surfaces without being asked — no "your disk is nearly full". `notify` exists; nothing drives it. |
| **Unported Windows features** | `send_message`, `smart_home_control`, `kravein_connect`, `flight_finder`, `file_processor`; gesture tracking blocked on Python 3.14 wheels. |

---

## A8 · Observability

### A8-1 · Tool starts were not audited · **P1 · fixed**

The audit log recorded results, not attempts, so a hang left no trace and the
only evidence was a screenshot. `voice.live_tool_start`/`_end`/`_timeout` now
record durations.

---

## Summary

| Severity | Found | Fixed |
|---|---|---|
| P0 | 6 | 6 |
| P1 | 2 | 2 |
| P2 | 5 | 0 (recorded) |

The P0s share one shape: **the system acted on a target it had not established
was the right one, and told the user too little to catch it.** Resolution,
confirmation and self-protection are now handled in one place each rather than
re-implemented per skill.
