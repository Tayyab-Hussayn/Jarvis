import assert from "node:assert/strict";
import { test } from "node:test";

import {
  capLines, COLLAPSE_LINES, formatDuration, historyStep, newHistory, parseResult,
  pushHistory,
} from "./console.ts";

test("parses stdout, stderr and exit code", () => {
  const r = parseResult("$ ls\na\nb\n[stderr]\noops\n[exit 2]", "ls");
  assert.deepEqual(r.lines, [
    { stream: "stdout", text: "a" },
    { stream: "stdout", text: "b" },
    { stream: "stderr", text: "oops" },
  ]);
  assert.equal(r.exit, 2);
});

test("no output and timeout", () => {
  assert.deepEqual(parseResult("$ true\n(no output)\n[exit 0]", "true"), {
    lines: [], exit: 0, timedOut: false,
  });
  const t = parseResult("$ sleep 99\n[timed out after 30s]\n(no output)\n[exit -9]", "sleep 99");
  assert.equal(t.timedOut, true);
  assert.equal(t.exit, -9);
  assert.deepEqual(t.lines, [{ stream: "note", text: "timed out" }]);
});

test("a refusal or error is a plain note with no exit code", () => {
  const r = parseResult("Working directory does not exist: /x", "ls");
  assert.deepEqual(r.lines, [{ stream: "note", text: "Working directory does not exist: /x" }]);
  assert.equal(r.exit, null);
});

test("long output is capped until expanded", () => {
  const lines = Array.from({ length: COLLAPSE_LINES + 5 }, (_, i) => i);
  assert.deepEqual([capLines(lines, false).shown.length, capLines(lines, false).hidden], [COLLAPSE_LINES, 5]);
  assert.equal(capLines(lines, true).hidden, 0);
  assert.equal(capLines([1, 2], false).hidden, 0);
});

test("durations", () => {
  assert.equal(formatDuration(12), "12 ms");
  assert.equal(formatDuration(1500), "1.5 s");
  assert.equal(formatDuration(12000), "12 s");
});

test("history: up/down walk, draft is restored, duplicates collapse", () => {
  let h = newHistory();
  h = pushHistory(h, "ls");
  h = pushHistory(h, "pwd");
  h = pushHistory(h, "pwd");
  h = pushHistory(h, "   ");
  assert.deepEqual(h.items, ["ls", "pwd"]);

  let step = historyStep(h, "up", "dra");
  assert.equal(step.value, "pwd");
  step = historyStep(step.history, "up", step.value);
  assert.equal(step.value, "ls");
  step = historyStep(step.history, "up", step.value);
  assert.equal(step.value, "ls");           // stops at the oldest
  step = historyStep(step.history, "down", step.value);
  assert.equal(step.value, "pwd");
  step = historyStep(step.history, "down", step.value);
  assert.equal(step.value, "dra");          // back to what was being typed
  step = historyStep(step.history, "down", step.value);
  assert.equal(step.value, "dra");
});
