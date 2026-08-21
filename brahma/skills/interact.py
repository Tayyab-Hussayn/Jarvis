"""Direct machine interaction: typing, clicking, screen capture, clipboard.

Ports ``actions/computer_control.py``. Everything here used pyautogui, which
cannot drive a Wayland session; the platform layer routes it through ydotool
instead.
"""

from __future__ import annotations

import time

from ..platform import clipboard as clip
from ..platform import inputs, screen
from ..registry import skill
from ..safety import Danger, Verdict, enforce


# ---------------------------------------------------------------------------
# Input synthesis
# ---------------------------------------------------------------------------

@skill(category="interact", danger=Danger.MODERATE)
def type_text(text: str, delay_ms: int = 12) -> str:
    """Type text into whatever currently has keyboard focus.

    Args:
        text: The literal text to type.
        delay_ms: Milliseconds between keystrokes.
    """
    return inputs.type_text(text, delay_ms=delay_ms)


@skill(category="interact", danger=Danger.MODERATE)
def press_keys(keys: str) -> str:
    """Press a key or key combination.

    Args:
        keys: A key name or combination, e.g. 'enter', 'ctrl+c', 'super+1'.
    """
    return inputs.press(keys)


@skill(category="interact", danger=Danger.MODERATE)
def click_mouse(button: str = "left", count: int = 1) -> str:
    """Click at the current pointer position.

    Args:
        button: 'left', 'right' or 'middle'.
        count: Number of clicks; 2 for a double-click.
    """
    return inputs.click(button, count)


@skill(category="interact", danger=Danger.MODERATE)
def move_mouse(x: int, y: int, relative: bool = False) -> str:
    """Move the mouse pointer.

    Args:
        x: X coordinate in pixels.
        y: Y coordinate in pixels.
        relative: Move by this offset instead of to this position.
    """
    return inputs.move_mouse(x, y, absolute=not relative)


@skill(category="interact", danger=Danger.MODERATE)
def scroll(direction: str = "down", amount: int = 3) -> str:
    """Scroll the mouse wheel.

    Args:
        direction: 'up', 'down', 'left' or 'right'.
        amount: How many wheel steps.
    """
    return inputs.scroll(direction, amount)


@skill(category="interact", danger=Danger.SAFE)
def input_status() -> str:
    """Check whether the assistant can synthesise keyboard and mouse input."""
    return inputs.status()


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------

@skill(category="interact", danger=Danger.SAFE)
def read_clipboard() -> str:
    """Read the current clipboard contents."""
    text = clip.paste()
    if not text:
        return "The clipboard is empty."
    if len(text) > 4000:
        return text[:4000] + f"\n… [{len(text) - 4000} more chars]"
    return text


@skill(category="interact", danger=Danger.MODERATE)
def write_clipboard(text: str) -> str:
    """Put text on the clipboard.

    Args:
        text: What to copy.
    """
    return clip.copy(text)


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

@skill(category="interact", danger=Danger.MODERATE)
def take_screenshot(save_path: str = "", region: bool = False, active_window: bool = False) -> str:
    """Capture the screen to a PNG file.

    Args:
        save_path: Where to save it. Defaults to the cache directory.
        region: Interactively select a region with the mouse.
        active_window: Capture only the focused window.
    """
    try:
        path = screen.capture(
            save_path or None, region=region, window=active_window
        )
    except screen.CaptureError as exc:
        return str(exc)

    size = path.stat().st_size
    return f"Screenshot saved: {path} ({size / 1024:.0f} KB)"


@skill(category="interact", danger=Danger.MODERATE)
def look_at_screen(question: str = "", region: bool = False, active_window: bool = False) -> str:
    """Capture the screen and describe what is on it.

    This is the screen-awareness feature: it takes a screenshot, sends it to
    the vision model and answers a question about what is visible.

    Args:
        question: What to ask about the screen. Defaults to a description.
        region: Select a region interactively first.
        active_window: Only look at the focused window.
    """
    from ..llm import LLMError, client

    try:
        image_b64, mime = screen.capture_base64(region=region, window=active_window)
    except screen.CaptureError as exc:
        return str(exc)

    prompt = question.strip() or (
        "Describe what is on this screen: the application in use, the visible "
        "content, and anything that looks like it needs attention. Be concise."
    )

    try:
        return client().vision(prompt, image_b64, mime)
    except LLMError as exc:
        return f"Captured the screen but no vision model could read it: {exc}"


@skill(category="interact", danger=Danger.MODERATE)
def click_on_screen(description: str, _source: str = "local", _confirm: bool = False) -> str:
    """Find a UI element by description and click it.

    Takes a screenshot, asks the vision model where the element is, then
    moves the pointer there and clicks.

    Args:
        description: What to click, e.g. 'the blue Sign In button'.
    """
    import re

    from ..llm import LLMError, client

    if not description.strip():
        return "Describe what I should click."

    width, height = screen.screen_size()
    if not width or not height:
        return "Could not determine the screen size, so I cannot click accurately."

    try:
        image_b64, mime = screen.capture_base64(max_width=width)
    except screen.CaptureError as exc:
        return str(exc)

    prompt = (
        f"This is a screenshot of a {width}x{height} pixel screen. "
        f"Find this UI element: '{description}'. "
        f"Reply with ONLY the centre coordinates as 'x,y' in pixels "
        f"relative to the full {width}x{height} screen. "
        f"If you cannot find it, reply exactly NOT_FOUND."
    )

    try:
        answer = client().vision(prompt, image_b64, mime)
    except LLMError as exc:
        return f"Vision model unavailable: {exc}"

    if "NOT_FOUND" in answer.upper():
        return f"Could not find '{description}' on screen."

    match = re.search(r"(\d+)\s*,\s*(\d+)", answer)
    if not match:
        return f"The model did not return usable coordinates: {answer[:120]}"

    x, y = int(match.group(1)), int(match.group(2))
    if not (0 <= x <= width and 0 <= y <= height):
        return f"The model returned off-screen coordinates ({x}, {y})."

    # Clicking a model-chosen point is a real-world action on someone else's
    # UI, so it is gated rather than fired blind.
    verdict = Verdict(
        danger=Danger.DESTRUCTIVE,
        reasons=[f"clicks at ({x}, {y}) — the model's guess at '{description}'"],
    )
    enforce(verdict, f"click '{description}' at ({x}, {y})",
            skill="click_on_screen", source=_source, pre_confirmed=_confirm)

    inputs.move_mouse(x, y, absolute=True)
    time.sleep(0.15)
    inputs.click("left")
    return f"Clicked '{description}' at ({x}, {y})."


@skill(category="interact", danger=Danger.SAFE)
def screen_info() -> str:
    """Report screen dimensions and capture capability."""
    width, height = screen.screen_size()
    lines = [screen.status()]
    if width and height:
        lines.append(f"Screen size: {width}x{height}")
    from ..platform import display

    lines.append(display.list_monitors())
    return "\n".join(lines)
