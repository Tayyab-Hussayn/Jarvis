import assert from "node:assert/strict";
import { test } from "node:test";

import {
  bannerVisible,
  formatClock,
  parsePolicy,
  secondsRemaining,
  type Anchored,
} from "./policy.ts";

const base = { default_seconds: 3600, max_seconds: 14400 };
const auto = (left: number, at = 1000): Anchored => ({
  policy: { mode: "auto", seconds_left: left, ...base },
  at,
});

test("parsePolicy accepts a server answer", () => {
  const p = parsePolicy({ mode: "auto", seconds_left: 90, ...base });
  assert.equal(p?.mode, "auto");
  assert.equal(p?.seconds_left, 90);
});

test("parsePolicy rejects anything malformed, so the selector never shows it", () => {
  assert.equal(parsePolicy(null), null);
  assert.equal(parsePolicy("auto"), null);
  assert.equal(parsePolicy({ mode: "yolo", seconds_left: 0, ...base }), null);
  assert.equal(parsePolicy({ mode: "ask", ...base }), null);
  assert.equal(parsePolicy({ mode: "ask", seconds_left: "0", ...base }), null);
  assert.equal(parsePolicy({ detail: "nope" }), null);
});

test("countdown ticks down from the moment the server answered", () => {
  assert.equal(secondsRemaining(auto(60, 1000), 1000), 60);
  assert.equal(secondsRemaining(auto(60, 1000), 11_000), 50);
  assert.equal(secondsRemaining(auto(60, 1000), 11_001), 50);
  assert.equal(secondsRemaining(auto(60, 1000), 61_000), 0);
  assert.equal(secondsRemaining(auto(60, 1000), 999_999), 0);
});

test("banner shows only for auto with time left", () => {
  assert.equal(bannerVisible(null, 0), false);
  assert.equal(bannerVisible(auto(5), 1000), true);
  assert.equal(bannerVisible(auto(5), 7000), false);
  for (const mode of ["ask", "decline"] as const) {
    const a: Anchored = { policy: { mode, seconds_left: 0, ...base }, at: 0 };
    assert.equal(bannerVisible(a, 0), false);
  }
});

test("a mode change from another tab hides the banner on the next answer", () => {
  assert.equal(bannerVisible(auto(600), 1000), true);
  const after = parsePolicy({ mode: "ask", seconds_left: 0, ...base })!;
  assert.equal(bannerVisible({ policy: after, at: 2000 }, 2000), false);
});

test("formatClock", () => {
  assert.equal(formatClock(0), "00:00");
  assert.equal(formatClock(65), "01:05");
  assert.equal(formatClock(3599), "59:59");
  assert.equal(formatClock(3600), "1:00:00");
  assert.equal(formatClock(14400), "4:00:00");
  assert.equal(formatClock(-3), "00:00");
});

test("a tick older than the answer never shows more than the server said", () => {
  assert.equal(secondsRemaining(auto(60, 5000), 1000), 60);
});
