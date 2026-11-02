import assert from "node:assert/strict";
import { test } from "node:test";

import { readFileSync } from "node:fs";

import { outcomeLabel, overlayConfirms, settleConfirm } from "./confirm.ts";
import type { ConfirmView } from "./protocol.ts";

const pending = (id: string, status: ConfirmView["status"] = "pending"): ConfirmView => ({
  confirmId: id, action: "a", detail: "d", danger: "DESTRUCTIVE", expiresAt: 0, status,
});

test("with no pane open the overlay shows only panel confirmations", () => {
  const out = overlayConfirms([pending("p1")], [{ confirms: [pending("c1")] }], false);
  assert.deepEqual(out.map((c) => c.confirmId), ["p1"]);
});

test("with a pane open every pending inline confirmation is surfaced, never hidden", () => {
  const out = overlayConfirms(
    [pending("p1")],
    [{ confirms: [pending("c1"), pending("c2", "approved")] }, { confirms: [pending("c3")] }, {}],
    true,
  );
  assert.deepEqual(out.map((c) => c.confirmId), ["p1", "c1", "c3"]);
});

test("a card present in both lists is shown once", () => {
  const out = overlayConfirms([pending("x")], [{ confirms: [pending("x")] }], true);
  assert.equal(out.length, 1);
});

test("the confirmation layer sits above panes, the dock, and the palette", () => {
  const css = readFileSync(new URL("../app/tokens.css", import.meta.url), "utf8");
  const z = (name: string) => Number(new RegExp(`--z-${name}:\\s*(\\d+)`).exec(css)?.[1]);
  assert.ok(z("confirm") > z("palette"), "confirm over palette");
  assert.ok(z("palette") > z("pane"), "palette over panes");
  assert.ok(z("pane") > z("dock"), "panes over the dock");
  assert.ok(z("dock") > z("hud"), "dock over the HUD");
});

const card: ConfirmView = {
  confirmId: "c1", action: "close workspace", detail: "", danger: "DESTRUCTIVE",
  expiresAt: 0, status: "pending",
};

test("a pending card closes with the outcome and who answered", () => {
  const done = settleConfirm(card, "c1", "approved", "phone");
  assert.equal(done.status, "approved");
  assert.equal(outcomeLabel(done), "Approved on phone");
  assert.equal(outcomeLabel(settleConfirm(card, "c1", "denied", "")), "Declined");
  assert.equal(outcomeLabel(settleConfirm(card, "c1", "expired", "")), "Timed out — treated as denied");
});

test("other ids and already-settled cards are untouched", () => {
  assert.equal(settleConfirm(card, "c2", "approved", "phone"), card);
  const denied = settleConfirm(card, "c1", "denied", "web");
  assert.equal(settleConfirm(denied, "c1", "approved", "phone"), denied);
});
