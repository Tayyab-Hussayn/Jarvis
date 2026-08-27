# UI Audit — Kravein frontend

Audited live in Chrome against a running `kravein serve`, at 1600×1000 and 1448×625, on the
target machine. Computed styles and DOM state were read directly rather than judged from
screenshots — two things that *looked* broken (a double-highlighted rail item) turned out to be
JPEG artifacts, and one thing that looked fine (the font) turned out to be badly broken.

Severity: **P0** breaks the experience · **P1** reads as unpolished · **P2** refinement.

---

## A. Root cause first

### A1 — The entire UI renders in monospace on this machine · P0

`--font-sans` is `ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif`. On this Arch
system **`system-ui` resolves to a monospace face** — verified by measuring glyph advance widths:

| family | `i` vs `W` equal? | width of `Hamburgefonstiv` |
|---|---|---|
| `ui-sans-serif` | no | 104.7 |
| **`system-ui`** | **yes (mono)** | **131.8** |
| `monospace` | yes | 135.0 |
| `sans-serif` | no | 113.4 |

Chrome on Linux does not resolve `ui-sans-serif` to a real face, so it falls through to
`system-ui` → monospace. Every heading, label, button and paragraph in the app is therefore
rendering as a terminal typeface. This single bug is responsible for most of the "clean but not
premium" gap — it makes a designed application look like a TTY.

Inter, Noto Sans, Cantarell, Roboto, DejaVu Sans and Liberation Sans are all installed and
resolve correctly.

---

## B. Sidebar / rail (primary focus)

### B1 — Icons are Unicode geometric glyphs, not an icon set · P0
`◈ ▤ ▦ ▢ ◰ ◑ ▣ ≡ ◉`. Four of the nine are near-identical squares (`▤ ▦ ▣ ▢`). They render at
inconsistent optical sizes and stroke weights because they come from whatever font happens to
cover that codepoint. They carry no semantic relationship to what they open. This is the single
most obvious amateur tell in the interface.

### B2 — The logo and the active item are the same visual object · P1
Both are lime rounded squares of near-identical size, stacked adjacently. The eye cannot separate
brand from selection; the "B" reads as a tenth nav item.

### B3 — No labels anywhere · P1
Nine ambiguous glyphs with hover-only tooltips. The user must memorise position. Every reference
design uses icon **plus** label.

### B4 — Active state is a heavy solid fill · P1
Solid lime is the strongest treatment available and is spent on navigation. Mature apps reserve
solid accent for primary actions and use a subtle tint plus an indicator bar for selection.

### B5 — No grouping · P1
Nine items in one undifferentiated run. They are three different kinds of thing: the agent
(Console), machine surfaces (System/Windows/Files/Apps/Control/Screen), and introspection
(Activity/Brain). No separators or spacing communicate that.

### B6 — Settings is orphaned · P2
Pushed to the very bottom by `mt-auto` with ~380px of dead space above it. Reads as forgotten
rather than deliberately separated.

### B7 — Rail is visually empty below the items · P2
68px wide, items stop at ~40% height. Large void with no purpose.

### B8 — No hover or pressed feedback that is actually visible · P2
Hover only changes glyph colour from `--text-muted` to `--text`, which at this size is nearly
imperceptible. No background change, no transition on the indicator.

---

## C. Console

### C1 — Vertical dead space dominates · P1
The empty state floats in a ~500px void. Content is neither vertically centred nor top-anchored;
it just hangs.

### C2 — Composer and dock collide · P1
The floating dock sits ~60px under the composer. Two floating bars stacked at the bottom compete
for the same attention, and the dock partially occludes content on every screen.

### C3 — Suggestion chips wrap 3 + 1 · P2
Ragged final row. Needs an even grid or fewer/shorter prompts.

### C4 — Header is three competing elements · P1
32px title, mono subtitle, and a right-floating "119 skills available" with no anchor or
container. No structural relationship between them.

### C5 — Always-visible scrollbar track in the message area · P2
Visible even when there is nothing to scroll.

---

## D. System

### D1 — First paint shows CPU 0% and an all-zero process table · P0 *(functional bug)*
`psutil.cpu_percent(interval=None)` and `Process.cpu_percent()` both return `0.0` on their first
call — they measure *since the previous call*. Verified: first pass over `process_iter` returned
**0 non-zero values**; the second pass returned real numbers.

