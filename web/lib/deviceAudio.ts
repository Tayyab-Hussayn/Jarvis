"use client";

/**
 * This device's microphone and speaker as the live-voice audio path.
 *
 * Browser glue around the tested pieces in `pcm.ts` and `playback.ts`: it only
 * owns the Web Audio objects and their lifetime. `stop()` is the one place the
 * microphone is released, and it is idempotent, because it is reached from the
 * stop button, a closed socket, a hidden page and a server `stopped` frame.
 */

import { Chunker, Pcm16Reader, RECEIVE_RATE, Resampler, floatToPcm16 } from "./pcm";
import { PlaybackQueue, type PlaybackContext, type PlaybackSource } from "./playback";

/** Frames queued in the socket beyond this are dropped rather than sent: on a
 *  slow link, late microphone audio is worse than missing audio. */
const MAX_BUFFERED_BYTES = 256 * 1024;

/** Why this browser cannot capture, or "" when it can. `getUserMedia` exists
 *  only in a secure context (https, or 127.0.0.1/localhost). */
export function deviceAudioUnsupported(): string {
  if (typeof window === "undefined") return "";
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    return "This page cannot use the microphone (it needs https or localhost).";
  }
  if (typeof AudioWorkletNode === "undefined") {
    return "This browser has no AudioWorklet support.";
  }
  return "";
}

export class DeviceAudio {
  private stopped = false;
  private readonly playback: PlaybackQueue;
  private readonly reader = new Pcm16Reader();

  private constructor(
    private readonly ctx: AudioContext,
    private readonly stream: MediaStream,
    private readonly node: AudioWorkletNode,
  ) {
    this.playback = new PlaybackQueue(contextAdapter(ctx), RECEIVE_RATE);
  }

  /**
   * Ask for the microphone and start capturing. `send` receives ~100 ms PCM16
   * frames and is only called while the socket can take them. Rejects (with the
   * browser's reason) when permission is refused, leaving nothing open.
   */
  static async start(send: (frame: ArrayBuffer) => void, bufferedAmount: () => number): Promise<DeviceAudio> {
    const reason = deviceAudioUnsupported();
    if (reason) throw new Error(reason);

    // Created before any await: browsers only let a context start inside the
    // user gesture that began this, and getUserMedia would use it up.
    const ctx = new AudioContext();
    let stream: MediaStream | null = null;
    try {
      await ctx.resume();
      stream = await navigator.mediaDevices.getUserMedia({
        // Without echo cancellation the phone's speaker feeds the model its own
        // voice, which it takes for the user talking and barges in on itself.
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      await ctx.audioWorklet.addModule("/pcm-capture.js");
      const source = ctx.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(ctx, "pcm-capture");
      const resampler = new Resampler(ctx.sampleRate);
      const chunker = new Chunker();
      node.port.onmessage = (event: MessageEvent<Float32Array>) => {
        const frames = chunker.push(floatToPcm16(resampler.push(event.data)));
        for (const frame of frames) {
          if (bufferedAmount() > MAX_BUFFERED_BYTES) continue;
          send(frame.buffer as ArrayBuffer);
        }
      };
      source.connect(node);
      // A worklet that goes nowhere may never be pulled; a silent sink keeps
      // it running without playing the microphone back.
      const mute = ctx.createGain();
      mute.gain.value = 0;
      node.connect(mute).connect(ctx.destination);
      return new DeviceAudio(ctx, stream, node);
    } catch (error) {
      stream?.getTracks().forEach((track) => track.stop());
      void ctx.close().catch(() => undefined);
      throw error instanceof Error ? error : new Error(String(error));
    }
  }

  /** A binary frame from the server: PCM16 mono, 24 kHz. */
  play(bytes: ArrayBuffer): void {
    if (this.stopped) return;
    this.playback.push(this.reader.push(new Uint8Array(bytes)));
  }

  /** Barge-in: drop what is queued. */
  clear(): void {
    this.playback.clear();
  }

  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    this.playback.clear();
    this.node.port.onmessage = null;
    this.stream.getTracks().forEach((track) => track.stop());
    void this.ctx.close().catch(() => undefined);
  }
}

function contextAdapter(ctx: AudioContext): PlaybackContext {
  return {
    get currentTime() {
      return ctx.currentTime;
    },
    createSource(samples: Float32Array, rate: number): PlaybackSource {
      const buffer = ctx.createBuffer(1, samples.length, rate);
      buffer.copyToChannel(samples as Float32Array<ArrayBuffer>, 0);
      const node = ctx.createBufferSource();
      node.buffer = buffer;
      node.connect(ctx.destination);
      return {
        start: (when) => node.start(when),
        stop: () => node.stop(),
        get onended() {
          return node.onended as (() => void) | null;
        },
        set onended(fn: (() => void) | null) {
          node.onended = fn;
        },
      };
    },
  };
}
