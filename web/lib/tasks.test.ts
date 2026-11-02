import assert from "node:assert/strict";
import { test } from "node:test";

import { initialModel } from "./agents.ts";
import {
  applyCancelled,
  applyFrame,
  chipLabel,
  dismissNotice,
  elapsedNow,
  hydrate,
  initialTasks,
  markSeen,
  noticeIds,
  overlayAgents,
  parseTask,
  tasksFor,
  trayTasks,
  type TasksModel,
} from "./tasks.ts";

const row = (over: Record<string, unknown> = {}) => ({
  id: "t3a9", agent: "system-ops", task: "folder sizes", status: "running",
  step: 0, tool: "", created: 100, elapsed: 0, ...over,
});

const feed = (frames: unknown[], start: TasksModel = initialTasks(), t0 = 1000) =>
  frames.reduce<TasksModel>((m, f, i) => applyFrame(m, f, t0 + i * 100), start);

test("a task frame creates a task and restarts the connector pulse once", () => {
  let m = feed([row({ status: "queued" })]);
  assert.equal(m.byId.t3a9.status, "queued");
  assert.equal(m.startSeq["system-ops"], 1);
  // steps of the same task are not new tasks: no second pulse out
  m = feed([row({ step: 1, tool: "disk_usage" }), row({ step: 2, tool: "run_command" })], m);
  assert.equal(m.startSeq["system-ops"], 1);
  assert.equal(m.byId.t3a9.step, 2);
  assert.equal(m.byId.t3a9.tool, "run_command");
});

test("two parallel tasks each pulse and both show as active", () => {
  const m = feed([row(), row({ id: "t7b1", task: "check logs", created: 101 })]);
  assert.equal(m.startSeq["system-ops"], 2);
  assert.deepEqual(m.order, ["t7b1", "t3a9"]); // newest first
});

test("an ended task highlights its chip until it is opened", () => {
  let m = feed([row(), row({ status: "done", outcome: "done", summary: "3 folders, 2 GB", elapsed: 12 })]);
  assert.equal(m.endSeq["system-ops"], 1);
  assert.equal(m.fresh.t3a9, true);
  m = markSeen(m, "t3a9");
  assert.equal(m.fresh.t3a9, undefined);
  assert.equal(m.byId.t3a9.summary, "3 folders, 2 GB");
});

test("a late or stale answer never rolls a finished task back", () => {
  let m = feed([row({ status: "failed", outcome: "failed", step: 4 })]);
  m = feed([row({ status: "running", step: 2 })], m);
  assert.equal(m.byId.t3a9.status, "failed");
  m = hydrate(m, [row({ status: "running", step: 1 })], 5000);
  assert.equal(m.byId.t3a9.status, "failed");
  assert.equal(m.byId.t3a9.step, 4);
});

test("a step counter never goes down", () => {
  let m = feed([row({ step: 5 })]);
  m = hydrate(m, [row({ step: 3 })], 9000);
  assert.equal(m.byId.t3a9.step, 5);
});

test("hydration on load/resume fills the list without pulses or highlights", () => {
  const m = hydrate(initialTasks(), [
    row({ status: "running" }),
    row({ id: "t0c2", status: "done", outcome: "done", created: 50 }),
    { nonsense: true },
    null,
  ], 1000);
  assert.deepEqual(m.order, ["t3a9", "t0c2"]);
  assert.deepEqual(m.startSeq, {});
  assert.deepEqual(m.fresh, {});
});

test("hydration finishing a task we saw running still ends it, without a replayed highlight", () => {
  let m = feed([row()]);
  m = hydrate(m, [row({ status: "cancelled", outcome: "cancelled" })], 4000);
  assert.equal(m.byId.t3a9.status, "cancelled");
  assert.equal(m.fresh.t3a9, undefined);
});

test("malformed rows are rejected, not trusted", () => {
  assert.equal(parseTask(row({ status: "exploded" }), 0), null);
  assert.equal(parseTask(row({ id: "" }), 0), null);
  assert.equal(parseTask("x", 0), null);
  const t = parseTask(row({ step: -3, elapsed: "soon", task: "x".repeat(900) }), 0);
  assert.equal(t?.step, 0);
  assert.equal(t?.elapsed, 0);
  assert.equal(t?.task.length, 200);
});

test("Stop's other_tasks become a notice that drops finished ones", () => {
  let m = feed([row(), row({ id: "t7b1", created: 101 })]);
  m = applyCancelled(m, { t: "cancelled", id: "", tasks_cancelled: 0, other_tasks: ["t3a9", "t7b1"] });
  assert.deepEqual(noticeIds(m), ["t3a9", "t7b1"]);
  // one ends while the notice is up: it no longer offers to cancel it
  m = feed([row({ status: "done", outcome: "done" })], m);
  assert.deepEqual(noticeIds(m), ["t7b1"]);
  // cancelling the last one clears the notice by itself
  m = hydrate(m, [row({ id: "t7b1", status: "cancelled", outcome: "cancelled", created: 101 })], 1);
  assert.deepEqual(noticeIds(m), []);
});