Consequence: a freshly started server serves a process table where every row is `0.0`, so
"sort by CPU" degenerates to PID order and the table fills with `systemd`, `kthreadd` and
`kworker/*`. The stat card shows a wrong `0 %`. This is not cosmetic — the flagship screen lies
on first load.

### D2 — The four stat cards have four different anatomies · P1
CPU has a sparkline + bar; Memory has a sparkline + bar; Disk has a bar only; Uptime has neither.
Same container, four internal structures — the row reads as unfinished rather than deliberate.

### D3 — Sparklines are absent until several polls have elapsed · P2
History accumulates client-side from an empty array, so the cards visibly change shape ~9s after
load.

### D4 — Process table clips the last row mid-height · P2
Fixed `max-h-[46vh]` cuts a row in half at the container edge.

### D5 — `kill` is an unstyled low-contrast text link · P1
A destructive action rendered as `--text-faint` text with no icon, no border, no visible hover.
It is simultaneously too easy to hit by accident and too weak to read as dangerous.

### D6 — No empty/loading distinction · P2
"Reading system state…" and a genuinely unavailable psutil look nearly identical.

---

## E. Settings

### E1 — It is entirely read-only · P0 *(control failure)*
A Settings screen where nothing can be changed actively undermines "full control". The user is
told to go edit JSON by hand.

### E2 — Raw snake_case keys as labels · P1
`assistant_name`, `stt_backend`, `breaker_cooldown_seconds`, `max_tool_iterations`. Machine
identifiers leaking directly into the interface.

### E3 — Group titles are unhumanised lowercase keys · P1
"General" (capitalised, hand-written) sits beside "voice", "llm", "safety" (lowercase, derived).
Inconsistent within a single row.

### E4 — Empty values render as nothing · P1
`language` and `wake_word` show a label and blank space. Rows look broken rather than unset.

### E5 — Panels in a row have wildly mismatched heights · P2
"General" holds 2 rows and "voice" holds 14, but the grid stretches both — leaving a large empty
box beside a full one.

### E6 — Long values truncate with no recovery · P2
`protected_paths` cuts mid-path with no tooltip, wrap, or expand.

---

## F. Cross-cutting

### F1 — No persistent top chrome · P1
The app has no header bar. Every reference design has one. Without it there is no place for
global context, search, or live vitals, and each screen floats unanchored.

### F2 — No always-visible system vitals · P1
To know CPU or battery you must navigate to a screen. For an assistant whose whole promise is
control over the machine, the machine's state should never be more than a glance away.

### F3 — Focus-visible ring is the browser default shape · P2
`:focus-visible` sets an accent outline but `border-radius` is only applied via `--radius-sm`,
which mismatches pill and card radii.

### F4 — No transitions between views · P2
Screens swap instantly with no fade, so navigation feels like a page reload.

---

## G. Elements worth taking from the reference designs

Re-read `user-space/designs/`. Not yet used, and worth adding:

| # | From | Element | Why it fits |
|---|---|---|---|
| G1 | Cardiology | **Inline vitals strip** — `Diagnosis · Heart Rate 89bpm · Pressure 100/67 · Oxygen 98%` as a compact header row | Becomes a persistent CPU / MEM / DISK / BAT strip. Directly answers F2 and is the highest-value addition available. |
| G2 | Cardiology | **Identity card** (portrait + name + meta) | Becomes a machine identity card: hostname, distro, kernel, uptime, session. |
| G3 | Cardiology + Salesforce | **Pill nav with icon *and* label** | Answers B3. |
| G4 | Squadly | **Count badges on items** (`34 Deals` with a `4` pip) | Failed units on System, window count on Windows, denial count on Activity. |
| G5 | Salesforce | **"Active filters (2)"** indicator | Activity and Apps both have filter rows with no state summary. |
| G6 | Squadly | **Per-card status control** (dropdown on each card) | Window cards and process rows gain inline actions. |

Deliberately **not** taken: Squadly's schedule strip and avatar clusters (no analogue),
Cardiology's bottom timeline scrubber (the audit log already does this better as a list), and the
glass reference's photo-backed blur (illegible for dense output).

---

# Fix plan — in order

