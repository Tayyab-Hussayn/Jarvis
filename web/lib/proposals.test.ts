import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyFrame,
  cardModel,
  decideRequest,
  mergeList,
  ordered,
  parseProposal,
  pendingCount,
  upsert,
  type Proposal,
} from "./proposals.ts";

const row = (over: Record<string, unknown> = {}) => ({
  id: "p1",
  title: "Clear the 12 GB cache",
  reasons: ["Disk is 91% full", "~/.cache is 12 GB"],
  kind: "delegate",
  agent: "system-ops",
  task: "Delete ~/.cache/pip",
  risk: "low",
  status: "pending",
  created_by: "review",
  created_at: 1000,
  ...over,
});

const make = (over: Record<string, unknown> = {}): Proposal => parseProposal(row(over))!;

test("a well-formed row parses; a malformed one is refused", () => {
  const p = parseProposal(row());
  assert.equal(p?.title, "Clear the 12 GB cache");
  assert.deepEqual(p?.reasons, ["Disk is 91% full", "~/.cache is 12 GB"]);
  assert.equal(p?.agent, "system-ops");
  for (const bad of [null, "x", {}, row({ id: "" }), row({ status: "exploding" }), row({ title: "" })]) {
    assert.equal(parseProposal(bad), null);
  }
});

test("unknown risk reads as low, never invented as major", () => {
  assert.equal(make({ risk: "weird" }).risk, "low");
  assert.equal(make({ risk: "major" }).risk, "major");
});

test("a live frame adds a proposal and updates a known one", () => {
  let list: Proposal[] = [];
  list = applyFrame(list, { proposal: row() });
  assert.equal(list.length, 1);
  list = applyFrame(list, { proposal: row({ status: "running", decided_at: 1100 }) });
  assert.equal(list.length, 1);
  assert.equal(list[0].status, "running");
  list = applyFrame(list, { proposal: row({ status: "done", result: "Freed 12 GB." }) });
  assert.equal(list[0].status, "done");
  assert.equal(list[0].result, "Freed 12 GB.");
  assert.equal(applyFrame(list, { proposal: "junk" }), list, "junk frames change nothing");
});

test("a late REST listing never rolls a card back past a live frame", () => {
  let list = applyFrame([], { proposal: row({ status: "done", result: "ok" }) });
  list = mergeList(list, [row({ status: "pending" }), row({ id: "p2", status: "pending" })]);
  assert.equal(list.find((p) => p.id === "p1")?.status, "done");
  assert.equal(list.find((p) => p.id === "p2")?.status, "pending", "new rows are added");
  assert.equal(mergeList(list, "not a list"), list);
});

test("pending come first newest-first, then the rest by latest activity", () => {
  const list = [
    make({ id: "old-done", status: "done", created_at: 1, decided_at: 50 }),
    make({ id: "p-old", created_at: 10 }),
    make({ id: "recent-declined", status: "declined", created_at: 5, decided_at: 90 }),
    make({ id: "p-new", created_at: 20 }),
  ];
  assert.deepEqual(ordered(list).map((p) => p.id), ["p-new", "p-old", "recent-declined", "old-done"]);
});

test("the dock badge counts only proposals waiting on the owner", () => {
  const list = [make({ id: "a" }), make({ id: "b", status: "running" }), make({ id: "c", status: "done" }), make({ id: "d" })];
  assert.equal(pendingCount(list), 2);
  assert.equal(pendingCount(upsert(list, make({ id: "a", status: "declined" }))), 1);
  assert.equal(pendingCount([]), 0);
});

test("a pending card offers Decline then Approve, Decline focused, Approve primary", () => {
  const card = cardModel(make());
  assert.equal(card.canDecide, true);
  assert.equal(card.focusFirst, "decline");
  assert.deepEqual(card.actions.map((a) => [a.id, a.primary]), [["decline", false], ["approve", true]]);
  assert.equal(card.statusLabel, "Waiting for you");
});

test("major risk gets the coral accent, low does not", () => {
  assert.equal(cardModel(make({ risk: "major" })).accent, "coral");
  assert.equal(cardModel(make({ risk: "major" })).riskLabel, "Major risk");
  assert.equal(cardModel(make({ risk: "low" })).accent, "none");
});

test("a decided card has no actions; done and failed carry the result", () => {
  const running = cardModel(make({ status: "running" }));
  assert.equal(running.canDecide, false);
  assert.equal(running.actions.length, 0);
  assert.equal(running.focusFirst, null);
  const done = cardModel(make({ status: "done", result: "Freed 12 GB." }));
  assert.equal(done.resultTone, "ok");
  assert.equal(done.result, "Freed 12 GB.");
  assert.equal(cardModel(make({ status: "failed", result: "boom" })).resultTone, "bad");
});

test("the decide request names the proposal, the decision and the current session", () => {
  const { path, init } = decideRequest("p1", "approve", "sess-1");
  assert.equal(path, "/api/proposals/p1/decide");
  assert.equal(init.method, "POST");
  assert.deepEqual(JSON.parse(String(init.body)), { decision: "approve", session_id: "sess-1" });
  assert.deepEqual(JSON.parse(String(decideRequest("p1", "decline", "").init.body)), { decision: "decline" });
  assert.equal(decideRequest("a/b", "decline", "").path, "/api/proposals/a%2Fb/decide", "id is encoded");
});
