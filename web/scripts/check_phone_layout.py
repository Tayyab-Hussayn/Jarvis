#!/usr/bin/env python
"""Check the v2 shell (home, every dock pane, an agent pane) at one viewport.

Default 390x844, the Android WebView. `tools/check_layout.py` (qa's) walks
`<main>` only, and panes live outside it, so this is what proves a pane can be
scrolled. For each route it requires:

* the page itself does not scroll sideways,
* nothing visible sticks out past the right edge,
* every tappable control is at least 40px (phone widths),
* a pane's content, if it overflows, has something that can scroll it,
* the dock lists every built module, and the composer stays inside a viewport
  shrunk by the on-screen keyboard.

    PYTHONPATH=. .venv/bin/python web/scripts/check_phone_layout.py [width] [height]

Needs `kravein serve` running. Exits non-zero on any failure.
"""

from __future__ import annotations

import sys
import time

from kravein import webauth
from kravein.platform import browser

ROUTES = (
    "", "windows", "apps", "files", "screen", "system", "control", "habits",
    "schedule", "phones", "brain", "activity", "settings", "agent/system-ops",
    "agent/orchestrator",
)
MODULES_BUILT = 12  # dock buttons that open something (Proposals is a slot)
MIN_TARGET = 40

PROBE = """
([min, phone]) => {
  const vw = document.documentElement.clientWidth;
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  // Inside a deliberately sideways-scrolling strip (the dock bar, chip rows).
  const inScroller = (el) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      if (/auto|scroll/.test(getComputedStyle(p).overflowX) && p.scrollWidth > p.clientWidth) return true;
    }
    return false;
  };
  const stickout = [];
  const small = [];
  for (const el of document.querySelectorAll("main *, header *, [role=dialog] *, nav *")) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.right > vw + 1 && !inScroller(el)) {
      stickout.push((el.tagName + "." + String(el.className).slice(0, 40)) + " right=" + Math.round(r.right));
    }
    if (phone && el.matches("button, a[href], select, input:not([type=hidden]), [role=button]") && !inScroller(el)) {
      if (r.height < min - 0.5 || (r.width < min - 0.5 && !el.matches("input"))) {
        small.push((el.textContent || el.getAttribute("aria-label") || el.tagName).trim().slice(0, 24)
          + " " + Math.round(r.width) + "x" + Math.round(r.height));
      }
    }
  }
  // A pane must be able to scroll whatever overflows inside it.
  let unscrollable = 0;
  const dialog = document.querySelector("[role=dialog]");
  if (dialog) {
    let worst = 0, scrollable = false;
    const walk = (el) => {
      const s = getComputedStyle(el);
      if (/auto|scroll/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 4) scrollable = true;
      worst = Math.max(worst, el.scrollHeight - el.clientHeight);
      for (const c of el.children) walk(c);
    };
    walk(dialog);
    if (worst > 4 && !scrollable) unscrollable = worst;
  }
  return {
    pageOverflow: document.documentElement.scrollWidth - vw,
    stickout: stickout.slice(0, 5),
    small: small.slice(0, 6),
    unscrollable,
    dialog: Boolean(dialog),
  };
}
"""


def main(width: int = 390, height: int = 844) -> int:
    token = webauth.read_token()
    if not token:
        print("No web token — start `kravein serve` first.", file=sys.stderr)
        return 2
    phone = width < 640

    def run(context):
        page = context.new_page()
        page.set_viewport_size({"width": width, "height": height})
        results = []
        for route in ROUTES:
            # Fresh, cache-busted document per route (stale bundles hide fixes).
            page.goto(
                f"http://127.0.0.1:8787/?token={token}&r={time.time()}#{route}",
                wait_until="networkidle",
            )
            page.wait_for_timeout(1200)
            results.append((route or "home", page.evaluate(PROBE, [MIN_TARGET, phone])))
        page.goto(f"http://127.0.0.1:8787/?token={token}&r={time.time()}",
                  wait_until="networkidle")
        page.wait_for_timeout(800)
        dock = page.evaluate(
            "() => [...document.querySelectorAll('nav[aria-label=Modules] button[data-dock]')]"
            ".filter(b => b.getAttribute('aria-disabled') !== 'true').length"
        )
        # On-screen keyboard: the WebView viewport shrinks (resizes-content), so
        # emulate that and require the composer to stay inside it.
        page.set_viewport_size({"width": width, "height": height // 2})
        page.wait_for_timeout(500)
        composer = page.evaluate(
            "() => { const t = document.querySelector('textarea[data-composer]');"
            " if (!t) return null; const r = t.getBoundingClientRect();"
            " return {top: r.top, bottom: r.bottom, vh: innerHeight}; }"
        )
        page.close()
        return results, dock, composer

    try:
        results, dock, composer = browser.call(run, timeout=300)
    except Exception as exc:
        print(f"Could not drive the UI: {exc}", file=sys.stderr)
        return 2

    failed = []
    for route, r in results:
        ok = (r["pageOverflow"] <= 1 and not r["stickout"] and not r["small"]
              and not r["unscrollable"] and (r["dialog"] or route == "home"))
        if not ok:
            failed.append(route)
        print(f"  {route:20} pageOverflow={r['pageOverflow']:4}px  "
              f"{'ok' if ok else 'FAIL'}")
        for key in ("stickout", "small"):
            for item in r[key]:
                print(f"             {key}: {item}")
        if r["unscrollable"]:
            print(f"             pane overflows by {r['unscrollable']}px with nothing to scroll it")
        if route != "home" and not r["dialog"]:
            print("             route did not open a pane")

    dock_ok = dock >= MODULES_BUILT
    print(f"  dock       buttons={dock}  {'ok' if dock_ok else 'FAIL'}")
    # Only phones shrink for a keyboard; a desktop window half a screen high is
    # not a case the shell is designed for.
    kb_ok = not phone or (
        bool(composer) and composer["top"] >= 0 and composer["bottom"] <= composer["vh"]
    )
    print(f"  keyboard   composer={composer}  {'ok' if kb_ok else 'FAIL'}")
    print()
    if failed or not dock_ok or not kb_ok:
        print(f"Layout problems: {', '.join(failed) or 'dock/keyboard'}")
        return 1
    print(f"All {len(results)} routes fit at {width}x{height}.")
    return 0


if __name__ == "__main__":
    args = [int(a) for a in sys.argv[1:3]]
    try:
        raise SystemExit(main(*args))
    finally:
        with __import__("contextlib").suppress(Exception):
            browser.shutdown()
