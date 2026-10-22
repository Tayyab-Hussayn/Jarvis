---
paths:
  - "kravein/skills/**"
  - "kravein/platform/**"
  - "kravein/gentle.py"
  - "kravein/registry.py"
---

# Hard-won knowledge: Desktop, input, windows and browser

Moved verbatim from `CLAUDE.md`. Each paragraph is a bug that already happened; do not rediscover it.

**Hyprland 0.56 replaced dispatchers with a Lua API.** `hyprctl dispatch closewindow address:0x…` no longer parses. Worse: in the Lua API `window.close()`, `kill()`, `float()` and `move()` act on the **focused** window and *silently ignore* an `"address:…"` argument. Passing an address looks like it targets that window and actually destroys whatever is focused. `desktop.HyprlandBackend._act_on()` focuses the target, confirms focus landed, and refuses if it did not. Never bypass it.

**Verify a window actually closed.** Hyprland answering `ok` only means the request was accepted. `close_app`/`close_window` poll afterwards and report honestly when an app is showing a save prompt.

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

**A bulk window operation must exclude Kravein's own session.** `close_workspace` filters out any
window whose pid is this process or one of its ancestors — normally the terminal running
`kravein serve` — and says it skipped them. This is structural, not a confirmation: it holds even
when the user answers yes, because "close workspace 2" is not a request to kill the thing doing
the closing. An earlier session killed the user's terminal exactly this way.

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
