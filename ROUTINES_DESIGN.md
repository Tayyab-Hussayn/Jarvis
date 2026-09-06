# ROUTINES_DESIGN.md

Design record for the Health & Habits routine manager. Written before
implementation on 2026-09-06. **Nothing here is built yet.**

Companion to `SCHEDULING_AUDIT.md`, which covers the jobs subsystem this
extends.

**Status: phases 1 and 2 built** (engine, store, `delivery.py` extraction,
skills, tests). Phases 3–6 — the Habits panel, WhatsApp, the coach — are still
design only. Amendments made during implementation are recorded in §8 at the
bottom; where §1–§4 and §8 disagree, §8 is what was built.

---

## 0. What was checked first, and what it changes

Four findings from reading the existing code and the `neonize` wheel. Each one
moved a decision, so they come before the design rather than after it.

**`neonize.client.connect()` blocks its thread for the whole session.** It is
not "open a connection and return" — it starts the Go runtime, hands control to
`Neonize()`, and only comes back when `stop()` cancels the Go context. Read from
`neonize/client.py:3277-3400` in the 0.4.3 wheel. So a short-lived process
cannot send a WhatsApp message: it would pay a full connect and handshake per
message, and repeated connect/disconnect churn is what makes a linked device
look unstable to WhatsApp. **WhatsApp needs a resident process.** This is the
single largest constraint on phase 3.

**The wheel installs on this machine.** `neonize-0.4.3.post0-py310-none-manylinux2014_x86_64.whl`,
8.0 MB, verified with `pip download` against the 3.14.6 venv. The `py310` tag is
a Python tag, not a CPython ABI tag — the Go code is a bundled `.so` loaded
through ctypes, so it is not affected by the 3.14 wheel gap that keeps `cv2` and
`mediapipe` out.

**The tick's import budget is already spent.** `jobs.py` imports `config` and
nothing else, deliberately, because the timer runs 1,440 times a day. An 8 MB
shared library and a Go runtime cannot appear anywhere near that path. Routine
processing has to be stdlib-only for the same reason.

**`deliver()` is currently private to `jobs.py`.** Routines need the same
escalation (notification → speech → web push) and must not import `jobs` to get
it, or "process routines separately from standalone jobs" stops being true after
the first week. It gets factored out — see §5.

---

## 1. Data layer — `routines.json`

`~/.local/state/kravein/routines.json`, atomic tmp+rename, `routines.lock`
alongside it. Identical idiom to `jobs.py`; the lock is a separate file for the
same reason (a rename swaps the inode, so a `flock` on the store guards a path
that no longer exists).

History is **not** in this file. `~/.local/state/kravein/routine-days.jsonl`
gets one append-only line per closed day, mirroring `job-runs.jsonl`. The store
stays small and rewritable; history stays cheap to append and never rewritten.

### Shape

```jsonc
{
  "seq": 3,
  "routines": [
    {
      "id": "r1",
      "name": "Morning",
      "enabled": true,
      "timezone": "Asia/Karachi",

      // The day boundary. A step at 01:00 belongs to the previous day, not to
      // a new one that started an hour ago. Rolling at exactly 00:00 splits a
      // night routine in half. Local wall-clock.
      "day_start": "04:00",

      // Which weekdays this routine runs. 0 = Monday, matching datetime.
      "days": [0, 1, 2, 3, 4, 5, 6],

      "steps": [
        {
          "id": "s1",
          "label": "Wake up",
          "at": "07:00",           // absolute local wall-clock
          "after": null,           // XOR with `at` — see below
          "requires": [],          // step ids that must be `done` first
          "window_minutes": 60,    // after this, the step is missed
          "points": 10,
          "nag": { "every_minutes": 10, "max": 4, "escalate": true },
          "messages": [            // pre-generated phrasings, rotated by the
            "Time to get up.",     // tick. The tick never calls the LLM.
            "Morning. Out of bed."
          ]
        },
        {
          "id": "s2",
          "label": "Breakfast",
          "at": null,
          "after": { "step": "s1", "minutes": 30 },
          "requires": ["s1"],
          "window_minutes": 90,
          "points": 10,
          "nag": { "every_minutes": 15, "max": 3, "escalate": true },
          "messages": ["Breakfast."]
        }
      ]
    }
  ],

  // Exactly one open day instance per routine. Closed days move to the JSONL.
  "today": {
    "r1": {
      "date": "2026-09-06",          // local date key, resolved against day_start
      "opened_at": "2026-09-06T02:00:00+00:00",
      "score": 10,
      "available": 10,               // points of steps already resolved or due
      "total": 20,                   // points of every step in the day
      "steps": {
        "s1": {
          "state": "done",
          "due_at": "2026-09-06T02:00:00+00:00",
          "expires_at": "2026-09-06T03:00:00+00:00",
          "done_at": "2026-09-06T02:04:11+00:00",
          "source": "web",           // web | whatsapp | cli | auto
          "nags_sent": 1,
          "next_nag_at": ""
        },
        "s2": {
          "state": "due",
          "due_at": "2026-09-06T02:34:11+00:00",   // materialised from s1's done_at
          "expires_at": "2026-09-06T04:04:11+00:00",
          "done_at": "",
          "source": "",
          "nags_sent": 0,
          "next_nag_at": "2026-09-06T02:49:11+00:00"
        }
      }
    }
  }
}
```

