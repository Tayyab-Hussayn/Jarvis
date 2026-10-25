import assert from "node:assert/strict";
import { test } from "node:test";

import { planRetry } from "./connection.ts";
import type { ServerFrame } from "./protocol.ts";
import {
  CLOSE_REPLACED, SESSION_KEY, loadSessionId, maxTurnSeq, saveSessionId,
  type KeyValue,
} from "./resume.ts";

function memory(): KeyValue & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => void data.set(k, v),
    removeItem: (k) => void data.delete(k),
  };
}

test("the session id round-trips and an empty id forgets it", () => {
  const store = memory();
  assert.equal(loadSessionId(store), "");
  saveSessionId("abc", store);
  assert.equal(store.data.get(SESSION_KEY), "abc");
  assert.equal(loadSessionId(store), "abc");
  saveSessionId("", store);
  assert.equal(loadSessionId(store), "");
});

test("blocked storage means no session, never an exception", () => {
  const broken: KeyValue = {
    getItem: () => { throw new Error("denied"); },
    setItem: () => { throw new Error("denied"); },
    removeItem: () => { throw new Error("denied"); },
  };
  assert.equal(loadSessionId(broken), "");
  assert.doesNotThrow(() => saveSessionId("x", broken));
  assert.equal(loadSessionId(null), "");
});

test("new turn numbers continue after the replayed ones", () => {
  const frames = [
    { t: "turn_start", id: "t3" },
    { t: "turn_start", id: "t12" },
    { t: "turn_start", id: "s5" },
    { t: "pong" },
    { t: "error", message: "x" },
  ] as ServerFrame[];
  assert.equal(maxTurnSeq(frames), 12);
  assert.equal(maxTurnSeq([]), 0);
});

test("a socket replaced by another tab stops retrying", () => {
  const plan = planRetry(0, CLOSE_REPLACED);
  assert.equal(plan.delay, null);
  assert.equal(plan.state, "replaced");
});
