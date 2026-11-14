import assert from "node:assert/strict";
import { test } from "node:test";

import {
  formatCost,
  moveEntry,
  parseModels,
  parseUsage,
  pickerOptions,
  putRequest,
  setEntry,
  totalCost,
} from "./models.ts";

const state = parseModels({
  catalog: {
    "a:ok": { tool_calling: "verified" },
    "a:raw": { tool_calling: "untested" },
    "a:bad": { tool_calling: "failed", probe: { error: "HTTPError 404" } },
    "a:weird": "nonsense",
  },
  roles: ["orchestrator", "utility"],
  chains: { utility: ["a:raw"] },
});

test("parseModels survives odd shapes", () => {
  assert.equal(state.catalog.length, 4);
  assert.equal(state.catalog.find((c) => c.key === "a:weird")?.tool, "untested");
  assert.deepEqual(parseModels(null).catalog, []);
  assert.deepEqual(parseModels({ chains: { x: "no" } }).chains, { x: [] });
});

test("an agent role's first slot offers only verified models, with a reason", () => {
  const first = pickerOptions("orchestrator", state.catalog, [], 0);
  assert.equal(first.find((o) => o.key === "a:ok")?.disabled, false);
  const raw = first.find((o) => o.key === "a:raw");
  assert.equal(raw?.disabled, true);
  assert.match(raw?.reason ?? "", /not probed: run `kravein models probe a:raw`/);
  assert.match(first.find((o) => o.key === "a:bad")?.reason ?? "", /failed.*HTTPError 404/);
});

test("failover slots and non-agent roles accept unverified models", () => {
  const second = pickerOptions("orchestrator", state.catalog, ["a:ok"], 1);
  assert.equal(second.find((o) => o.key === "a:raw")?.disabled, false);
  assert.equal(second.find((o) => o.key === "a:ok")?.reason, "already in this chain");
  const util = pickerOptions("utility", state.catalog, [], 0);
  assert.ok(util.every((o) => !o.disabled));
});

test("chain edits do not mutate", () => {
  const c = ["a", "b", "c"];
  assert.deepEqual(moveEntry(c, 2, 0), ["c", "a", "b"]);
  assert.deepEqual(moveEntry(c, 0, 5), c);
  assert.deepEqual(setEntry(c, 3, "d"), ["a", "b", "c", "d"]);
  assert.deepEqual(c, ["a", "b", "c"]);
});

test("putRequest encodes the colon in worker roles", () => {
  const r = putRequest("worker:research", ["a:ok"]);
  assert.equal(r.path, "/api/models/roles/worker%3Aresearch");
  assert.equal(r.init.body, JSON.stringify({ chain: ["a:ok"] }));
});

test("spend rows: unknown cost is a dash, never zero", () => {
  const rows = parseUsage({
    rows: [
      { role: "", provider: "g", model: "m", calls: 3, reported: 2, failures: 1, input: 5, cached: 0, output: 2, cost: null },
      { role: "utility", provider: "g", model: "m", calls: 1, reported: 1, cost: 0.5 },
      "junk",
    ],
  });
  assert.equal(rows.length, 2);
  assert.equal(rows[0].role, "default");
  assert.equal(rows[0].unreported, 1);
  assert.equal(formatCost(rows[0].cost), "—");
  assert.equal(totalCost(rows), 0.5);
  assert.equal(totalCost([rows[0]]), null);
});
