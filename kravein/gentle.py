"""Gentle scroll — keep a page or a window drifting downwards on its own.

"Open my liked songs and scroll it slowly" is two different problems wearing
one name, because the two targets have nothing in common:

* **A page in Kravein's browser.** A script injected into the page moves the
  scroll container itself, a fraction of a pixel per frame. Smooth, exact, and
  it cannot land anywhere else — the page scrolls whether or not it is focused,
  and the user's pointer is never touched.

* **A native window** — the Spotify desktop app, a PDF viewer, a file manager.
  There is no protocol for scrolling someone else's window, so this comes down
  to synthesising wheel events, and a wheel event goes to **whatever is under
  the pointer**, not to the focused window. That means moving the user's mouse
  and holding it there. It is the weaker mechanism by a long way, and it is
  treated that way: the target is resolved without guessing, focus is verified,
  and the moment the user takes the mouse or focus back the scroll stops
  instead of firing wheel events into whatever they moved to.

Everything here runs on a background thread, which brings its own rule. A
thread that synthesises input must never outlive its reason to exist, so a
session ends on *all* of: an explicit stop, a deadline, the target
disappearing, the user reclaiming focus or the pointer, reaching the end of the
content, and the process exiting. Only one session runs at a time.
"""

from __future__ import annotations

import atexit
import threading
import time
from dataclasses import dataclass, field

from . import config
from .platform import browser, desktop, inputs

# 1.0 is the intended "gentle" pace; everything else is a multiple of it.
# Browser: pixels per second. Window: wheel ticks per second — coarser by
# nature, since one tick is however many lines the app decides it is.
_BROWSER_PX_PER_SEC = 45.0
_WINDOW_TICKS_PER_SEC = 1.2

_WORDS = {
    "crawl": 0.25, "very slow": 0.35, "slower": 0.5, "slow": 0.6,
    "gentle": 1.0, "normal": 1.0, "default": 1.0,
    "medium": 1.6, "faster": 2.2, "fast": 2.6, "very fast": 4.0,
}

MIN_RATE, MAX_RATE = 0.1, 10.0

# Nothing scrolls forever unattended. Overridable per call and in settings.
DEFAULT_MAX_SECONDS = 600.0

# How often the browser session re-asserts itself in the page. A navigation
# tears down the injected loop with the document, so it is put back rather than
# the session silently going dead while still reporting as running.
_REASSERT = 1.2

# The user is allowed to nudge the mouse without killing the session; a real
# grab moves it much further than this.
_POINTER_SLACK = 40


def parse_rate(value: object, fallback: float = 1.0) -> tuple[float, str]:
    """Turn '2', 'slow' or 'very fast' into a rate. Returns (rate, problem)."""
    if value is None or value == "":
        return fallback, ""
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().lower()
        if text in _WORDS:
            return _WORDS[text], ""
        try:
            number = float(text.rstrip("x"))
        except ValueError:
            return fallback, (
                f"I don't know the speed '{value}'. Give a number "
                f"({MIN_RATE}–{MAX_RATE}, 1 is gentle) or one of: "
                + ", ".join(sorted(_WORDS))
            )
    if not MIN_RATE <= number <= MAX_RATE:
        return fallback, f"Speed must be between {MIN_RATE} and {MAX_RATE}."
    return number, ""


def describe_rate(rate: float) -> str:
    return f"{rate:g}× (about {rate * _BROWSER_PX_PER_SEC:.0f} px/s in the browser)"


@dataclass
class _Session:
    kind: str                       # "browser" | "window"
    label: str
    direction: str
    rate: float
    max_seconds: float
    started: float = field(default_factory=time.monotonic)
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    window_id: str = ""
    # Two different positions, and conflating them broke the drift guard on
    # its first real run: `pointer` is where the user's cursor was before we
    # touched it, to be put back; `parked` is where we placed it. Drift is
    # measured against `parked` — comparing against `pointer` reports "you
    # moved the mouse" the instant we move it ourselves.
    pointer: tuple[int, int] | None = None
    parked: tuple[int, int] | None = None
    ended: str = ""                 # why it stopped, once it has

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started


