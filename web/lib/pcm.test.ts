import assert from "node:assert/strict";
import { test } from "node:test";

import {
  Chunker,
  Pcm16Reader,
  Resampler,
  floatToPcm16,
  pcm16ToFloat,
} from "./pcm.ts";

function sine(rate: number, hz: number, seconds: number): Float32Array {
  const out = new Float32Array(Math.round(rate * seconds));
  for (let i = 0; i < out.length; i += 1) out[i] = Math.sin((2 * Math.PI * hz * i) / rate);
  return out;
}

test("48k to 16k yields a third of the samples, regardless of block size", () => {
  const input = sine(48_000, 440, 1);
  const whole = new Resampler(48_000).push(input);
  const r = new Resampler(48_000);
  const parts: number[] = [];
  for (let i = 0; i < input.length; i += 128) {
    parts.push(...r.push(input.subarray(i, i + 128)));
  }
  assert.equal(whole.length, 16_000);
  assert.equal(parts.length, 16_000);
  // Block boundaries must not change a single sample (no clicks).
  for (let i = 0; i < whole.length; i += 997) assert.ok(Math.abs(whole[i] - parts[i]) < 1e-6);
});

test("44.1k to 16k keeps the tone and the length (fractional ratio)", () => {
  const out = new Resampler(44_100).push(sine(44_100, 440, 1));
  assert.ok(Math.abs(out.length - 16_000) <= 1);
  const peak = Math.max(...out.map(Math.abs));
  assert.ok(peak > 0.9 && peak <= 1.0);
});

test("a tone above the new Nyquist is attenuated, not aliased at full volume", () => {
  const out = new Resampler(48_000).push(sine(48_000, 12_000, 0.5));
  const peak = Math.max(...out.map(Math.abs));
  assert.ok(peak < 0.9, `peak ${peak}`);
});

test("float to PCM16 clips instead of wrapping, and round-trips", () => {
  const pcm = floatToPcm16(Float32Array.from([0, 1, -1, 2, -2, 0.5]));
  assert.deepEqual(Array.from(pcm), [0, 32767, -32768, 32767, -32768, 16384]);
  const bytes = new Uint8Array(pcm.buffer);
  const back = pcm16ToFloat(bytes);
  assert.ok(Math.abs(back[5] - 0.5) < 1e-3);
  assert.equal(back[2], -1);
});

test("chunker emits fixed frames and keeps the remainder", () => {
  const c = new Chunker(4);
  assert.equal(c.push(Int16Array.from([1, 2, 3])).length, 0);
  const frames = c.push(Int16Array.from([4, 5, 6, 7, 8, 9]));
  assert.deepEqual(frames.map((f) => Array.from(f)), [[1, 2, 3, 4], [5, 6, 7, 8]]);
  assert.deepEqual(Array.from(c.push(Int16Array.from([10, 11, 12]))[0]), [9, 10, 11, 12]);
});

test("pcm reader carries an odd byte across frames", () => {
  const r = new Pcm16Reader();
  const full = new Uint8Array(floatToPcm16(Float32Array.from([0.25, -0.25])).buffer);
  const a = r.push(full.subarray(0, 3));
  const b = r.push(full.subarray(3));
  assert.equal(a.length, 1);
  assert.equal(b.length, 1);
  assert.ok(Math.abs(a[0] - 0.25) < 1e-3 && Math.abs(b[0] + 0.25) < 1e-3);
});
