# PLAN.md — automation capabilities

Restores the automation the Windows build had, on mechanisms that suit Linux and
Wayland. Previous plans are done; see git history, `UI_AUDIT.md` and
`AGENT_AUDIT.md`.

**Status:** built and driven against the real system. 139 skills, 292 tests passing.
Browser, Spotify (MPRIS), real-terminal typing, and the graphical file manager.

Found while building, and worth reading before touching input synthesis: **ydotool had never
worked**. It accepts only numeric key codes, silently ignores names like `ENTER`, and exits 0
either way — so every `press_keys` call and every hotkey in the port had been doing nothing while
reporting success. Fixed; see `CLAUDE.md`.

---

## What the original did, and what we do instead

Read from the Windows tree rather than assumed:

| Capability | Original mechanism | Here |
|---|---|---|
| Browser | **Playwright** on the OS default browser | Playwright on a **dedicated Brahma profile** |
| Element targeting | `smart_click`: `get_by_role` → `get_by_text` → `get_by_placeholder` → `get_by_label` | Same cascade, but **refuses ambiguity** |
| WhatsApp | pyautogui macro: Win → type name → Enter → Ctrl+F → … | Playwright on WhatsApp Web |
| Spotify | pyautogui keystrokes at the window | **MPRIS over D-Bus** — no keystrokes |
| Terminal commands | pyautogui typing at whatever had focus | Spawn → locate window → **verify focus** → type |

The original was fast because it verified nothing. It typed and hoped; a slow
page meant the message went wherever focus happened to be. Every mechanism below
verifies before acting, because this port has already destroyed the wrong
terminal once by not doing so.

Environment confirmed on this machine: `playerctl` present, Spotify installed
(`spotify.desktop`), `ydotoold` running with `/dev/uinput` writable, `foot` and
`kitty` available, Chrome running **without** a debugging port.

---

## P1 · Browser control — dedicated profile

### Decision: its own browser, not yours

Playwright launches Chrome with a persistent context at
`~/.local/share/brahma-echo/chrome-profile`.

- Your Chrome keeps running untouched; no restart, no profile lock, no conflict.
- You log into WhatsApp Web (or anything else) **once** in Brahma's profile and
  it persists.
- A mis-click lands in a profile you chose, not your banking tab.

Attaching to a live Chrome over CDP needs `--remote-debugging-port`, which is
**unauthenticated** — any local process could then act as you everywhere. Kept
as an opt-in setting, never the default. Launching with your real profile
directory is ruled out entirely: Chrome holds a lock on it, so it would require
closing your browser first.

### Build

`brahma/platform/browser.py`
- Playwright driven from a dedicated thread, since the agent loop is blocking.
- `launch_persistent_context(headless=False, channel="chrome")`, lazily started
  on first use and reused after.
- `--ozone-platform=wayland` so it renders natively here.
- Clean shutdown when the server exits.

`brahma/skills/browser.py`
- `browser_open(url)` · `browser_search(query)` · `browser_read()`
- `browser_click(description)` · `browser_type(description, text)`
- `browser_scroll(direction, amount)` · `browser_press(key)`
- `browser_tabs()` · `browser_switch_tab(index)` · `browser_new_tab(url)`
- `browser_close_tab()` · `browser_status()`

**Ambiguity is refused, not guessed.** `browser_click("Send")` on a page with
four Send buttons lists them rather than clicking the first. This is the A1
lesson from `AGENT_AUDIT.md` applied to the DOM: taking `matches[0]` is what
closed the wrong terminal.

**Danger levels.** Navigating and reading are MODERATE. Clicking is MODERATE by
default but escalates to DESTRUCTIVE when the target looks like it submits,
sends, buys, deletes or posts — and the confirmation names the site and the
element, not just "click".

## P2 · Spotify and media — over D-Bus, not keystrokes

`playerctl open <URI>` is MPRIS `OpenUri`, which is exactly what "open my liked
songs" needs. No synthetic input, no window focus, no guessing.

| Ask | URI |
|---|---|
| Liked songs | `spotify:collection:tracks` |
| A playlist | `spotify:playlist:<id>` |
| Search | `spotify:search:<query>` |
| An album / artist / track | `spotify:album:<id>` etc. |

`brahma/platform/audio.py` gains `open_uri(uri, player)`; `brahma/skills/media.py`
gains:

- `spotify_play(what)` — "liked songs", a search phrase, or a raw `spotify:`
  URI. Launches Spotify first if it is not running, and waits for it to appear
  on the bus.
- `spotify_status()` — track, artist, album, position, via MPRIS metadata.
- Existing `media_control` and `now_playing` stay; they already use playerctl.

## P3 · Real terminal control

"Open a terminal and run X there" genuinely needs synthetic input — there is no
protocol for typing into someone else's terminal.

`brahma/skills/terminal.py`
- `open_terminal(command, terminal, workspace)` — spawns `foot`/`kitty`, waits
  for its window to appear, and optionally types a command into it.
- `type_into_terminal(command, query, workspace)` — types into an **existing**
  terminal identified the same way windows are elsewhere.

Rules, each earned from a previous bug:

1. **Resolve the target the same way as `close_window`** — refuse ambiguity,
   accept a `workspace=` scope, list candidates. Three terminals open is the
   normal case here.
2. **Focus and verify before typing.** If focus does not land, refuse. Typing
   blind is precisely how the original sent messages to the wrong person.
3. **Classify the command first.** Text typed into a shell runs with the user's
   privileges, so it goes through `safety.classify_command` exactly as
   `run_command` does. Typing `rm -rf` at a terminal is not safer than executing
   it because a human pressed Enter for you.
4. **Never type into Brahma's own terminal** — `safety.own_pids()`.
5. Prefer the safest route: when the caller just wants output, `run_command` is
   better. This is for when the command must run *in a visible terminal*.

## Order and verification

1. **P2 Spotify** — smallest, and D-Bus is the most reliable mechanism.
2. **P1 Browser** — largest capability gain.
3. **P3 Terminal** — weakest mechanism, most caveats, last.

Each lands with tests. Anything I cannot drive against the real thing gets said
plainly rather than claimed.