_lock = threading.RLock()
_session: _Session | None = None

# Why the last session ended. A scroll that stops on its own — end of page,
# focus lost, deadline — has to leave a trace, or "why did it stop?" has no
# answer at all.
_last_ending = ""


# ---------------------------------------------------------------------------
# The page-side scroller
# ---------------------------------------------------------------------------

# Injected with {rate, direction}. Idempotent: called again it only updates the
# speed of the loop already running, so re-asserting costs nothing.
#
# Picking the element matters more than the movement does. Spotify, YouTube and
# most app-shell sites scroll an inner div while the document itself never
# moves, so `window.scrollBy` on those does exactly nothing — the failure looks
# like the feature is broken rather than aimed at the wrong node. The largest
# genuinely-scrollable element wins, and it is re-picked whenever the one we
# had is detached or has stopped being scrollable (which is what a client-side
# navigation looks like from in here).
_SCRIPT = r"""
(arg) => {
  const perSec = arg.rate * %(px)s * (arg.direction === 'up' ? -1 : 1);

  const usable = (el) =>
    el && el.isConnected && el.scrollHeight > el.clientHeight + 40;

  const pick = () => {
    const doc = document.scrollingElement || document.documentElement;
    if (usable(doc)) return doc;
    let best = null, bestArea = 0;
    for (const el of document.querySelectorAll('*')) {
      if (!usable(el)) continue;
      const style = getComputedStyle(el);
      if (!/auto|scroll/.test(style.overflowY)) continue;
      const box = el.getBoundingClientRect();
      const area = box.width * box.height;
      if (area > bestArea) { best = el; bestArea = area; }
    }
    return best;
  };

  let s = window.__kraveinGentle;
  if (!s || s.stopped) {
    s = window.__kraveinGentle = {
      perSec: perSec, stopped: false, done: false, last: 0, pos: null, el: null,
    };
    const step = (now) => {
      if (window.__kraveinGentle !== s || s.stopped) return;
      const dt = s.last ? Math.min((now - s.last) / 1000, 0.25) : 0;
      s.last = now;

      if (!usable(s.el)) { s.el = pick(); s.pos = s.el ? s.el.scrollTop : null; }
      if (s.el) {
        // Kept as a float so a sub-pixel-per-frame pace still moves; writing
        // scrollTop truncates, so accumulating in the DOM would stall at slow
        // speeds instead of drifting.
        if (s.pos === null || Math.abs(s.el.scrollTop - Math.round(s.pos)) > 4) {
          s.pos = s.el.scrollTop;   // the user scrolled it themselves
        }
        s.pos += s.perSec * dt;
        const limit = s.el.scrollHeight - s.el.clientHeight;
        if (s.pos >= limit) { s.pos = limit; s.done = perSec > 0; }
        if (s.pos <= 0) { s.pos = 0; s.done = s.perSec < 0; }
        s.el.scrollTop = s.pos;
      }
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  s.perSec = perSec;
  // Pick here as well as in the frame loop. The caller reads this return value
  // to decide whether there is anything to scroll at all, and that happens
  // before the first animation frame has run — so leaving the choice to the
  // loop reported "nothing can scroll" on every page, including ones that
  // then scrolled perfectly well.
  if (!usable(s.el)) { s.el = pick(); s.pos = s.el ? s.el.scrollTop : null; }
  return {
    done: !!s.done,
    found: !!s.el,
    at: s.el ? Math.round(s.el.scrollTop) : 0,
    of: s.el ? Math.round(s.el.scrollHeight - s.el.clientHeight) : 0,
  };
}
""" % {"px": _BROWSER_PX_PER_SEC}

_CANCEL = "() => { if (window.__kraveinGentle) window.__kraveinGentle.stopped = true; }"


def _apply_to_page(rate: float, direction: str) -> dict:
    def run(context):
        page = browser.active_page(context)
        return page.evaluate(_SCRIPT, {"rate": rate, "direction": direction})

    return browser.call(run) or {}


