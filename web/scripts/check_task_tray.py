#!/usr/bin/env python
"""Check that the background-task tray never covers other content (#160).

The tray used to float over the NOW / LAST REPORT cards and the suggestion
grid. It now takes layout space in the stream column, and this proves it: for
each viewport and each scenario (hero + 2 tasks, 3 tasks collapsed, expanded,
Stop notice, with a conversation on screen) no visible element of the page
intersects the tray, the tray stays inside the viewport, and the composer is
still fully visible.

Runs against the built `kravein/web-dist` served statically with a mocked
websocket and API, so it needs no `kravein serve` (and does not disturb the
owner's single server on :8787).

    .venv/bin/python web/scripts/check_task_tray.py [outdir]   # screenshots land in outdir

Exits non-zero on any overlap.
"""

from __future__ import annotations

import functools
import http.server
import json
import pathlib
import sys
import threading
import time

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[2] / "kravein" / "web-dist"
PORT = 8899
VIEWPORTS = ((1440, 900), (1280, 700), (390, 844))
NOW = int(time.time())

AGENTS = {"orchestrator_enabled": True, "agents": [
    {"name": "default", "role": "orchestrator", "purpose": "Talks to you.", "tools": 20},
    {"name": "system-ops", "role": "sub", "purpose": "Deep system work.", "tools": 30},
    {"name": "research", "role": "sub", "purpose": "Research.", "tools": 8},
    {"name": "reviewer", "role": "sub", "purpose": "Reviews.", "tools": 5}]}


def task(i, agent, text, status="running", step=1, tool="disk_usage", elapsed=9, **kw):
    return {"t": "task", "id": i, "agent": agent, "task": text, "status": status,
            "step": step, "tool": tool, "created": NOW, "elapsed": elapsed, **kw}


# Probe: the tray rect against everything else that is visible. Overlap of the
# tray with its own descendants and ancestors is fine; with anything else is not.
PROBE = """
() => {
  const tray = document.querySelector("[data-task-tray]");
  if (!tray) return {tray: false};
  const t = tray.getBoundingClientRect();
  const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none"; };
  // What is actually painted: the element's box clipped by every scrolling /
  // overflow-hiding ancestor. Text scrolled out of the transcript is not
  // "under" the tray, it is not visible at all.
  const shown = (el) => {
    const r = el.getBoundingClientRect();
    let [l, tp, rt, b] = [r.left, r.top, r.right, r.bottom];
    for (let p = el.parentElement; p && p !== document.documentElement; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (/auto|scroll|hidden|clip/.test(s.overflowY + s.overflowX)) {
        const q = p.getBoundingClientRect();
        l = Math.max(l, q.left); tp = Math.max(tp, q.top); rt = Math.min(rt, q.right); b = Math.min(b, q.bottom);
      }
    }
    return {left: l, top: tp, right: rt, bottom: b};
  };
  const hit = (r) => r.right > r.left && r.bottom > r.top &&
    r.left < t.right - 1 && r.right > t.left + 1 && r.top < t.bottom - 1 && r.bottom > t.top + 1;
  const name = (el) => (el.getAttribute("aria-label") || el.textContent || el.tagName).trim().slice(0, 40);
  const bad = [];
  const targets = [
    ...document.querySelectorAll("[role=group][aria-label]"),          // NOW / LAST REPORT
    ...document.querySelectorAll("[data-agent-node]"),
    ...document.querySelectorAll("main button"),                        // suggestions, mode toggle, chips
    ...document.querySelectorAll("textarea, [data-composer]"),
    ...document.querySelectorAll("nav button"),
    ...document.querySelectorAll("h1, h2, h3, p"),
  ];
  for (const el of targets) {
    if (!vis(el) || tray.contains(el) || el.contains(tray)) continue;
    if (hit(shown(el))) bad.push(el.tagName + ":" + name(el));
  }
  const ta = document.querySelector("textarea[data-composer]");
  const c = ta ? ta.getBoundingClientRect() : null;
  const vh = window.innerHeight;
  return {
    tray: true, bad,
    inViewport: t.top >= 0 && t.bottom <= vh + 1 && t.left >= -1 && t.right <= window.innerWidth + 1,
    composerVisible: !!c && c.top >= 0 && c.bottom <= vh + 1,
    box: [Math.round(t.left), Math.round(t.top), Math.round(t.right), Math.round(t.bottom)],
    sideways: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  };
}
"""


def main() -> int:
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(".")
    out.mkdir(parents=True, exist_ok=True)
    if not (ROOT / "index.html").exists():
        print("build first: cd web && npm run build", file=sys.stderr)
        return 2

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    failures: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for w, h in VIEWPORTS:
            sockets: list = []

            def on_ws(ws, sockets=sockets):
                sockets.append(ws)
                ws.send(json.dumps({"t": "hello", "session_id": "s1", "assistant_name": "Kravein",
                                    "skills": 123, "provider": "gemini", "model": "flash",
                                    "environment": "niri", "local_client": True}))

            def api(route):
                url = route.request.url
                if "/api/tasks" in url:
                    return route.fulfill(json={"tasks": [], "report": "Full report text."})
                if "/api/agents/state" in url:
                    return route.fulfill(json={"states": []})
                if "/api/agents" in url:
                    return route.fulfill(json=AGENTS)
                return route.fulfill(json={}, status=404)

            def send(frame, sockets=sockets):
                sockets[-1].send(json.dumps(frame))

            page = browser.new_page(viewport={"width": w, "height": h})
            page.route_web_socket("**/ws*", on_ws)
            page.route("**/api/**", api)
            page.goto(f"http://127.0.0.1:{PORT}/?token=x&r={time.time()}")
            page.wait_for_timeout(1200)

            def check(label: str) -> None:
                page.wait_for_timeout(500)
                r = page.evaluate(PROBE)
                tag = f"{w}x{h} {label}"
                page.screenshot(path=str(out / f"160-{w}x{h}-{label.replace(' ', '-')}.png"))
                if not r["tray"]:
                    failures.append(f"{tag}: no tray rendered")
                    return
                if r["bad"]:
                    failures.append(f"{tag}: tray {r['box']} overlaps {r['bad'][:6]}")
                if not r["inViewport"]:
                    failures.append(f"{tag}: tray {r['box']} leaves the viewport")
                if not r["composerVisible"]:
                    failures.append(f"{tag}: composer not fully visible")
                if r["sideways"]:
                    failures.append(f"{tag}: page scrolls sideways")
                print(f"ok   {tag} tray={r['box']}")

            send(task("t1", "system-ops", "Measure the biggest folders in home"))
            send(task("t2", "research", "Compare three backup tools", tool="web_search"))
            check("hero 2 tasks")
            page.get_by_role("button", name="Expand").click()
            check("hero 2 tasks expanded")
            send(task("t3", "reviewer", "Review the last change", tool="read_file"))
            check("3 tasks")
            send({"t": "cancelled", "id": "", "pending_confirms": 0, "tasks_cancelled": 0,
                  "other_tasks": ["t1", "t2", "t3"]})
            check("stop notice")
            # A conversation on screen: the stream scrolls, the tray stays put.
            send({"t": "turn_start", "id": "t10"})
            send({"t": "answer", "id": "t10", "text": "Here is a long answer. " * 120})
            send({"t": "turn_end", "id": "t10", "iterations": 1})
            send(task("t2", "research", "Compare three backup tools", "done", 3, "", 21,
                      outcome="done", summary="restic fits best."))
            check("conversation")
            page.close()
        browser.close()

    for line in failures:
        print("FAIL", line)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