### Sequencing

`requires` is the whole mechanic. A step whose prerequisites are not all `done`
sits in `locked` and **cannot be delivered, nagged, or marked done**. That is
the "Task B cannot trigger unless Task A is confirmed" requirement, expressed as
data rather than as code.

`at` and `after` are mutually exclusive and both are needed:

- `at` is a fixed wall-clock time. Known before the day starts.
- `after` is an offset from a predecessor's *actual* completion. Not knowable in
  advance — breakfast thirty minutes after you actually got up, not thirty
  minutes after you were supposed to.

An `after` step's `due_at` is **materialised at the moment it unlocks**, written
into the day instance, and never recomputed. Deriving it live on every tick
would make the timeline shift under the user while they look at it, and would
make history unreproducible. A step with `after` and unfinished prerequisites
has an empty `due_at`, and the UI says "after Breakfast" instead of a time.

Cycles are refused at write time (`add_routine_step` walks `requires`); a cycle
would leave every step permanently `locked` with no error anywhere.

### Step states

```
locked ──► due ──► nagging ──► missed
             │         │
             └────► done ◄─┘        (also: skipped, from an explicit "skip")
```

`locked` is a real stored state, not the absence of one. The distinction between
"not your turn yet" and "you missed it" is exactly what the panel has to render,
and reconstructing it from prerequisites at display time gets it wrong the
moment a prerequisite is `missed` rather than merely pending.

### Scoring

Three numbers, and the third one is the reason there are three:

- `total` — every point available today. Fixed when the day opens.
- `available` — points of steps that have already come due. Grows through the day.
- `score` — points earned.

The panel shows `score / available`. Showing `score / total` reads as failure all
morning, because you have not yet had the chance to do the evening steps. That
is the difference between a tracker someone keeps opening and one they stop
opening in week two.

### History line

```json
{"ts":"...","routine":"r1","date":"2026-09-06","score":30,"total":40,
 "done":3,"missed":1,"skipped":0,"streak":5,
 "steps":{"s1":"done","s2":"done","s3":"missed","s4":"done"}}
```

One line per closed day per routine. Streaks and the heat strip are computed
from this by reading the tail, never stored in the live store where they would
drift.

---

## 2. The logic engine

New module `kravein/routines.py`. **Stdlib plus `config` only.** Same rule as
`jobs.py`, same reason, and it should get the same test that fails if anything
heavy shows up in `sys.modules` after an empty pass.

### How it hooks into the tick

One systemd timer, not two. `kravein/jobs.py:main()` gains one line:

```python
if command == "tick":
    ran = tick()                      # standalone jobs, unchanged
    from . import routines            # lazy: not imported when used as a library
    routines.tick()
    return 0
```

`routines.tick()` is a self-contained pass over `routines.json` under its own
lock. It shares the timer, the delivery layer and the store idioms with jobs,
and shares no state. A routine step never becomes a row in `jobs.json` — that
would be two sources of truth for the same obligation, and the one thing this
design must not become is a second scheduler.

Measure the added cost before and after. Budget: the empty pass must stay under
about 5 ms on top of the current ~47 ms.

### One pass

