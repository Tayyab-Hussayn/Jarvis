"""Headless-browser helper for live UI passes (run on the machine that serves).

    .venv/bin/python tools/live_ui.py shot  OUT.png [WIDTH] [HEIGHT]
    .venv/bin/python tools/live_ui.py probe          # list inputs/buttons
    .venv/bin/python tools/live_ui.py ask "text" OUT_PREFIX [--approve|--deny]

The bearer token is read from the runtime file and never printed. Uses the
system Chrome (no browser download on the laptop).
"""
from __future__ import annotations

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
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
