import assert from "node:assert/strict";
import { test } from "node:test";

import { LEAD_SECONDS, PlaybackQueue, type PlaybackContext, type PlaybackSource } from "./playback.ts";

class FakeSource implements PlaybackSource {
  onended: (() => void) | null = null;
  startedAt: number | null = null;
  stopped = false;
  start(when: number) {
    this.startedAt = when;
  }
  stop() {
    this.stopped = true;
  }
}

function fake() {
  const sources: FakeSource[] = [];
  const ctx = {
    currentTime: 10,
    createSource() {
      const s = new FakeSource();
      sources.push(s);
      return s;
    },
  } satisfies PlaybackContext & { currentTime: number };
  return { ctx, sources };
}

test("chunks are scheduled back to back, the first a little ahead of now", () => {
  const { ctx, sources } = fake();
  const q = new PlaybackQueue(ctx, 24_000);
  q.push(new Float32Array(2_400)); // 0.1 s
  q.push(new Float32Array(2_400));
  assert.ok(Math.abs(sources[0].startedAt! - (10 + LEAD_SECONDS)) < 1e-9);
  assert.ok(Math.abs(sources[1].startedAt! - (10 + LEAD_SECONDS + 0.1)) < 1e-9);
  assert.ok(Math.abs(q.queuedSeconds - (LEAD_SECONDS + 0.2)) < 1e-9);
});

test("clear stops everything scheduled and forgets the timeline", () => {
  const { ctx, sources } = fake();
  const q = new PlaybackQueue(ctx, 24_000);
  q.push(new Float32Array(2_400));
  q.push(new Float32Array(2_400));
  q.clear();
  assert.ok(sources.every((s) => s.stopped));
  assert.equal(q.playing, false);
  assert.equal(q.queuedSeconds, 0);
  q.push(new Float32Array(2_400));
  // Fresh timeline, not appended after the cleared audio.
  assert.ok(Math.abs(sources[2].startedAt! - (10 + LEAD_SECONDS)) < 1e-9);
});

test("after an underrun the next chunk restarts ahead of now", () => {
  const { ctx, sources } = fake();
  const q = new PlaybackQueue(ctx, 24_000);
  q.push(new Float32Array(2_400));
  ctx.currentTime = 20;
  q.push(new Float32Array(2_400));
  assert.ok(Math.abs(sources[1].startedAt! - (20 + LEAD_SECONDS)) < 1e-9);
});

test("a finished source leaves the active set", () => {
  const { ctx, sources } = fake();
  const q = new PlaybackQueue(ctx, 24_000);
  q.push(new Float32Array(240));
  assert.equal(q.playing, true);
  sources[0].onended?.();
  assert.equal(q.playing, false);
});