```
routines.tick():
  with locked(blocking=False):        # a slow pass must not queue up behind itself
    now = now_utc()
    for each enabled routine:
        roll_day(routine, now)        # §midnight
        unlock(routine, now)          # locked → due where prerequisites are met
        expire(routine, now)          # due/nagging → missed past expires_at
        collect_nags(routine, now)    # what needs saying, recorded before sending
    save() if anything changed
  deliver everything collected        # outside the lock — I/O must not hold it
```

Nags are **decided and recorded under the lock, delivered outside it**.
`nags_sent` and `next_nag_at` are written before the notification goes out, so a
crash mid-delivery costs one missed nag rather than an infinite loop of them.
Same ordering as `_claim` marking a job `running` before the Agent exists, and
for the same reason.

### The midnight boundary

Rollover is **lazy, idempotent, and driven by comparison — never by a scheduled
event at 00:00.**

`roll_day` computes today's key: the local date in the routine's zone, shifted
back by `day_start`. If it matches the open instance, nothing happens. If it
does not:

1. Every step still in `locked`, `due` or `nagging` becomes `missed`.
2. The day is scored, appended to `routine-days.jsonl`, streak updated.
3. A fresh instance is built from the template for the current key.

Consequences that matter:

- **A machine asleep across midnight is fine.** The first tick after waking does
  the rollover. There is no 00:00 tick to miss.
- **Several missed days collapse to one.** A laptop shut for a week closes the
  last open instance, writes one `{"event":"skipped_days","from":…,"to":…}` line,
  and opens today. It does not fabricate six days of all-missed history, and it
  does not fire six days of stale reminders. Same no-catch-up rule as the jobs
  grace window.
- **Rollover is idempotent**, so two ticks racing on it (which the lock already
  prevents) still cannot double-close a day.

DST is handled by resolving each `at` as local wall-clock in the routine's zone
via `zoneinfo`, then converting to UTC. A step at 02:30 on a spring-forward day
does not exist; it resolves to the first valid instant after the gap. On a
fall-back day the earlier of the two occurrences wins. Everything stored in the
instance is UTC ISO, exactly as `jobs.py` does it.

`days` is evaluated against the **local** date after the `day_start` shift, so a
"weekdays only" routine with a 04:00 boundary does not fire at 01:00 on Saturday
because UTC still thinks it is Friday.

### Nagging, and what "blocking" is allowed to mean

A `due` step with a `nag` policy becomes `nagging` at its first reminder. Each
nag increments `nags_sent` and sets `next_nag_at = now + every_minutes`. It
stops at `max`, or when `expires_at` passes and the step becomes `missed`.

With `escalate: true` the channel climbs with each nag: desktop notification →
notification plus speech → WhatsApp. Cheapest first, loudest last, and a routine
never opens with the most intrusive channel.

**On "blocking", I want to be explicit, because there are two readings and one
of them is a bad idea.**

*Soft blocking* — the routine itself stalls. Later steps stay `locked`, the score
cannot advance, the panel shows the chain stopped and where. This is the default
and it is already free: it is what `requires` means. It is also, in practice,
the mechanic that works, because the cost of not confirming is visible and
cumulative.

*Hard blocking* — Kravein interferes with the machine until you confirm: locks
the screen, closes the browser, blocks a site. **I recommend against building
this, and it should not be in phase 1.** Three reasons, in order of weight:

1. It is a self-inflicted denial of service on a machine you work on, triggered
   by a state machine, on a timer, with nobody watching.
2. It runs unattended with `confirmer=None`, so `safety.enforce()` refuses
   anything at `DESTRUCTIVE` or above regardless. Closing windows and killing
   processes is exactly that tier. Building it means either it does not work, or
   somebody widens the safety policy to make it work — and that policy is the
   thing standing between a scheduled prompt and `rm -f`.
3. The failure mode is unrecoverable by design: the escape hatch is confirming
   the step, and if the reason you did not confirm is that you are away from the
   machine, the machine is locked until you come back to it.

If it is wanted anyway, the shape that is defensible is a per-step
`insist: true` that unlocks exactly one extra channel — a critical-urgency,
never-expiring notification plus repeated speech. Loud, impossible to miss,
reversible, and it cannot lock you out of anything. Nothing that closes,
kills, or blocks.

### Where the LLM sits, and where it does not

The constraint is that the model parses natural language in and writes reminder
text out, and owns none of the state logic. Concretely:

