import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { formatWhen, parseRules, usesLabel, withoutRule } from "./rules.ts";

test("parseRules keeps only rules it can show and revoke", () => {
  const rules = parseRules({
    rules: [
      { id: "r1", signature: "pkill *", created_at: 1, uses: 2 },
      { id: "", signature: "x" },
      { signature: "no id" },
      { id: "r3" },
      null,
    ],
  });
  assert.deepEqual(rules.map((r) => r.id), ["r1"]);
  assert.deepEqual(parseRules(null), []);
  assert.deepEqual(parseRules({ rules: "nope" }), []);
});

test("revoking removes exactly that rule", () => {
  const rules = parseRules({ rules: [{ id: "a", signature: "x" }, { id: "b", signature: "y" }] });
  assert.deepEqual(withoutRule(rules, "a").map((r) => r.id), ["b"]);
  assert.equal(withoutRule(rules, "zzz").length, 2);
});

test("times: seconds, milliseconds and ISO all read as a date; missing is never", () => {
  const sec = formatWhen(1_700_000_000);
  assert.equal(sec, formatWhen(1_700_000_000_000));
  assert.equal(sec, formatWhen(new Date(1_700_000_000_000).toISOString()));
  assert.match(sec, /2023/);
  assert.equal(formatWhen(null), "never");
  assert.equal(formatWhen(undefined), "never");
  assert.equal(formatWhen(0), "never");
  assert.equal(formatWhen("not a date"), "unknown");
});

test("use counts", () => {
  assert.equal(usesLabel(undefined), "used 0 times");
  assert.equal(usesLabel(1), "used once");
  assert.equal(usesLabel(5), "used 5 times");
});

test("the list calls GET and DELETE on the rules endpoint, has an empty state, and cannot create", () => {
  const src = readFileSync(new URL("../components/AlwaysRules.tsx", import.meta.url), "utf8");
  assert.match(src, /api<unknown>\("\/api\/safety\/rules"\)/);
  assert.match(src, /`\/api\/safety\/rules\/\$\{encodeURIComponent\(id\)\}`, \{ method: "DELETE" \}/);
  assert.match(src, /No commands are always approved/);
  assert.doesNotMatch(src, /method: "POST"/);
  // the row leaves the list only after the DELETE resolved
  assert.ok(src.indexOf('method: "DELETE"') < src.indexOf("withoutRule(current, id)"));
});
