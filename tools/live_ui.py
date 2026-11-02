"""Headless-browser helper for live UI passes (run on the machine that serves).

    .venv/bin/python tools/live_ui.py shot  OUT.png [WIDTH] [HEIGHT]
    .venv/bin/python tools/live_ui.py probe          # list inputs/buttons
    .venv/bin/python tools/live_ui.py ask "text" OUT_PREFIX [--approve|--deny]

The bearer token is read from the runtime file and never printed. Uses the
system Chrome (no browser download on the laptop).
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from kravein import webauth

URL = "http://127.0.0.1:8787/"


def _page(p, w=1440, h=900):
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome-stable", headless=True,
                          args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": w, "height": h})
    pg.goto(f"{URL}?token={webauth.read_token()}")
    pg.wait_for_timeout(2500)
    return b, pg


def main(argv: list[str]) -> int:
    cmd = argv[0]
    with sync_playwright() as p:
        if cmd == "shot":
            w = int(argv[2]) if len(argv) > 2 else 1440
            h = int(argv[3]) if len(argv) > 3 else 900
            b, pg = _page(p, w, h)
            pg.screenshot(path=argv[1])
            b.close()
        elif cmd == "probe":
            b, pg = _page(p)
            for sel in ("input", "textarea", "button", "[role=button]"):
                for el in pg.query_selector_all(sel):
                    print(sel, el.get_attribute("aria-label"), (el.inner_text() or "")[:40].replace("\n", " "),
                          el.get_attribute("placeholder"))
            b.close()
        elif cmd == "ask":
            _ask(p, argv[1], argv[2], argv[3] if len(argv) > 3 else "", int(argv[4]) if len(argv) > 4 else 120)
        return 0


def _ask(p, text: str, prefix: str, decision: str, budget: int) -> None:
    """Send ``text``; shoot every few seconds; answer confirmation cards.

    ``decision`` is "approve" or "deny" (anything else leaves the card open and
    stops). Prints the visible card/status text so a run is readable without
    opening the screenshots.
    """
    b, pg = _page(p)
    pg.get_by_placeholder(re.compile(r"Ask Kravein")).fill(text)
    pg.get_by_role("button", name="Send").click()
    end, n, seen_card, last = time.time() + budget, 0, False, ""
    while time.time() < end:
        pg.wait_for_timeout(2500)
        n += 1
        pg.screenshot(path=f"{prefix}-{n:02d}.png")
        body = pg.inner_text("body")
        if body != last:
            last = body
        deny = pg.get_by_role("button", name="Deny")
        if deny.count() and not seen_card:
            seen_card = True
            print("CARD:", pg.locator("body").inner_text()[-900:].replace("\n", " | "))
            pg.screenshot(path=f"{prefix}-card.png")
            if decision == "deny":
                deny.first.click()
            elif decision == "approve":
                btn = pg.get_by_role("button", name="Approve…")
                if btn.count():
                    btn.first.click()
                    pg.wait_for_timeout(500)
                pg.get_by_role("button", name="Approve", exact=True).first.click()
            else:
                break
            seen_card = False if decision else True
        if n > 3 and "Standing by" in body and not deny.count():
            break
    pg.wait_for_timeout(1500)
    pg.screenshot(path=f"{prefix}-final.png")
    print("FINAL:", last[-1800:].replace("\n", " | "))
    b.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