**In.** `add_routine`, `add_routine_step`, `edit_routine_step` are skills taking
structured arguments. The model turns "wake me at seven, breakfast half an hour
after I'm actually up, and don't let me skip the meds" into those arguments.
There is no prose parser in `routines.py` — same rule that killed the hand-rolled
date grammar in `parse_when`.

**Out, and this needs a decision made deliberately.** "The LLM generates reminder
text" cannot mean the tick calls a model, or a nagging step at ten-minute
intervals becomes an agent turn every ten minutes, forever, on a timer, with a
metered provider. So: phrasings are generated **once, at creation or edit time**,
stored in `messages[]` on the step, and the tick rotates through them. Varied
text, zero model calls on the hot path. A `refresh_routine_phrasings` skill can
regenerate the pool on demand.

The one place a model legitimately runs is the **daily wrap-up**, and it needs no
new machinery: a routine with `coach: true` gets a normal `jobs.add(kind="agent")`
entry at `day_start`, whose prompt asks for a short review of yesterday's line
from the JSONL. That is one agent turn a day, on the existing scheduler, which is
what "extend and merge with what exists" looks like.

---

## 3. WhatsApp delivery

Strictly `neonize`. No Node, no Baileys, no webhooks, no n8n — the whole point
is that this stays a self-contained Python system with no external service in
the loop.

### Why a resident service is not optional

Because `connect()` blocks for the session's lifetime (§0). The tick is a 50 ms
process; it cannot host a WhatsApp session, and it must not try to open one per
message.

So: **`kravein-whatsapp.service`**, a systemd user unit, `Restart=always`,
`WantedBy=default.target`. It owns the neonize client and nothing else.

```
kravein/whatsapp_service.py   # the resident process: session, socket, events
kravein/platform/whatsapp.py  # thin client used by everything else: send(), state()
systemd/kravein-whatsapp.service
```

`platform/whatsapp.py` imports nothing heavy — it talks to the socket. `neonize`
is imported **only** inside `whatsapp_service.py`. That placement is what keeps
the 8 MB `.so` off the tick path and out of the CLI's startup, and it belongs in
`platform/` because that is where every OS-and-outside-world concern already
lives; skills never touch the transport directly.

### IPC

A unix domain socket at `$XDG_RUNTIME_DIR/kravein/whatsapp.sock`, mode 0600,
line-delimited JSON.

Not HTTP-on-loopback. The web push in `jobs._push_to_web` uses HTTP because a
browser is on the other end and it has to carry a bearer token past the Host and
Origin checks. Here it is process to process on one machine, so filesystem
permissions *are* the authentication: no token to leak, no port to bind, nothing
reachable from the network, and it disappears on reboot with the runtime dir.

```jsonc
→ {"op":"send","to":"owner","text":"Time to get up.","ref":"r1/s1"}
← {"ok":true,"id":"3EB0…"}

→ {"op":"state"}
← {"state":"connected","jid":"92300…@s.whatsapp.net","since":"…"}

→ {"op":"link"}            // begin pairing, causes qr frames to be published
```

`send()` has a 5 s timeout and a hard rule: **WhatsApp is additive, never
exclusive.** The desktop notification always fires. A socket that is absent,
refused, or slow degrades to exactly today's behaviour and records
`whatsapp_unavailable` in the run history. A habit tracker whose only channel is
an unofficial WhatsApp client is a habit tracker that silently stops one day.

### Pairing and the QR

`NewClient(name, ...)` with a session database at
`~/.local/share/kravein/whatsapp/session.db`, mode 0600, directory 0700. That
file is a full WhatsApp device login. Three things follow, and they are not
optional:

- It goes in `safety.py`'s protected paths, so no skill can read, move or delete
  it.
- It is excluded from anything that syncs or backs up the data dir.
- `kravein doctor` reports its presence and mode, because a 0644 session file is
  a credential leak with no symptom.

The QR arrives through the client's `qr` callback as raw bytes. **The QR string
is a login credential for its validity window.** It is never logged, never
written to the audit trail, and never rendered anywhere but the two surfaces
below.

*Console* — `kravein whatsapp link` renders it as ASCII in the terminal using
`qrcode`, which is already a dependency (it is in `requirements-linux.txt` for
the old Connect gateway). Blocks until paired or cancelled.

*Web* — the service writes the current QR and its expiry to
`$XDG_RUNTIME_DIR/kravein/whatsapp-qr.json` (0600, runtime dir only, never the
state dir). `GET /api/whatsapp` serves the connection state and, while pairing,
the current code. The panel renders it as an inline SVG.

