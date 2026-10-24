#!/usr/bin/env python
"""Check every view at phone width (default 390x844).

`tools/check_layout.py` (qa's) proves each panel can scroll vertically. This
adds what only matters on a phone, where the Android WebView shows the UI:

* the page itself must not scroll sideways (document wider than the viewport),
* nothing visible may stick out past the right edge,
* every tappable control is at least 40px in both directions,
* the nav drawer opens and lists the views.

    .venv/bin/python web/scripts/check_phone_layout.py [width] [height]

Needs `kravein serve` running. Exits non-zero on any failure.
"""

from __future__ import annotations

import sys
import time

from kravein import webauth
from kravein.platform import browser

VIEWS = (
    "console", "schedule", "habits", "system", "windows", "files", "apps",
    "control", "phones", "screen", "activity", "brain", "settings",
)
MIN_TARGET = 40

PROBE = """
(min) => {
  const vw = document.documentElement.clientWidth;
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  // Elements inside a deliberately horizontally-scrolling strip may extend past
  // the edge; they are reachable by scrolling that strip.
  const inScroller = (el) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      if (/auto|scroll/.test(getComputedStyle(p).overflowX) && p.scrollWidth > p.clientWidth) return true;
    }
    return false;
  };
  const stickout = [];
  const small = [];
  for (const el of document.querySelectorAll("main *, header *")) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.right > vw + 1 && !inScroller(el)) {
      stickout.push((el.tagName + "." + String(el.className).slice(0, 40)) + " right=" + Math.round(r.right));
    }
    if (el.matches("button, a[href], select, input:not([type=hidden]), [role=button]") && !inScroller(el)) {
      if (r.height < min - 0.5 || (r.width < min - 0.5 && !el.matches("input"))) {
        small.push((el.textContent || el.getAttribute("aria-label") || el.tagName).trim().slice(0, 24)
          + " " + Math.round(r.width) + "x" + Math.round(r.height));
      }
    }
  }
  return {
    pageOverflow: document.documentElement.scrollWidth - vw,
    stickout: stickout.slice(0, 5),
    small: small.slice(0, 6),
  };
}
"""


def main(width: int = 390, height: int = 844) -> int:
    token = webauth.read_token()
    if not token:
        print("No web token — start `kravein serve` first.", file=sys.stderr)
        return 2

    def run(context):
        page = context.new_page()
        page.set_viewport_size({"width": width, "height": height})
        results = []
        for view in VIEWS:
            # Fresh, cache-busted document per view (stale bundles hide fixes).
            page.goto(
                f"http://127.0.0.1:8787/?token={token}&r={time.time()}#/{view}",
                wait_until="networkidle",
            )
            page.wait_for_timeout(1200)
            results.append((view, page.evaluate(PROBE, MIN_TARGET)))
        # Drawer: opens, lists every view, and closes on selection.
        page.goto(f"http://127.0.0.1:8787/?token={token}&r={time.time()}#/console",
                  wait_until="networkidle")
        page.wait_for_timeout(800)
        page.click('button[aria-label="Open menu"]')
        drawer_items = page.locator('nav[aria-label="Navigation drawer"] button:has(span)').count()
        page.click('nav[aria-label="Navigation drawer"] button:has-text("Settings")')
        page.wait_for_timeout(300)
        closed = page.locator('button[aria-label="Close menu"]').count() == 0
        landed = page.evaluate("location.hash")
        # On-screen keyboard: the WebView viewport shrinks (resizes-content), so
        # emulate that and require the composer to stay inside it.
        page.goto(f"http://127.0.0.1:8787/?token={token}&r={time.time()}#/console",
                  wait_until="networkidle")
        page.set_viewport_size({"width": width, "height": height // 2})
        page.wait_for_timeout(500)
        composer = page.evaluate(
            "() => { const t = document.querySelector('main textarea, main input[type=text]');"
            " if (!t) return null; const r = t.getBoundingClientRect();"
            " return {top: r.top, bottom: r.bottom, vh: innerHeight}; }"
        )
        page.close()
        return results, drawer_items, closed, landed, composer

    try:
        results, drawer_items, closed, landed, composer = browser.call(run, timeout=240)
    except Exception as exc:
        print(f"Could not drive the UI: {exc}", file=sys.stderr)
        return 2

    failed = []
    for view, r in results:
        ok = r["pageOverflow"] <= 1 and not r["stickout"] and not r["small"]
        if not ok:
            failed.append(view)
        print(f"  {view:10} pageOverflow={r['pageOverflow']:4}px  "
              f"{'ok' if ok else 'FAIL'}")
        for key in ("stickout", "small"):
            for item in r[key]:
                print(f"             {key}: {item}")

    drawer_ok = drawer_items >= len(VIEWS) and closed and landed == "#/settings"
    print(f"  drawer     items={drawer_items} closes_on_select={closed} -> {landed}  "
          f"{'ok' if drawer_ok else 'FAIL'}")
    kb_ok = bool(composer) and composer["top"] >= 0 and composer["bottom"] <= composer["vh"]
    print(f"  keyboard   composer={composer}  {'ok' if kb_ok else 'FAIL'}")
    print()
    drawer_ok = drawer_ok and kb_ok
    if failed or not drawer_ok:
        print(f"Phone layout problems: {', '.join(failed) or 'drawer'}")
        return 1
    print(f"All {len(results)} views fit at {width}x{height}.")
    return 0


if __name__ == "__main__":
    args = [int(a) for a in sys.argv[1:3]]
    try:
        raise SystemExit(main(*args))
    finally:
        with __import__("contextlib").suppress(Exception):
            browser.shutdown()
