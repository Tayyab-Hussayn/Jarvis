#!/usr/bin/env python
"""Check every panel can actually be scrolled to the bottom.

There is no JS test runner in this project, so a layout regression has nothing
watching it. This is the cheapest thing that does: drive the real UI in
Kravein's own browser, walk every view, and report any whose content overflows
with nothing able to scroll it.

That bug shipped once already — five screens clipped, with over 1,000px of the
Apps list unreachable, because each panel added `overflow-y-auto` to a div with
no height constraint. A flex child will not shrink below its content without
`min-h-0`, so the element grew instead of scrolling and the overflow escaped
into `<main>`, which is `overflow-hidden`.

    .venv/bin/python tools/check_layout.py

Needs `kravein serve` running. Exits non-zero if anything clips, so it can gate
a commit.
"""

from __future__ import annotations

import sys
import time

from kravein import webauth
from kravein.platform import browser

VIEWS = (
    "console", "schedule", "system", "windows", "files", "apps",
    "control", "screen", "activity", "brain", "settings",
)

# Walk every element under <main>, find the largest overflow and whether
# anything with `overflow-y: auto|scroll` can actually absorb it.
PROBE = """
() => {
  const main = document.querySelector("main");
  if (!main) return { error: "no <main>" };
  let worst = 0, scrollable = false;
  const walk = (el) => {
    const style = getComputedStyle(el);
    if (/auto|scroll/.test(style.overflowY) && el.scrollHeight > el.clientHeight + 4) {
      scrollable = true;
    }
    worst = Math.max(worst, el.scrollHeight - el.clientHeight);
    for (const child of el.children) walk(child);
  };
  walk(main);
  return { overflow: worst, scrollable };
}
"""


def main(width: int = 1280, height: int = 700) -> int:
    token = webauth.read_token()
    if not token:
        print("No web token — start `kravein serve` first.", file=sys.stderr)
        return 2

    def run(context):
        page = context.new_page()
        page.set_viewport_size({"width": width, "height": height})
        results = []
        for view in VIEWS:
            # A fresh document per view, cache-busted. A stale bundle in the
            # persistent profile once made a working fix look like a failure.
            page.goto(
                f"http://127.0.0.1:8787/?token={token}&r={time.time()}#/{view}",
                wait_until="networkidle",
            )
            page.wait_for_timeout(1500)
            results.append((view, page.evaluate(PROBE)))
        page.close()
        return results

    try:
        results = browser.call(run, timeout=240)
    except Exception as exc:
        print(f"Could not drive the UI: {exc}", file=sys.stderr)
        return 2

    clipped = []
    for view, r in results:
        if r.get("error"):
            print(f"  {view:10} {r['error']}")
            continue
        ok = r["overflow"] < 5 or r["scrollable"]
        if not ok:
            clipped.append(view)
        print(
            f"  {view:10} overflow={r['overflow']:6}px  "
            f"scrollable={str(r['scrollable']):5}  {'ok' if ok else 'CLIPPED'}"
        )

    print()
    if clipped:
        print(f"{len(clipped)} view(s) clip content with no way to scroll: "
              f"{', '.join(clipped)}")
        return 1
    print(f"All {len(results)} views scroll.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        with __import__("contextlib").suppress(Exception):
            browser.shutdown()
