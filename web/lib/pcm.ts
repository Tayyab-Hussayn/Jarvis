// Microphone audio for device voice: float blocks from the browser at whatever
// rate the AudioContext runs (44.1/48 kHz) become PCM16 mono at the 16 kHz the
// server expects. Pure functions and small classes, so they run under node --test.

export const SEND_RATE = 16_000;
export const RECEIVE_RATE = 24_000;
/** ~100 ms per websocket frame: small enough for latency, large enough that the
 *  frame rate does not flood a phone's radio. */
export const FRAME_SAMPLES = 1_600;

/**
 * Streaming downsampler (box filter over each output sample's input span).
 * Averaging the span is a cheap low-pass: plain decimation would alias speech
 * sibilants into the band the recogniser listens to. State carries between
 * calls, so block boundaries (128 samples in a worklet) leave no click.
 */
export class Resampler {
  private readonly step: number;
  private pending: number[] = [];
  /** Position of the next output sample, in input samples from `pending[0]`. */
  private pos = 0;

  constructor(inRate: number, outRate: number = SEND_RATE) {
    if (!(inRate > 0) || !(outRate > 0)) throw new Error("rates must be positive");
    this.step = inRate / outRate;
  }

  push(input: Float32Array): Float32Array {
    for (let i = 0; i < input.length; i += 1) this.pending.push(input[i]);
    const out: number[] = [];
    // An output sample needs its whole span [pos, pos + step) to have arrived.
    while (this.pos + this.step <= this.pending.length) {
      const start = this.pos;
      const end = this.pos + this.step;
      let sum = 0;
      let weight = 0;
      for (let i = Math.floor(start); i < Math.ceil(end); i += 1) {
        const w = Math.min(i + 1, end) - Math.max(i, start);
        sum += this.pending[i] * w;
        weight += w;
      }
      out.push(weight > 0 ? sum / weight : 0);
      this.pos = end;
    }
    // Drop what is fully consumed; keep the fractional remainder.
    const drop = Math.floor(this.pos);
    if (drop > 0) {
      this.pending = this.pending.slice(drop);
      this.pos -= drop;
    }
    return Float32Array.from(out);
  }
}

/** Float [-1, 1] to little-endian PCM16, clipped rather than wrapped. */
export function floatToPcm16(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i += 1) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    out[i] = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff);
  }
  return out;
}

/** Little-endian PCM16 bytes to float, for playback. An odd trailing byte is
 *  ignored here; `Pcm16Reader` carries it. */
export function pcm16ToFloat(bytes: Uint8Array): Float32Array {
  const n = bytes.length >> 1;
  const out = new Float32Array(n);
  for (let i = 0; i < n; i += 1) {
    const v = bytes[2 * i] | (bytes[2 * i + 1] << 8);
    out[i] = (v >= 0x8000 ? v - 0x10000 : v) / 0x8000;
  }
  return out;
}

/** Re-chunks a sample stream into fixed-size frames. */
export class Chunker {
  private buf: number[] = [];
  private readonly size: number;
  constructor(size: number = FRAME_SAMPLES) {
    this.size = size;
  }

  push(samples: Int16Array): Int16Array[] {
    for (let i = 0; i < samples.length; i += 1) this.buf.push(samples[i]);
    const frames: Int16Array[] = [];
    while (this.buf.length >= this.size) {
      frames.push(Int16Array.from(this.buf.splice(0, this.size)));
    }
    return frames;
  }
}

/** Binary frames can split a sample; keep the odd byte for the next frame so
 *  playback never gets a click from half a sample. */
export class Pcm16Reader {
  private carry: number | null = null;

  push(bytes: Uint8Array): Float32Array {
    let data = bytes;
    if (this.carry !== null) {
      const joined = new Uint8Array(bytes.length + 1);
      joined[0] = this.carry;
      joined.set(bytes, 1);
      data = joined;
      this.carry = null;
    }
    if (data.length % 2) {
      this.carry = data[data.length - 1];
      data = data.subarray(0, data.length - 1);
    }
    return pcm16ToFloat(data);
  }
}