test("Keep dismisses the notice; an empty or missing other_tasks shows none", () => {
  let m = applyCancelled(feed([row()]), { other_tasks: ["t3a9"] });
  assert.equal(noticeIds(m).length, 1);
  m = dismissNotice(m);
  assert.deepEqual(noticeIds(m), []);
  assert.equal(applyCancelled(m, { other_tasks: [] }).notice, null);
  assert.equal(applyCancelled(m, { t: "cancelled" }).notice, null);
  assert.equal(applyCancelled(m, { other_tasks: [3, null, ""] }).notice, null);
});

test("a task id the page has never seen still counts in the notice", () => {
  const m = applyCancelled(initialTasks(), { other_tasks: ["tffff"] });
  assert.deepEqual(noticeIds(m), ["tffff"]);
});

test("elapsed runs on the local clock while active and freezes when done", () => {
  const m = feed([row({ elapsed: 10 })], initialTasks(), 1000);
  assert.equal(elapsedNow(m.byId.t3a9, 4000), 13);
  assert.equal(chipLabel(m.byId.t3a9, 4000), "starting · 13s");
  const s = feed([row({ step: 2, tool: "disk_usage", elapsed: 70 })], initialTasks(), 0);
  assert.equal(chipLabel(s.byId.t3a9, 0), "disk_usage · step 2 · 1m10s");
  const d = feed([row({ status: "done", outcome: "done", elapsed: 65 })], initialTasks(), 0);
  assert.equal(elapsedNow(d.byId.t3a9, 999999), 65);
  assert.equal(chipLabel(d.byId.t3a9, 0), "done · 1m05s");
});

test("overlay: a sub-agent works while the Orchestrator is idle, with a pulse per task", () => {
  const model = initialModel();
  assert.equal(model.agents.orchestrator.state, "idle");
  const m = feed([row(), row({ id: "t7b1", created: 101, task: "check logs" })]);
  const shown = overlayAgents(model, m);
  assert.equal(shown.agents["system-ops"].state, "working");
  assert.equal(shown.agents["system-ops"].task, "2 background tasks");
  assert.equal(shown.agents["system-ops"].outSeq, 2);
  assert.equal(shown.agents.orchestrator.state, "idle");
  assert.equal(shown.agents.research.state, "idle");
  assert.equal(model.agents["system-ops"].state, "idle"); // the store is not mutated
});

test("overlay: a single task shows its text and tool; a waiting agent stays waiting", () => {
  const model = initialModel();
  const m = feed([row({ step: 1, tool: "disk_usage" })]);
  const one = overlayAgents(model, m).agents["system-ops"];
  assert.equal(one.task, "folder sizes");
  assert.equal(one.tool, "disk_usage");

  const waiting = {
    ...model,
    agents: { ...model.agents, "system-ops": { ...model.agents["system-ops"], state: "waiting" as const } },
  };
  assert.equal(overlayAgents(waiting, m).agents["system-ops"].state, "waiting");
});

test("overlay: finished tasks stop 'working' but still pulse back", () => {
  const model = initialModel();
  const m = feed([row(), row({ status: "done", outcome: "done" })]);
  const shown = overlayAgents(model, m);
  assert.equal(shown.agents["system-ops"].state, "idle");
  assert.equal(shown.agents["system-ops"].backSeq, 1);
});

test("overlay changes nothing when there are no tasks", () => {
  const model = initialModel();
  assert.equal(overlayAgents(model, initialTasks()), model);
});

test("the tray holds running tasks and unopened reports, plus anything pinned", () => {
  let m = feed([
    row({ id: "a", created: 1 }),
    row({ id: "b", created: 2, status: "done", outcome: "done" }),
    row({ id: "c", created: 3, status: "done", outcome: "done" }),
  ]);
  assert.deepEqual(trayTasks(m).map((t) => t.id).sort(), ["a", "b", "c"]);
  m = markSeen(m, "b");
  assert.deepEqual(trayTasks(m).map((t) => t.id).sort(), ["a", "c"]);
  assert.deepEqual(trayTasks(m, ["b"]).map((t) => t.id).sort(), ["a", "b", "c"]);
  // a finished task that was only ever hydrated is history, not tray content
  const h = hydrate(initialTasks(), [row({ status: "done", outcome: "done" })], 0);
  assert.deepEqual(trayTasks(h), []);
});

test("tasksFor lists one agent's tasks, newest first", () => {
  const m = feed([row(), row({ id: "t9", agent: "research", created: 105 }), row({ id: "t8", created: 110 })]);
  assert.deepEqual(tasksFor(m, "system-ops").map((t) => t.id), ["t8", "t3a9"]);
});
