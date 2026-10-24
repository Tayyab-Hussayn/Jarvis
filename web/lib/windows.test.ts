import assert from "node:assert/strict";
import { test } from "node:test";

import { homeOf, shortCwd, windowArgs } from "./windows.ts";

test("focus/close send the window id and nothing name-based", () => {
  assert.deepEqual(windowArgs({ id: "0xabc" }), { id: "0xabc" });
});

test("shortCwd uses ~ for home and trims deep paths", () => {
  assert.equal(shortCwd("/home/u", "/home/u"), "~");
  assert.equal(shortCwd("/home/u/code", "/home/u"), "~/code");
  assert.equal(shortCwd("/home/u/a/b/c/d", "/home/u"), "~/…/c/d");
  assert.equal(shortCwd("/home/user2/x", "/home/u"), "/home/user2/x");
  assert.equal(shortCwd("/var/log/nginx/old", undefined), "/…/nginx/old");
  assert.equal(shortCwd(null), "");
});

test("homeOf finds the home prefix", () => {
  assert.equal(homeOf("/home/krawin/code/x"), "/home/krawin");
  assert.equal(homeOf("/root"), "/root");
  assert.equal(homeOf("/tmp/x"), undefined);
});