def _cancel_page() -> None:
    def run(context):
        browser.active_page(context).evaluate(_CANCEL)

    try:
        browser.call(run)
    except Exception:
        pass    # the tab may already be gone; the loop dies with it either way


def _browser_loop(session: _Session) -> None:
    while not session.stop_event.is_set():
        try:
            state = _apply_to_page(session.rate, session.direction)
        except Exception as exc:
            _finish(session, f"the browser stopped responding ({exc})")
            return
        if not state.get("found"):
            _finish(session, "nothing on the page could be scrolled")
            return
        if state.get("done"):
            edge = "bottom" if session.direction != "up" else "top"
            _finish(session, f"it reached the {edge} of the page")
            return
        if session.elapsed >= session.max_seconds:
            _finish(session, f"the {session.max_seconds:.0f}s limit was reached")
            return
        if session.stop_event.wait(_REASSERT):
            return


# ---------------------------------------------------------------------------
# The native-window scroller
# ---------------------------------------------------------------------------

def _window_loop(session: _Session) -> None:
    backend = desktop.backend()
    while not session.stop_event.is_set():
        interval = max(0.08, min(1.0 / (session.rate * _WINDOW_TICKS_PER_SEC), 6.0))

        # Every guard below is checked *before* the tick, never after. A wheel
        # event is delivered the instant it is sent; noticing afterwards that
        # it went somewhere else is too late to matter.
        current = backend.focused()
        if current is None or current.id != session.window_id:
            where = f" — focus moved to {current.app}" if current else ""
            _finish(session, f"the window lost focus{where}")
            return

        if session.parked is not None:
            now = inputs.pointer_position()
            if now is not None:
                drift = max(abs(now[0] - session.parked[0]), abs(now[1] - session.parked[1]))
                if drift > _POINTER_SLACK:
                    # The user has the mouse. Their pointer, their call — and
                    # they are not getting it back if we keep re-centring it.
                    session.pointer = None      # nothing to restore
                    _finish(session, "you moved the mouse")
                    return

        if session.elapsed >= session.max_seconds:
            _finish(session, f"the {session.max_seconds:.0f}s limit was reached")
            return

        try:
            inputs.scroll(session.direction, 1)
        except Exception as exc:
            _finish(session, f"scrolling failed ({exc})")
            return

        if session.stop_event.wait(interval):
            return


def _restore_pointer(session: _Session) -> None:
    session.parked = None
    if session.pointer is None:
        return
    try:
        inputs.place_pointer(session.pointer[0], session.pointer[1])
    except Exception:
        pass
    session.pointer = None


# ---------------------------------------------------------------------------
# Session control
# ---------------------------------------------------------------------------

def _finish(session: _Session, reason: str) -> None:
    """End a session from inside its own thread."""
    global _session
    with _lock:
        session.ended = reason
        session.stop_event.set()
        if session.kind == "browser":
            _cancel_page()
        else:
            _restore_pointer(session)
        if _session is session:
            _session = None


def stop(reason: str = "stopped") -> str:
    """Stop whatever is scrolling. Safe to call when nothing is."""
    global _session
    with _lock:
        session = _session
        _session = None
    if session is None:
        return ""
    session.stop_event.set()
    if session.thread is not None and session.thread is not threading.current_thread():
        session.thread.join(timeout=3.0)
    if session.kind == "browser":
        _cancel_page()
    else:
        _restore_pointer(session)
    session.ended = session.ended or reason
    return f"Stopped scrolling {session.label} after {session.elapsed:.0f}s."


def current() -> _Session | None:
    """The running session, or None. Reaps one that ended on its own."""
    with _lock:
        return _session


def last_ending() -> str:
    return _last_ending


def _start(session: _Session) -> None:
    global _session, _last_ending
    _last_ending = ""
    loop = _browser_loop if session.kind == "browser" else _window_loop
    session.thread = threading.Thread(
        target=_wrap(loop, session), name="kravein-gentle-scroll", daemon=True
    )
    with _lock:
        _session = session
    session.thread.start()