WhatsApp rotates the code roughly every 20–30 s. The UI must re-render on each
rotation and, when the pairing window closes, say "code expired — press Link
again" rather than leaving a dead square on screen that scans to nothing. That
specific stale-square failure is the one users spend ten minutes on.

### Connection state machine

```
unlinked ──link──► pairing ──PairStatusEv──► connected
                      │                          │
                   expired                  DisconnectedEv
                      ▼                          ▼
                  unlinked                  reconnecting ──► connected
                                                 │
                                            LoggedOutEv
                                                 ▼
                                             unlinked  (session.db discarded)
```

Reconnection is exponential backoff with a ceiling of about five minutes.
`LoggedOutEv` means the phone unlinked the device — that is terminal, so the
service stops retrying, clears its session, and surfaces "needs re-linking"
rather than hammering the servers. The state is published on the socket and
polled by the panel, so a habits list that cannot reach you never looks
identical to one that can. Same reasoning as `JobsView`'s timer banner.

### Inbound — this is how confirmations arrive, and it is the risk surface

A message from WhatsApp is **untrusted input arriving over the network at a
process that can control this machine.** Four rules, all load-bearing:

1. **Sender allowlist.** Only `whatsapp.owner_jid` from settings is processed.
   Everything else is dropped, silently, without a reply — replying confirms the
   number is live.
2. **Fixed vocabulary, matched in plain Python.** `done`, `done s2`, `2`, `skip`,
   `status`, `pause`. Nothing else does anything. This matching lives in
   `routines.py` and is a dozen lines of string comparison.
3. **Inbound text never becomes an agent prompt.** Not "the model figures out
   what they meant" — that is a remote prompt-injection channel into a system
   assistant that runs shell commands. If richer replies are wanted later, that
   is a separate decision with its own threat model, not a convenience added
   during this build.
4. **Confirmations are audited** with their source, so `source: "whatsapp"` on a
   completed step is visible in the panel and in `audit.jsonl`.

Rate: nags are capped per step, and Kravein only ever messages the owner. Worth
stating plainly — `neonize` is an unofficial client built on whatsmeow, and
WhatsApp can ban a number for automated use with no appeal process. Messaging
only yourself from your own linked device is the low-risk end of that, but it is
not zero, and the number at stake is a personal one.

*(Noting for the record: you previously said you would never use Kravein for
WhatsApp automation because that account is personal. This request overrides
that, which is your call — flagging it once so it is a decision rather than an
oversight.)*

---

## 4. Frontend — the Habits panel

### Placement

`web/components/HabitsView.tsx`, nav id `habits`, in the **Machine** group
directly below **Schedule**. It belongs next to its sibling: both answer "what
happens at what time". The Agent group is Kravein's own internals (activity,
brain, settings) and this is not that.

Add `"habits"` to `VIEWS` in `tools/check_layout.py`. That script is the only
automated guard on the frontend; a panel not listed in it is a panel nothing is
watching.

### Scrolling

The panel returns `<Screen>` and adds **no** `overflow-y-auto` of its own. The
one scroll container is `Screen`'s content wrapper
(`min-h-0 flex-1 overflow-y-auto`), inherited. Every panel that added its own
scroll to an unconstrained flex child is exactly how five screens ended up
clipping over 1,000 px with nothing able to reach it.

Long inner regions (the history strip) scroll horizontally inside their own
`overflow-x-auto`; the page body never scrolls sideways.

### Layout, top to bottom

**1 — Status banner.** Two conditions, one row, and it comes before everything
because a routine list that cannot fire looks identical to one that can:

- `kravein-jobs.timer` inactive → nothing will ever nag. Loud, with the
  `systemctl --user enable --now` line.
- WhatsApp `unlinked` / `reconnecting` → an escalation channel is dead. Quieter,
  with a **Link** button that opens the QR.

**2 — Stats row.** Four `Stat` cards, the numbers you want without reading
anything: **Score today** (`score / available`, with `total` as the small
caption), **Done / Pending**, **Streak** in days, **Next up** as a live countdown
to the current step.

**3 — Today's timeline.** The primary object on the screen. A vertical rail, one
row per step in schedule order:

