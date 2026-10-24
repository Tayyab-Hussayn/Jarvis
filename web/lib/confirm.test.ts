import assert from "node:assert/strict";
import { test } from "node:test";

import { outcomeLabel, settleConfirm } from "./confirm.ts";
import type { ConfirmView } from "./protocol.ts";

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