def _wrap(loop, session: _Session):
    def run() -> None:
        global _last_ending
        try:
            loop(session)
        finally:
            if session.ended:
                _last_ending = f"Gentle scroll stopped: {session.ended}."

    return run


def set_rate(rate: float) -> str:
    session = current()
    if session is None:
        return ""
    session.rate = rate
    if session.kind == "browser":
        try:
            _apply_to_page(rate, session.direction)   # takes effect immediately
        except Exception:
            pass                                       # the loop will re-assert
    return f"Scrolling {session.label} at {rate:g}× now."


def default_rate() -> float:
    rate, _ = parse_rate(config.get("gentle_scroll.speed", 1.0), 1.0)
    return rate


def default_max_seconds() -> float:
    try:
        return float(config.get("gentle_scroll.max_seconds", DEFAULT_MAX_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_SECONDS


def start_browser(rate: float, direction: str, max_seconds: float, label: str) -> str:
    stop()
    session = _Session(
        kind="browser", label=label, direction=direction,
        rate=rate, max_seconds=max_seconds,
    )
    # Prove it can actually scroll something before reporting success — an
    # app-shell page with no scrollable container should say so now, not go
    # quiet and leave the user watching a page that never moves.
    try:
        state = _apply_to_page(rate, direction)
    except Exception as exc:
        return f"Could not reach the page: {exc}"
    if not state.get("found"):
        _cancel_page()
        return (
            "Nothing on that page can scroll — it all fits on screen, or the "
            "content has not loaded yet."
        )
    _start(session)
    return (
        f"Scrolling {label} {direction} at {rate:g}×. "
        f"{state.get('at', 0)} of {state.get('of', 0)} px so far. "
        "Say stop, or ask for a different speed."
    )


def start_window(
    window, rate: float, direction: str, max_seconds: float, label: str,
    restore_to: tuple[int, int] | None = None,
) -> str:
    """Scroll a native window. ``restore_to`` is where the pointer started.

    The caller supplies it because by the time we get here the pointer has
    usually already moved: Hyprland warps the cursor onto a window when it is
    focused, and focus has to happen before anything is scrolled. Reading the
    position here recorded the warped location, so "put the pointer back" put
    it back exactly where it already was.
    """
    stop()
    session = _Session(
        kind="window", label=label, direction=direction, rate=rate,
        max_seconds=max_seconds, window_id=window.id,
    )
    session.pointer = restore_to or inputs.pointer_position()

    geometry = window.geometry
    if not geometry or geometry[2] <= 0 or geometry[3] <= 0:
        return (
            f"{label} does not report its size, so there is nowhere to aim the "
            "pointer. Wheel events go where the pointer is, so this cannot be "
            "done blind."
        )
    x, y, width, height = geometry
    centre = (int(x + width / 2), int(y + height / 2))
    try:
        inputs.place_pointer(*centre)
        session.parked = inputs.pointer_position() or centre
    except inputs.InputError as exc:
        return (
            f"Could not aim the pointer at {label}: {exc}. Wheel events follow "
            "the pointer, so nothing was scrolled rather than scrolling "
            "whatever it was actually over."
        )

    _start(session)
    restore = " Your pointer goes back where it was when it stops." if session.pointer else ""
    return (
        f"Scrolling {label} {direction} at {rate:g}×. The pointer is parked over "
        f"the window because wheel events follow it, so the scroll stops if you "
        f"move the mouse or click another window.{restore}"
    )


def status() -> str:
    session = current()
    if session is None:
        return _last_ending or "Nothing is being scrolled right now."
    remaining = max(0.0, session.max_seconds - session.elapsed)
    return (
        f"Scrolling {session.label} {session.direction} at {session.rate:g}× "
        f"({session.kind}). Running {session.elapsed:.0f}s, stops on its own in "
        f"{remaining:.0f}s."
    )


atexit.register(lambda: stop("Kravein exited"))