```
 ●  07:00   Wake up                         10 pts   done   02:04 · web
 ●  07:34   Breakfast                       10 pts   [ Done ]  [ Skip ]
 ○  —       Meds        after Breakfast     10 pts   locked
 ◍  21:00   Walk                            10 pts   nagging · 2 of 4
 ✕  13:00   Stretch                          0/10    missed
```

Status colour and glyph come from a `STEP_STYLE` map, the same pattern
`JobsView` uses for `STATE_STYLE`. Locked steps are dimmed and state their
reason inline ("after Breakfast") instead of showing a time they do not have
yet. The **Done** button appears only on steps that are actually actionable —
offering it on a locked step and then refusing the click teaches people the UI
lies.

**4 — Routines (CRUD).** One collapsible card per routine: name, days, timezone,
day boundary, enable toggle. Inside, a reorderable step list with inline edit.
The step form takes label, time (native `<input type="time">` for `at`, or a
predecessor + minutes for `after`), prerequisites as chips, window, points, and
the nag policy. Cross-field validation is real: `at` XOR `after`, `requires`
must not contain a cycle, `window_minutes > 0`.

Form state is seeded from props and remounted with `key={editing?.id ?? "new"}`
rather than synced by an effect — the same fix `JobForm` already carries, and
skipping it is how a form silently keeps the previous row's values.

**5 — History.** A compact heat strip of the last 30 days (one cell per day,
shaded by score, tooltip with the breakdown), then the last handful of days
expanded as rows. Below the timeline, deliberately: the question this screen
answers is what happens next, not what happened.

### Data and real-time

- `GET /api/habits` → routines, today's instance, recent history, WhatsApp
  state. Records, not prose — the same rule the windows, process and jobs panels
  follow, because skill output exists to be read by a model and is free to
  change wording.
- Polled at ~15 s via `useResource`, plus a new **`routine_event`** websocket
  frame (`{t:"routine_event", routine, step, state, score}`) pushed the same way
  `job_event` is: the tick POSTs to `/api/routine-event` on the running server,
  which fans out to connected sockets. This is what makes a WhatsApp-sent "done"
  update an open browser tab within a second.
- **Every mutation goes through the existing `run` frame → skill.** No second
  write path. A routine edit from the panel is the same call the model would
  make, gets the same `safety.enforce()` treatment, and lands in the same audit
  trail.

---

## 5. Files, and the one refactor

**New**

| Path | What |
|---|---|
| `kravein/routines.py` | Store + state machine. Stdlib + `config` only. |
| `kravein/skills/routines.py` | `add_routine`, `add_routine_step`, `edit_routine_step`, `complete_step`, `skip_step`, `routine_status`, `routine_history`, `pause_routine` |
| `kravein/delivery.py` | Notification / speech / web push, lifted out of `jobs.py` |
| `kravein/whatsapp_service.py` | The resident neonize process |
| `kravein/platform/whatsapp.py` | Socket client: `send()`, `state()`, `link()` |
| `systemd/kravein-whatsapp.service` | User unit |
| `web/components/HabitsView.tsx` | The panel |

**Changed**

| Path | Change |
|---|---|
| `kravein/jobs.py` | `main()` also runs `routines.tick()`; `deliver` moves to `delivery.py` and is re-exported so nothing else breaks |
| `kravein/safety.py` | `session.db` added to protected paths |
| `kravein/server.py` | `/api/habits`, `/api/routine-event`, `/api/whatsapp` |
| `web/components/Shell.tsx` | `habits` nav item under Schedule |
| `web/lib/protocol.ts` | `routine_event` frame |
| `tools/check_layout.py` | `habits` in `VIEWS` |
| `install-linux.sh` | Optional WhatsApp unit, alongside the jobs timer |
| `requirements-linux.txt` | `neonize>=0.4.3`, in its own optional section |
| `kravein/cli.py` | `kravein whatsapp link|status|unlink` |

The `delivery.py` extraction is the only structural change to existing code, and
it is what stops routines from importing jobs to reach `deliver()`. Without it,
"processed separately" quietly becomes false.

---

## 6. Build order

Each phase ends somewhere usable, so this can stop early without leaving a
half-built subsystem.

1. **Store and state machine.** `routines.py` + `delivery.py` extraction + unit
   tests. Notification delivery only. Testable with a fake clock and no
   WhatsApp, no UI, no model.
