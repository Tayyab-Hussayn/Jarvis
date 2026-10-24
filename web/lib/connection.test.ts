import assert from "node:assert/strict";
import { test } from "node:test";

import { LOST_RESULT, planRetry, settleOnClose } from "./connection.ts";
import type { Message } from "./protocol.ts";

const tool = (callId: string, done: boolean) => ({
  callId, name: "phone_flashlight", args: {}, display: "", done,
});

test("running tool becomes lost, finished tool is untouched", () => {
  const m: Message = {
    id: "t1", role: "assistant", text: "", pending: true,
    tools: [tool("a", true), tool("b", false)],
  };
  const [out] = settleOnClose([m]);
  assert.equal(out.pending, false);
  assert.equal(out.interrupted, true);
  assert.equal(out.tools![0], m.tools![0]);
  assert.equal(out.tools![1].done, true);
  assert.equal(out.tools![1].lost, true);
  assert.equal(out.tools![1].result, LOST_RESULT);
});

test("pending confirmation is denied", () => {
  const m: Message = {
    id: "t1", role: "assistant", text: "", pending: true,
    confirms: [{ confirmId: "c", action: "x", detail: "", danger: "DESTRUCTIVE",
      expiresAt: 0, status: "pending" }],
  };
  assert.equal(settleOnClose([m])[0].confirms![0].status, "denied");
});

test("settled messages are returned as the same object", () => {
  const m: Message = { id: "t1", role: "assistant", text: "hi", tools: [tool("a", true)] };
  assert.equal(settleOnClose([m])[0], m);
});

test("retry: reconnecting, then offline, delay capped, keeps retrying", () => {
  let attempts = 0;
  const states: string[] = [];
  let last = 0;
  for (let i = 0; i < 8; i++) {
    const p = planRetry(attempts, 1006);
    attempts = p.attempts;
    states.push(p.state);
    last = p.delay!;
  }
  assert.deepEqual(states.slice(0, 4), ["reconnecting", "reconnecting", "reconnecting", "offline"]);
  assert.equal(states[7], "offline");
  assert.equal(last, 15_000);
});

test("retry: policy violation stops", () => {
  const p = planRetry(0, 1008);
  assert.equal(p.state, "unauthorized");
  assert.equal(p.delay, null);
});