Ordered by impact-per-risk. Each step is independently verifiable.

1. **Fix the font stack.** Drop `system-ui` and `ui-sans-serif`; lead with concrete installed
   faces (`Inter`, `Noto Sans`, `Cantarell`, `Liberation Sans`, `sans-serif`). Verify by
   re-measuring glyph advances in the live page. *(A1)*
2. **Fix the CPU sampling bug.** Prime `cpu_percent` on server start and hold a warm `Process`
   cache so the first `/api/processes` response carries real numbers; drop the idle-kernel-thread
   noise from the default view. *(D1)*
3. **Build a real icon set.** Replace all Unicode glyphs with inline SVG at a consistent 20px /
   1.5px stroke, one distinct shape per destination. *(B1)*
4. **Rebuild the rail.** Labels, grouping with separators, subtle active tint + indicator bar,
   real hover, logo differentiated from selection, Settings grouped rather than orphaned.
   *(B2–B8, G3)*
5. **Add the top bar with the vitals strip.** Persistent header: view title, machine identity,
   live CPU/MEM/DISK/BAT. *(F1, F2, G1, G2)*
6. **Fix the stat-card anatomy.** One structure for all four; reserve space for the sparkline so
   cards do not reflow as history arrives. *(D2, D3)*
7. **Fix the Console layout.** Vertical rhythm, composer/dock separation, even suggestion grid,
   structured header. *(C1–C5)*
8. **Make Settings editable and humanised.** Humanise labels and group titles, render unset values
   as `—`, allow editing scalar values with a save action. *(E1–E6)*
9. **Fix the destructive `kill` affordance.** Proper icon button with hover and danger colour.
   *(D5)*
10. **Polish pass.** Focus-ring radii, view transitions, scrollbar behaviour, count badges on nav.
    *(F3, F4, G4)*

---

# Outcome

All ten plan steps implemented and verified live in Chrome on the target machine.

| # | Fix | Status | Verification |
|---|---|---|---|
| 1 | Font stack | done | Re-measured glyph advances in the live page: `stillMonospace: false` |
| 2 | CPU sampling | done | Cold-start `/api/processes` now returns real values; 3 regression tests |
| 3 | Icon set | done | `components/icons.tsx`, one 24-unit grid, 1.6 stroke |
| 4 | Rail rebuilt | done | Labels, `Agent`/`Machine`/`Inspect` groups, indicator bar, collapse toggle persisted |
| 5 | Top bar + vitals | done | CPU/MEM/DISK/BAT live on every screen; hostname from a new `/api/state` field |
| 6 | Stat anatomy | done | All four cards share one structure; meter space reserved so nothing reflows |
| 7 | Console layout | done | Centred empty state, 2×2 prompts, composer divider, phantom scrollbar gone |
| 8 | Settings editable | done | Edited `assistant_name` in the browser, confirmed on disk, restored |
| 9 | `kill` affordance | done | Icon button, danger colour on hover, revealed on row hover |
| 10 | Polish | done | View transition, focus-ring radius, acronym casing, filter indicator |

Reference additions implemented: **G1** vitals strip, **G2** machine identity card, **G3** icon+label
nav, **G5** active-filter indicator.

## Deliberately not done

- **G4 count badges** — the rail supports them (`badges` prop) but nothing currently produces a
  number that means "needs attention". A failed-units count would need a new endpoint, and a
  window count is not an alert. Wiring badges to filler data would be decoration.
- **G6 per-card status dropdowns** — window and process rows already have direct actions; a
  dropdown would add a click without adding capability.
- **Turn cancellation** — still unimplemented, and still blocked on the agent loop having no
  interruption point. Unchanged from `PLAN.md`.

## Two notes for whoever reads this next

**Never put `system-ui` or `ui-sans-serif` in the sans stack.** On this machine `system-ui`
resolves to a monospace face and Chrome-on-Linux does not resolve `ui-sans-serif` at all, so the
whole app silently rendered as a terminal. The headless screenshots taken during the previous
build did not reproduce it — only inspecting the real browser on the real machine did.

**Screenshots are not evidence on their own.** Two apparent defects in this audit (a
double-highlighted rail item, a stale settings label) turned out to be a JPEG artifact and a
cached bundle respectively. Both were checked against the DOM and the built output before being
written down; neither was real.