2. **Skills.** Model can create, edit and complete routines from the REPL.
3. **Panel.** Full CRUD and timeline over the REST endpoints.
4. **WhatsApp — outbound.** Service, socket, pairing, escalation channel.
5. **WhatsApp — inbound.** Allowlisted "done" confirmations.
6. **Coach.** Optional daily wrap-up as an existing `agent` job.

### Testing

The state machine is pure and clock-injected, which is the point of designing it
that way — every case below is a unit test with no I/O:

- sequencing: a locked step cannot be completed, nagged, or expire
- `after` materialises once and does not drift on later ticks
- rollover across midnight; across a `day_start` that is not midnight; across
  seven skipped days; run twice on the same tick (idempotent)
- DST spring-forward gap and fall-back duplicate
- nag cap, escalation ladder, expiry to `missed`
- scoring: `available` grows through the day, `score/available` never exceeds 1
- cycle in `requires` refused at write time
- an empty pass imports nothing heavy (mirrors the existing jobs test)

WhatsApp is tested against a fake socket server, not a live session — the
protocol is ours and it is three message types. The live path gets one manual
end-to-end check at phase 4, and `send()` failure is exercised by simply not
starting the service, which must degrade to a notification with no error.

---

## 7. Decisions I need from you

1. **Hard blocking.** I recommend soft blocking only, with an optional `insist`
   flag that unlocks a critical notification plus repeated speech and nothing
   else. Confirm, or tell me you want more than that and what specifically.
2. **`day_start` default.** 04:00 local. Anything past that is a late night, not
   an early morning.
3. **Steps per routine, routines total.** Affects whether the timeline needs
   virtualising. I am assuming under ~20 steps and a handful of routines.
4. **Missed-day behaviour.** I am proposing a streak breaks on any day with a
   missed step. The gentler alternative is a threshold (streak survives at ≥70%
   of the day's points). Your call — it changes how the tracker feels more than
   any other single number here.
5. **WhatsApp number.** Confirm you are linking the personal account, given what
   §3 says about ban risk and given your earlier position on this.

---

## 8. What implementation actually changed

Recorded because none of it was in the design above, and three of the five were
found by running the thing rather than by the test suite.

**A third shared module, `timeutil.py`.** The design had `routines.py` importing
clock helpers from somewhere unstated, and the only candidate was `jobs.py` —
which would have undone the whole point of extracting `delivery.py`. Both now
import `timeutil` (stdlib only, ~0.2ms) and neither imports the other.

**`ends_at` on the day instance.** Deciding "has the day rolled over?" from
local wall-clock loads `zoneinfo` on every tick — 5.2ms, 1,440 times a day, to
answer a question whose answer changes at 04:00. The day now caches its own end
as a UTC instant. The steady-state pass does no timezone arithmetic at all:
**5.0ms → 0.9ms** added to the tick, against a populated 30-step store.

**Expiry and unlocking loop to a fixed point** (`_settle`). One pass of each is
not enough in either order: expiring a step fails the chain behind it, and
unlocking a step can immediately expire it when the tick is late. A single pass
let a broken chain dribble out one state change per minute.

**A day opened late stands its steps down rather than failing them.** Not in the
design at all. A routine created at 4pm reported its 07:00 step as missed before
it had ever existed. `skipped` keeps it out of the score, because being asked
and not doing it is a miss and never being asked is not. The test is whether the
step's *window* has closed, not whether its due time has passed — the first
version used the due time and stood down a step scheduled for the current
minute, two seconds late.

**Skipped points leave the streak denominator.** `score / (total - skipped)`,
not `score / total`. A skip is a decision, not a failure, and scoring it as one
teaches people not to press the button — which costs you the signal about what
they actually intended. A miss still counts against the day. History rows carry
`judged` and `skipped_points` so a ratio can be re-derived.

**`dst_note` was reading the wrong date.** A step before the day boundary lands
on the following calendar date; `step_due_at` knew that and `dst_note` did not,
so it would have warned about the wrong day and stayed silent about the right
one. Both now go through `step_calendar_date`.

### Measurements

| | |
|---|---|
| Empty routine store, added to the tick | **+0.9 ms** (budget 5 ms) |
| 30 steps loaded, nothing due | **+0.9 ms** |
| Engine alone, 30 steps, in-process | **0.37 ms** median, 0.59 ms p95 |
| `zoneinfo` on a steady-state pass | **not imported** |
| Suite | **368 passed** (338 before; 30 new) |
