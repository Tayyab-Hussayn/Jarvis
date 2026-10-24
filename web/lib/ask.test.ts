import assert from "node:assert/strict";
import { test } from "node:test";

import { ASK_MAX_CHARS, cleanAsk, extractAsk } from "./ask.ts";

test("extractAsk returns the decoded text and removes only ask", () => {
  const url = new URL("http://h/?token=abc&ask=hello%20world%0Aline2&x=1");
  assert.equal(extractAsk(url), "hello world\nline2");
  assert.equal(url.searchParams.has("ask"), false);
  assert.equal(url.searchParams.get("token"), "abc");
  assert.equal(url.searchParams.get("x"), "1");
});

test("extractAsk is null when there is no ask", () => {
  const url = new URL("http://h/?token=abc");
  assert.equal(extractAsk(url), null);
});

test("an empty or blank ask is removed but yields empty text", () => {
  for (const q of ["?ask=", "?ask=%20%0A%20"]) {
    const url = new URL(`http://h/${q}`);
    assert.equal(extractAsk(url), "");
    assert.equal(url.searchParams.has("ask"), false);
  }
});

test("long text is capped at ASK_MAX_CHARS", () => {
  assert.equal(cleanAsk("a".repeat(ASK_MAX_CHARS + 500)).length, ASK_MAX_CHARS);
  assert.equal(cleanAsk("a".repeat(ASK_MAX_CHARS)).length, ASK_MAX_CHARS);
});

test("the cap never splits a surrogate pair", () => {
  const out = cleanAsk("😀".repeat(ASK_MAX_CHARS + 10));
  assert.equal(Array.from(out).length, ASK_MAX_CHARS);
  assert.ok(!/[\ud800-\udbff]$/.test(out));
});

test("control characters are stripped, markup is left as plain text", () => {
  assert.equal(cleanAsk("a\u0000b\u0007c\td\r\ne"), "abc\td\ne");
  assert.equal(cleanAsk("<b>hi</b> **x**"), "<b>hi</b> **x**");
});
