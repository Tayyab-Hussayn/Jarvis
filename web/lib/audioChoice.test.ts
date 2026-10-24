import assert from "node:assert/strict";
import { test } from "node:test";

import { defaultAudioChoice, parseChoice } from "./audioChoice.ts";

test("a remote page defaults to its own mic, the laptop page to the laptop", () => {
  assert.equal(defaultAudioChoice(false, null), "device");
  assert.equal(defaultAudioChoice(true, null), "laptop");
  assert.equal(defaultAudioChoice(undefined, null), "laptop");
});

test("an earlier explicit choice wins over the default", () => {
  assert.equal(defaultAudioChoice(false, "laptop"), "laptop");
  assert.equal(defaultAudioChoice(true, "device"), "device");
});

test("junk in storage is ignored", () => {
  assert.equal(parseChoice("speaker"), null);
  assert.equal(defaultAudioChoice(false, "speaker"), "device");
});
