import assert from "node:assert/strict";
import { test } from "node:test";

import { formatDuration } from "./format.ts";

test("formatDuration reads in the unit that fits", () => {
  assert.equal(formatDuration(240), "240 ms");
  assert.equal(formatDuration(3200), "3.2 s");
  assert.equal(formatDuration(65_000), "1 m 05 s");
  assert.equal(formatDuration(-1), "");
  assert.equal(formatDuration(Number.NaN), "");
});
