"""Browser automation in Kravein's own Chrome profile.

Ports ``actions/browser_control.py``. See :mod:`kravein.platform.browser` for why
this drives a dedicated profile rather than the browser you are using.

Two rules carried over from `AGENT_AUDIT.md`:

* **Ambiguity is refused.** A description matching four buttons lists them
  rather than clicking the first. Taking `matches[0]` is what closed the wrong
  terminal; a DOM element is no different.
* **Anything that submits asks first.** Clicking "Send" or "Buy" is not
  reversible, so it carries a confirmation naming the site and the element.
"""

from __future__ import annotations

import contextlib
import urllib.parse

from ..platform import browser
from ..registry import skill
from ..safety import Danger, Verdict, describe_targets, enforce

# Words that suggest the click does something you cannot take back. Matched
# against the description the caller gave, not the DOM, because that is what
# the user actually asked for.
_IRREVERSIBLE = (
    "send", "submit", "buy", "purchase", "pay", "order", "checkout",
    "delete", "remove", "confirm", "post", "publish", "tweet", "share",
    "transfer", "withdraw", "book", "reserve", "subscribe", "unsubscribe",
)


def _requires_confirmation(description: str) -> bool:
    text = (description or "").lower()
    return any(word in text.split() or word in text for word in _IRREVERSIBLE)


def _guard() -> str:
    ok, why = browser.available()
    return "" if ok else why


