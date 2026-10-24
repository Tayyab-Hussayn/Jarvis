// Gapless playback of the model's audio, with barge-in.
//
// Chunks arrive faster than real time, so each is scheduled to start exactly
// where the previous one ends. `clear()` stops everything scheduled and
// forgets the timeline: that is what the server's `audio_clear` means (the
// user talked over the model), and audio that keeps playing after it would be
// the model talking over the user.

export interface PlaybackSource {
  start(when: number): void;
  stop(): void;
  onended: (() => void) | null;
}

export interface PlaybackContext {
  readonly currentTime: number;
  /** Build a source for `samples` at `rate` Hz (the context resamples). */
  createSource(samples: Float32Array, rate: number): PlaybackSource;
}

/** Audio is held back this long before the first chunk plays, so a late
 *  network frame does not cut a sentence into stutters. */
export const LEAD_SECONDS = 0.06;

export class PlaybackQueue {
  private next = 0;
  private active = new Set<PlaybackSource>();

  private readonly ctx: PlaybackContext;
  private readonly rate: number;

  constructor(ctx: PlaybackContext, rate: number) {
    this.ctx = ctx;
    this.rate = rate;
  }

  /** Seconds of audio scheduled and not yet played. */
  get queuedSeconds(): number {
    return Math.max(0, this.next - this.ctx.currentTime);
  }

  get playing(): boolean {
    return this.active.size > 0;
  }

  push(samples: Float32Array): void {
    if (samples.length === 0) return;
    const now = this.ctx.currentTime;
    // Underrun (or first chunk): restart the timeline a little ahead of now.
    const when = this.next > now ? this.next : now + LEAD_SECONDS;
    const source = this.ctx.createSource(samples, this.rate);
    source.onended = () => {
      this.active.delete(source);
    };
    this.active.add(source);
    source.start(when);
    this.next = when + samples.length / this.rate;
  }

  clear(): void {
    for (const source of this.active) {
      source.onended = null;
      try {
        source.stop();
      } catch {
        // already ended
      }
    }
    this.active.clear();
    this.next = 0;
  }
}
