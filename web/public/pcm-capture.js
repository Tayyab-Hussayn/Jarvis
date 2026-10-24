// AudioWorklet: hands the microphone's float blocks to the page, which does the
// resampling and framing (web/lib/pcm.ts) where it can be tested. A worklet
// block is only 128 samples (~2.7 ms); posting each one would flood the main
// thread, so they are gathered into ~21 ms batches first.
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.batch = new Float32Array(1024);
    this.filled = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    let offset = 0;
    while (offset < channel.length) {
      const room = this.batch.length - this.filled;
      const take = Math.min(room, channel.length - offset);
      this.batch.set(channel.subarray(offset, offset + take), this.filled);
      this.filled += take;
      offset += take;
      if (this.filled === this.batch.length) {
        // Copy, then reuse the buffer: a transferred one would be reallocated
        // on every batch.
        this.port.postMessage(this.batch.slice());
        this.filled = 0;
      }
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCapture);
