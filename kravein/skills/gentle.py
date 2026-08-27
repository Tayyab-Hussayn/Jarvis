"""Gentle scroll — hands-free reading pace for a page or a window.

The engine and the reasoning behind the two mechanisms live in
:mod:`kravein.gentle`. This is the surface the model calls.

Target resolution follows the same rule as everything else that acts on a
window: a query that matches three windows is refused with the list, not
resolved to the first one.
"""

from __future__ import annotations

from .. import gentle
from ..platform import browser, desktop, inputs
from ..registry import skill
from ..safety import Danger, own_pids
from .windows import AmbiguousTarget, _describe, _resolve_one

_BROWSER_WORDS = {"browser", "chrome", "page", "web", "tab", "this page", "the page"}


@skill(category="interact", danger=Danger.MODERATE)
def gentle_scroll(
    target: str = "",
    speed: str = "",
    direction: str = "down",
    seconds: int = 0,
) -> str:
    """Start scrolling something slowly and continuously, hands-free.

    Works on a page open in Kravein's browser, or on a native window such as
    the Spotify app or a document viewer. It keeps going until you say stop,
    it reaches the end, or the time limit runs out.

    Scrolling a native window parks the pointer over that window, because
    wheel events go wherever the pointer is. It stops by itself if you move
    the mouse or focus something else.

    Args:
        target: What to scroll. "browser" for the current page, or an app
            name or window title such as "spotify". Leave empty for the
            focused window.
        speed: How fast. A number where 1 is gentle, up to 10, or a word like
            slow, gentle, medium or fast.
        direction: down or up.
        seconds: Stop automatically after this long. 0 uses the default limit.
    """
    rate, problem = gentle.parse_rate(speed, gentle.default_rate())
    if problem:
        return problem

    direction = (direction or "down").strip().lower()
    if direction not in {"up", "down"}:
        return "Direction must be up or down."

    limit = float(seconds) if seconds and seconds > 0 else gentle.default_max_seconds()

    text = (target or "").strip().lower()
    if text in _BROWSER_WORDS or (not text and _browser_is_focused()):
        if not browser.running():
            return (
                "Kravein's browser is not running — open a page with "
                "browser_open first, or name a window to scroll instead."
            )
        return gentle.start_browser(rate, direction, limit, "the page in Kravein's browser")

    window_backend = desktop.backend()
    if not window_backend.available():
        return "No supported compositor is available, so windows cannot be scrolled."

    if text:
        try:
            window = _resolve_one(target, "")
        except AmbiguousTarget as exc:
            return exc.message
    else:
        window = window_backend.focused()
        if window is None:
            return "No window is focused, and no target was given."

    if window.pid in own_pids():
        return (
            f"Refusing: {_describe(window)} is the terminal Kravein is running "
            "in. Scrolling it would move the pointer onto the assistant itself."
        )

    # Grab this before focusing: Hyprland warps the cursor onto the window it
    # focuses, so a moment later "where the pointer was" is already gone.
    origin = inputs.pointer_position()

    if not desktop.focus_and_verify(window):
        return (
            f"Could not focus {_describe(window)}, so nothing was scrolled. "
            "Wheel events would have gone to whatever is in front instead."
        )

    return gentle.start_window(
        window, rate, direction, limit, _describe(window), restore_to=origin
    )


def _browser_is_focused() -> bool:
    """Is the focused window Kravein's own browser?

    With no target given, "scroll" most likely means the thing being looked at.
    Only the page path is chosen automatically, and only when that page is
    genuinely in front — guessing "browser" while Spotify is focused would
    scroll something the user cannot see.
    """
    if not browser.running():
        return False
    backend = desktop.backend()
    if not backend.available():
        return True
    window = backend.focused()
    return window is not None and "chrom" in (window.app or "").lower()


@skill(category="interact", danger=Danger.SAFE)
def stop_gentle_scroll() -> str:
    """Stop the gentle scroll that is running."""
    message = gentle.stop("you asked it to stop")
    return message or "Nothing is being scrolled right now."


@skill(category="interact", danger=Danger.SAFE)
def gentle_scroll_speed(speed: str) -> str:
    """Change the speed of the gentle scroll while it is running.

    Args:
        speed: A number where 1 is gentle, up to 10, or a word like slow,
            gentle, medium or fast.
    """
    rate, problem = gentle.parse_rate(speed, 0.0)
    if problem:
        return problem
    if gentle.current() is None:
        return "Nothing is being scrolled, so there is no speed to change."
    return gentle.set_rate(rate)


@skill(category="interact", danger=Danger.SAFE)
def gentle_scroll_status() -> str:
    """Whether anything is being scrolled, and how fast."""
    return (
        gentle.status()
        + f"\nDefault speed {gentle.default_rate():g}×, time limit "
        f"{gentle.default_max_seconds():.0f}s. Change them with "
        "gentle_scroll.speed and gentle_scroll.max_seconds in settings."
    )
