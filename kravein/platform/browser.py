"""Browser automation through Playwright, in Kravein's own Chrome profile.

Ports ``actions/browser_control.py`` from the Windows tree. Two deliberate
differences:

**It drives its own profile, not yours.** The original launched whatever browser
was the system default. Here Playwright opens Chrome with a persistent profile
at ``~/.local/share/kravein/chrome-profile``. Your browser keeps running
untouched — no restart, no profile lock, no shared cookie jar. Log into WhatsApp
Web once in Kravein's window and it stays logged in.

Attaching to a *running* Chrome would need ``--remote-debugging-port``, which is
unauthenticated: any local process could then act as you on every site you are
signed into. That is available as an opt-in setting and is never the default.
Reusing your real profile directory is not possible at all while Chrome is open,
because Chrome holds a lock on it.

**It refuses ambiguity.** The original's ``smart_click`` tried a cascade of
locators and clicked the first hit. Taking the first match is exactly what closed
the wrong terminal elsewhere in this codebase, so when a description matches
several elements this reports them instead of guessing.

Playwright's sync API is single-threaded, and the agent loop is blocking, so
everything runs on one dedicated thread behind a queue.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable

from .. import audit, config

# Chrome's own build, not Playwright's bundled chromium — it is already
# installed, and it is the browser the user actually has.
CHANNEL = "chrome"
START_TIMEOUT = 45.0
CALL_TIMEOUT = 60.0
ACTION_TIMEOUT_MS = 15_000


def profile_dir():
    return config.DATA_DIR / "chrome-profile"


class BrowserError(RuntimeError):
    """Anything the caller should see as a plain message."""


@dataclass
class _Job:
    fn: Callable[[Any], Any]
    result: queue.Queue


class _Browser:
    """Owns the Playwright thread and the persistent context."""

    def __init__(self) -> None:
        self._jobs: queue.Queue[_Job | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error = ""
        self._context = None
        self._playwright = None
        self._lock = threading.Lock()

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._ready.clear()
            self._error = ""
            self._thread = threading.Thread(
                target=self._run, name="kravein-browser", daemon=True
            )
            self._thread.start()

        if not self._ready.wait(timeout=START_TIMEOUT):
            raise BrowserError("the browser did not start within 45s")
        if self._error:
            raise BrowserError(self._error)

    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._error = (
                "Playwright is not installed. Run:\n"
                "  .venv/bin/pip install playwright"
            )
            self._ready.set()
            return

        try:
            self._playwright = sync_playwright().start()
            profile = profile_dir()
            profile.mkdir(parents=True, exist_ok=True)

            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                channel=CHANNEL,
                headless=False,
                args=[
                    # Render natively rather than through XWayland.
                    "--ozone-platform-hint=auto",
                    "--start-maximized",
                    # This profile is Kravein's; the first-run prompts are noise.
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                viewport=None,
                accept_downloads=True,
            )
            self._context.set_default_timeout(ACTION_TIMEOUT_MS)
            if not self._context.pages:
                self._context.new_page()
            audit.record("browser.started", profile=str(profile))
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"
            self._ready.set()
            return

        self._ready.set()

        while True:
            job = self._jobs.get()
            if job is None:
                break
            try:
                job.result.put(("ok", job.fn(self._context)))
            except Exception as exc:
                job.result.put(("error", exc))

        with self._closing():
            if self._context is not None:
                self._context.close()
            if self._playwright is not None:
                self._playwright.stop()

    def _closing(self):
        import contextlib

        return contextlib.suppress(Exception)

    def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            self._jobs.put(None)
            self._thread.join(timeout=10)
        self._context = None
        self._thread = None
        self._ready.clear()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._context)

    # -- calling ---------------------------------------------------------

    def call(self, fn: Callable[[Any], Any], timeout: float = CALL_TIMEOUT) -> Any:
        """Run `fn(context)` on the browser thread and return its result."""
        self.start()
        job = _Job(fn=fn, result=queue.Queue(maxsize=1))
        self._jobs.put(job)
        try:
            kind, value = job.result.get(timeout=timeout)
        except queue.Empty:
            raise BrowserError(
                f"the browser did not respond within {timeout:.0f}s"
            ) from None
        if kind == "error":
            raise BrowserError(str(value))
        return value


_browser = _Browser()


def call(fn: Callable[[Any], Any], timeout: float = CALL_TIMEOUT) -> Any:
    return _browser.call(fn, timeout=timeout)


def running() -> bool:
    return _browser.running


def shutdown() -> None:
    """Close the browser. Safe to call when it was never started."""
    _browser.stop()


def available() -> tuple[bool, str]:
    """Can the browser run here?"""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False, (
            "Playwright is not installed. Run: .venv/bin/pip install playwright"
        )
    from . import shell

    if not shell.have("google-chrome-stable") and not shell.have("google-chrome"):
        import pathlib

        if not pathlib.Path("/opt/google/chrome/chrome").exists():
            return False, "Google Chrome is not installed."
    return True, ""


# ---------------------------------------------------------------------------
# Page helpers — run on the browser thread
# ---------------------------------------------------------------------------

def active_page(context):
    """The page the caller means: the last one that was touched."""
    pages = [p for p in context.pages if not p.is_closed()]
    if not pages:
        return context.new_page()
    return pages[-1]


def describe_pages(context) -> list[dict]:
    out = []
    for index, page in enumerate(context.pages, start=1):
        if page.is_closed():
            continue
        try:
            out.append({"index": index, "title": page.title()[:70], "url": page.url})
        except Exception:
            out.append({"index": index, "title": "(unavailable)", "url": ""})
    return out


# A site's own search box, found without being told what it looks like.
#
# Ordered most-specific first, because the loose ones match too much: a page
# can easily contain an `input[name*=search]` that is a hidden mobile variant
# or a filter for one section, while the real search box is a proper
# `input[type=search]`. Every match is checked for visibility before it is
# offered, since hidden duplicates of the search field are extremely common —
# most responsive layouts ship two and hide one.
_SEARCH_SELECTORS = (
    'input[type="search"]:not([type="hidden"])',
    '[role="searchbox"]',
    'form[role="search"] input:not([type="hidden"]):not([type="submit"])',
    'input[aria-label*="search" i]',
    'textarea[aria-label*="search" i]',
    '[contenteditable="true"][aria-label*="search" i]',
    'input[placeholder*="search" i]',
    'input[name="q"]',
    'input[name*="search" i]',
    'input[id*="search" i]',
    'form[action*="search" i] input[type="text"]',
)

# Sites that keep the search box behind a button until you ask for it. There is
# no selector these agree on, but they all label the control for screen readers,
# so the accessible name is what to match — GitHub's is a bare <button> with no
# text at all and aria-label "Search or jump to, type / to search".
_SEARCH_OPENER_ROLES = ("button", "link")
_SEARCH_OPENER_NAME = "search"

# How long to let a revealed search box appear. GitHub's opens a dialog that
# takes over a second to render its input, so a single short sleep found
# nothing and the whole site reported "no search box". Polled rather than
# slept on, so fast sites stay fast.
_REVEAL_TIMEOUT_MS = 4000
_REVEAL_POLL_MS = 200


def search_field(page, reveal: bool = True):
    """The page's own search input, or None.

    This is deliberately not `candidates()` — that finds an element the caller
    already described. Here nobody knows what the field is called, because the
    site decides: "Search", "Search Amazon", "Search or jump to…", "What do you
    want to play?". Asking the model to guess the label is what made searching
    inside a site unreliable, so the field is identified structurally instead.
    """
    for selector in _SEARCH_SELECTORS:
        try:
            locator = page.locator(selector)
            count = locator.count()
        except Exception:
            continue
        for i in range(min(count, 6)):
            item = locator.nth(i)
            try:
                if item.is_visible() and item.is_enabled():
                    return item
            except Exception:
                continue

    if not reveal:
        return None

    # Nothing on show. Open a collapsed search, then look again.
    for role in _SEARCH_OPENER_ROLES:
        try:
            opener = page.get_by_role(role, name=_SEARCH_OPENER_NAME, exact=False)
            count = opener.count()
        except Exception:
            continue
        for i in range(min(count, 4)):
            control = opener.nth(i)
            try:
                if not control.is_visible():
                    continue
                control.click()
            except Exception:
                continue
            waited = 0
            while waited < _REVEAL_TIMEOUT_MS:
                found = search_field(page, reveal=False)
                if found is not None:
                    return found
                page.wait_for_timeout(_REVEAL_POLL_MS)
                waited += _REVEAL_POLL_MS

    return None


# Locators tried in order, the same cascade the Windows build used — but every
# candidate is counted before anything is clicked.
def candidates(page, description: str) -> list[tuple[str, Any]]:
    text = (description or "").strip()
    if not text:
        return []
    found: list[tuple[str, Any]] = []
    probes = [
        ("role=button", lambda: page.get_by_role("button", name=text)),
        ("role=link", lambda: page.get_by_role("link", name=text)),
        ("placeholder", lambda: page.get_by_placeholder(text)),
        ("label", lambda: page.get_by_label(text)),
        ("text", lambda: page.get_by_text(text)),
        ("title", lambda: page.get_by_title(text)),
    ]
    for name, build in probes:
        try:
            locator = build()
            count = locator.count()
        except Exception:
            continue
        if count:
            found.append((name, locator))
    return found