def _site(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc or url
    except Exception:
        return url


@skill(category="browser", danger=Danger.SAFE)
def browser_status() -> str:
    """Whether Kravein's browser is running, and what is open in it."""
    problem = _guard()
    if problem:
        return problem
    if not browser.running():
        return (
            f"Kravein's browser is not running. Its profile lives at "
            f"{browser.profile_dir()}; it starts on first use."
        )
    pages = browser.call(browser.describe_pages)
    if not pages:
        return "Kravein's browser is running with no pages open."
    lines = [f"{len(pages)} tab(s) open in Kravein's browser:"]
    lines += [f"  [{p['index']}] {p['title']} — {p['url'][:70]}" for p in pages]
    return "\n".join(lines)


@skill(category="browser", danger=Danger.MODERATE)
def browser_open(url: str, new_tab: bool = False) -> str:
    """Open a URL in Kravein's browser.

    This is a separate browser from your own, with its own profile and its own
    logins. Starting it the first time takes a few seconds.

    Args:
        url: Address to open. A bare domain gets https:// added.
        new_tab: Open in a new tab instead of reusing the current one.
    """
    problem = _guard()
    if problem:
        return problem

    target = (url or "").strip()
    if not target:
        return "No URL given."
    if not target.startswith(("http://", "https://", "file://", "about:")):
        target = "https://" + target

    def go(context):
        page = context.new_page() if new_tab else browser.active_page(context)
        page.goto(target, wait_until="domcontentloaded")
        return f"{page.title()[:70]} — {page.url}"

    try:
        return f"Opened {browser.call(go)}"
    except browser.BrowserError as exc:
        return f"Could not open {target}: {exc}"


@skill(category="browser", danger=Danger.MODERATE, aliases=["browser_search"])
def browser_search_web(query: str, engine: str = "", _source: str = "local") -> str:
    """Search the whole web with a search engine, leaving the current page.

    This navigates away to a search engine. To search *within* a site that is
    already open — YouTube, a shop, a wiki, a docs site — use
    `browser_search_site` instead, which uses that site's own search box.

    Args:
        query: What to search for.
        engine: duckduckgo (default), google or bing.
    """
    if not (query or "").strip():
        return "No search query given."
    engines = {
        "duckduckgo": "https://duckduckgo.com/?q=",
        "ddg": "https://duckduckgo.com/?q=",
        "google": "https://www.google.com/search?q=",
        "bing": "https://www.bing.com/search?q=",
    }
    # DuckDuckGo by default, not Google. Google serves its "unusual traffic"
    # interstitial to an automated Chrome profile and the search never runs —
    # observed on this machine. An engine that captchas the assistant is not a
    # working default, however good its results would have been.
    choice = (engine or "duckduckgo").lower()
    base = engines.get(choice, engines["duckduckgo"])
    return browser_open(base + urllib.parse.quote(query))


@skill(category="browser", danger=Danger.MODERATE)
def browser_search_site(query: str) -> str:
    """Search inside the site already open, using that site's own search box.

    This is what "search YouTube for X" or "find X on this site" means: it
    types into the page's search field and submits, staying on the site. It
    does not touch the address bar and does not go to a search engine.

    The field is found structurally rather than by name, so this works on any
    site without being told what its search box is called.

    Args:
        query: What to search for on the current site.
    """
    problem = _guard()
    if problem:
        return problem
    if not browser.running():
        return (
            "Kravein's browser is not running, so there is no site to search in. "
            "Open the site first with browser_open."
        )
    if not (query or "").strip():
        return "No search query given."

    def run(context):
        page = browser.active_page(context)
        before = page.url
        field = browser.search_field(page)
        if field is None:
            return {"found": False, "url": before, "title": page.title()}

        field.click()
        # `fill` replaces rather than appends. Site search boxes very often
        # already hold the previous query, and typing into that would submit
        # the two concatenated.
        try:
            field.fill(query)
        except Exception:
            # contenteditable search boxes reject fill(); type into them.
            field.type(query)
        field.press("Enter")

        # A site search is usually a client-side route change, so waiting for a
        # load event proves nothing — it can fire instantly having gone
        # nowhere. Wait for the URL to actually move, and treat not moving as a
        # fact to report rather than an error to hide.
        moved = True
        try:
            page.wait_for_url(lambda url: url != before, timeout=8000)
        except Exception:
            moved = False
        with contextlib.suppress(Exception):
            page.wait_for_load_state("domcontentloaded", timeout=5000)

        return {
            "found": True, "moved": moved, "url": page.url,
            "before": before, "title": page.title(),
        }

    try:
        result = browser.call(run)
    except browser.BrowserError as exc:
        return f"Could not search this site: {exc}"

    site = _site(result["url"])
    if not result["found"]:
        return (
            f"No search box found on {site} ({result['title'][:50]}). "
            "Use browser_search_web to search the web instead, or "
            "browser_click to open the site's search yourself."
        )
    if not result["moved"]:
        # Submitted, but nothing observably happened. Saying "searched" here
        # would be the assistant asserting a result it has not seen.
        return (
            f"Typed '{query}' into the search box on {site} and pressed Enter, "
            f"but the page did not change. It may still be loading — read the "
            "page to check."
        )
    return f"Searched {site} for '{query}' — now on {result['url']}"


@skill(category="browser", danger=Danger.SAFE)
def browser_read(max_chars: int = 3000) -> str:
    """Read the visible text of the current page.

    Args:
        max_chars: Truncate beyond this many characters.
    """
    problem = _guard()
    if problem:
        return problem
    if not browser.running():
        return "Kravein's browser is not running."

    def read(context):
        page = browser.active_page(context)
        return page.title(), page.url, page.inner_text("body")

    try:
        title, url, text = browser.call(read)
    except browser.BrowserError as exc:
        return f"Could not read the page: {exc}"

    limit = max(200, min(int(max_chars), 20000))
    body = " ".join(text.split())
    if len(body) > limit:
        body = body[:limit] + f"… [{len(text)} chars total]"
    return f"{title}\n{url}\n\n{body}"


@skill(category="browser", danger=Danger.MODERATE)
def browser_click(
    description: str,
    index: int = 0,
    _source: str = "local",
    _confirm: bool = False,
) -> str:
    """Click something on the page, found by its visible text or label.

    If the description matches several elements this reports them rather than
    guessing — pass index to choose one.

    Args:
        description: Visible text, label, placeholder or title of the element.
        index: 1-based choice when several elements match.
    """
    problem = _guard()
    if problem:
        return problem
    if not browser.running():
        return "Kravein's browser is not running — open a page first."

    def look(context):
        page = browser.active_page(context)
        found = browser.candidates(page, description)
        out = []
        for how, locator in found:
            count = locator.count()
            for i in range(min(count, 8)):
                item = locator.nth(i)
                try:
                    label = " ".join((item.inner_text() or "").split())[:60]
                except Exception:
                    label = ""
                out.append({"how": how, "n": i, "label": label})
        return page.url, page.title(), out, found

    try:
        url, title, listing, _ = browser.call(look)
    except browser.BrowserError as exc:
        return f"Could not search the page: {exc}"

    if not listing:
        return f"Nothing on this page matches '{description}'."

    if len(listing) > 1 and not index:
        lines = [
            f"'{description}' matches {len(listing)} elements on {_site(url)} — "
            "say which one with index:"
        ]
        lines += [
            f"  [{n + 1}] via {item['how']}: {item['label'] or '(no text)'}"
            for n, item in enumerate(listing)
        ]
        return "\n".join(lines)

    choice = max(1, index) - 1
    if choice >= len(listing):
        return f"Only {len(listing)} match(es); index {index} is out of range."
    chosen = listing[choice]

    if _requires_confirmation(description):
        verdict = Verdict(
            danger=Danger.DESTRUCTIVE,
            reasons=[
                f"clicks '{chosen['label'] or description}' on {_site(url)} "
                f"({title[:40]}) — this may not be reversible"
            ],
        )
        enforce(
            verdict, f"click '{description}' on {_site(url)}",
            skill="browser_click", source=_source, pre_confirmed=_confirm,
            targets=describe_targets(f"browser:{_site(url)}:{description.lower()}"),
        )

    def do_click(context):
        page = browser.active_page(context)
        found = browser.candidates(page, description)
        flat = []
        for how, locator in found:
            for i in range(min(locator.count(), 8)):
                flat.append(locator.nth(i))
        if choice >= len(flat):
            raise browser.BrowserError("the page changed; try again")
        flat[choice].click()
        page.wait_for_load_state("domcontentloaded", timeout=8000)
        return page.url

    try:
        return f"Clicked '{chosen['label'] or description}' — now on {browser.call(do_click)}"
    except browser.BrowserError as exc:
        return f"Could not click '{description}': {exc}"


@skill(category="browser", danger=Danger.MODERATE)
def browser_type(description: str, text: str, submit: bool = False) -> str:
    """Type into a field on the page, found by its label or placeholder.

    Args:
        description: Label, placeholder or nearby text of the field.
        text: What to type.
        submit: Press Enter afterwards.
    """
    problem = _guard()
    if problem:
        return problem
    if not browser.running():
        return "Kravein's browser is not running — open a page first."

    def do_type(context):
        page = browser.active_page(context)
        for build in (
            lambda: page.get_by_placeholder(description),
            lambda: page.get_by_label(description),
            lambda: page.get_by_role("textbox", name=description),
            lambda: page.get_by_role("searchbox", name=description),
        ):
            try:
                locator = build()
                if not locator.count():
                    continue
                field = locator.first
                field.click()
                field.fill(text)
                if submit:
                    field.press("Enter")
                    page.wait_for_load_state("domcontentloaded", timeout=8000)
                return page.url
            except Exception:
                continue
        raise browser.BrowserError(f"no field matches '{description}'")

    try:
        where = browser.call(do_type)
        return f"Typed into '{description}'" + (f" and submitted — now on {where}" if submit else ".")
    except browser.BrowserError as exc:
        return f"Could not type into '{description}': {exc}"


@skill(category="browser", danger=Danger.MODERATE)
def browser_scroll(direction: str = "down", amount: int = 600) -> str:
    """Scroll the current page.

    Args:
        direction: up or down.
        amount: Pixels to scroll.
    """
    if not browser.running():
        return "Kravein's browser is not running."
    delta = -abs(int(amount)) if (direction or "").lower() == "up" else abs(int(amount))

    def scroll(context):
        page = browser.active_page(context)
        page.mouse.wheel(0, delta)
        return page.url

    try:
        browser.call(scroll)
        return f"Scrolled {direction} {abs(delta)}px."
    except browser.BrowserError as exc:
        return f"Could not scroll: {exc}"


@skill(category="browser", danger=Danger.MODERATE)
def browser_switch_tab(index: int) -> str:
    """Bring one of Kravein's browser tabs to the front.

    Args:
        index: 1-based tab number from browser_status.
    """
    if not browser.running():
        return "Kravein's browser is not running."

    def switch(context):
        pages = [p for p in context.pages if not p.is_closed()]
        if not 1 <= index <= len(pages):
            raise browser.BrowserError(f"there are {len(pages)} tab(s)")
        page = pages[index - 1]
        page.bring_to_front()
        return f"{page.title()[:60]} — {page.url}"

    try:
        return f"Switched to tab {index}: {browser.call(switch)}"
    except browser.BrowserError as exc:
        return f"Could not switch tab: {exc}"


@skill(category="browser", danger=Danger.MODERATE)
def browser_close_tab(index: int = 0) -> str:
    """Close a tab in Kravein's browser.

    Args:
        index: 1-based tab number; omit to close the current tab.
    """
    if not browser.running():
        return "Kravein's browser is not running."

    def close(context):
        pages = [p for p in context.pages if not p.is_closed()]
        if not pages:
            raise browser.BrowserError("no tabs are open")
        page = pages[index - 1] if index else pages[-1]
        title = page.title()[:60]
        page.close()
        return title, len([p for p in context.pages if not p.is_closed()])

    try:
        title, left = browser.call(close)
        return f"Closed '{title}'. {left} tab(s) left."
    except (browser.BrowserError, IndexError) as exc:
        return f"Could not close that tab: {exc}"


@skill(category="browser", danger=Danger.MODERATE)
def browser_quit() -> str:
    """Close Kravein's browser entirely. Your own browser is unaffected."""
    if not browser.running():
        return "Kravein's browser is not running."
    browser.shutdown()
    return "Closed Kravein's browser."
