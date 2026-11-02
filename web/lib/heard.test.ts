import assert from "node:assert/strict";
import { test } from "node:test";

import { routeHeard, scopedTurn, turnKey, type HeardState } from "./heard.ts";

test("a restarted voice session reusing lt1 does not rewrite the old session's line", () => {
  // Session 1 spoke lt1 -> s1-user. Session 2's first turn is also lt1:
  // unscoped, that was a "rewrite" of session 1's line (the new line vanished).
  const lines = new Map([[scopedTurn(1, "lt1")!, "s1-user"]]);
  const idleState: HeardState = { open: null, openServerTurn: null, lines };
  assert.deepEqual(routeHeard(idleState, scopedTurn(2, "lt1")), { kind: "orphan" });
  const open: HeardState = { open: "s2", openServerTurn: null, lines };
  assert.deepEqual(routeHeard(open, scopedTurn(2, "lt1")), { kind: "bind-open" });
  // a late replace from session 1 still lands on session 1's line
  assert.deepEqual(routeHeard(idleState, scopedTurn(1, "lt1")), { kind: "rewrite", id: "s1-user" });
  assert.equal(scopedTurn(2, undefined), null);
});

const idle = (lines: [string, string][] = []): HeardState => ({
  open: null,
  openServerTurn: null,
  lines: new Map(lines),
});

test("a known turn rewrites its own line even after the turn closed", () => {
  // Reply already shown and `listening` seen: open is null.
  const state = idle([["7", "s3-user"]]);
  assert.deepEqual(routeHeard(state, 7), { kind: "rewrite", id: "s3-user" });
  assert.deepEqual(routeHeard(state, "7"), { kind: "rewrite", id: "s3-user" });
});

test("the first frame of a turn binds to the open spoken turn", () => {
  const state: HeardState = { open: "s1", openServerTurn: null, lines: new Map() };
  assert.deepEqual(routeHeard(state, 1), { kind: "bind-open" });
});

test("two fast turns: a new turn id while another is open starts a new turn", () => {
  const state: HeardState = {
    open: "s1", openServerTurn: "1", lines: new Map([["1", "s1-user"]]),
  };
  assert.deepEqual(routeHeard(state, 2), { kind: "new-turn" });
  // and the first turn's late replace still lands on the first line
  assert.deepEqual(routeHeard(state, 1), { kind: "rewrite", id: "s1-user" });
});

test("an unknown turn after reconnect renders once, as a line only", () => {
  assert.deepEqual(routeHeard(idle(), 9), { kind: "orphan" });
});

test("an untagged frame keeps the old open-turn behaviour", () => {
  assert.deepEqual(routeHeard(idle(), undefined), { kind: "new-turn" });
  const open: HeardState = { open: "s2", openServerTurn: null, lines: new Map() };
  assert.deepEqual(routeHeard(open, undefined), { kind: "bind-open" });
});

test("turnKey rejects empty and non-finite ids", () => {
  assert.equal(turnKey(""), null);
  assert.equal(turnKey(NaN), null);
  assert.equal(turnKey(0), "0");
});
