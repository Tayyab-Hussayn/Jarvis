package com.kravein.connect.voice

import android.annotation.SuppressLint
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.AudioTrack
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * This phone's microphone and speaker as the live-voice audio path (what `web/lib/deviceAudio.ts` is in a browser).
 *
 * Capture is PCM16 mono 16 kHz in 100 ms frames, which the server takes as-is; playback is PCM16 mono 24 kHz, which is
 * what the server sends. Neither side needs a resampler. [stop] is the one place the microphone is released and it is
 * idempotent, because it is reached from the user's stop, a server `stopped`, a lost link and the service's
 * `onDestroy`.
 *
 * [permissionHeld] is checked by the caller; [start] throws [SecurityException] if the platform still refuses.
 */
class VoiceAudio(
    /** Sends one frame; false = dropped (socket gone or backed up). */
    private val send: (ByteArray, Int) -> Boolean,
    /** Whether frames should leave at all (the session must be live) and whether the user muted. */
    private val live: () -> Boolean,
    private val muted: () -> Boolean,
) {
    @Volatile private var stopped = false
    private var record: AudioRecord? = null
    private var track: AudioTrack? = null
    private var micThread: Thread? = null
    private var playThread: Thread? = null
    private val playQueue = ArrayBlockingQueue<ByteArray>(MAX_QUEUED_CHUNKS)
    private val effects = ArrayList<android.media.audiofx.AudioEffect>()

    @SuppressLint("MissingPermission") // checked by the service before it starts the foreground type
    fun start() {
        val minRecord = AudioRecord.getMinBufferSize(SEND_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        check(minRecord > 0) { "No audio input on this device." }
        val rec = AudioRecord(
            // Platform echo handling is tuned for this source; without it the speaker feeds the model its own voice and
            // it barges in on itself.
            MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            SEND_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minRecord, FRAME_BYTES * 4),
        )
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release()
            error("The microphone could not be opened.")
        }
        // Some devices already apply these for VOICE_COMMUNICATION; asking again is harmless where they exist.
        if (AcousticEchoCanceler.isAvailable()) AcousticEchoCanceler.create(rec.audioSessionId)?.also { it.enabled = true; effects += it }
        if (NoiseSuppressor.isAvailable()) NoiseSuppressor.create(rec.audioSessionId)?.also { it.enabled = true; effects += it }

        val minTrack = AudioTrack.getMinBufferSize(RECEIVE_RATE, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val out = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(RECEIVE_RATE)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build(),
            )
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setBufferSizeInBytes(maxOf(minTrack, RECEIVE_RATE)) // ~0.5 s of 16-bit mono
            .build()
        record = rec
        track = out
        out.play()
        rec.startRecording()

        micThread = Thread(::captureLoop, "kravein-voice-mic").apply { isDaemon = true; start() }
        playThread = Thread(::playLoop, "kravein-voice-play").apply { isDaemon = true; start() }
    }

    /** A chunk of model audio (PCM16 mono 24 kHz). Drops the oldest queued audio when the speaker falls behind. */
    fun play(pcm: ByteArray) {
        if (stopped) return
        while (!playQueue.offer(pcm)) playQueue.poll()
    }

    /** Barge-in: the user talked over the model, what is queued is stale. */
    fun clear() {
        playQueue.clear()
        val t = track ?: return
        try {
            t.pause()
            t.flush()
            t.play()
        } catch (_: IllegalStateException) {
            // Released under us by stop(); nothing left to clear.
        }
    }

    fun stop() {
        if (stopped) return
        stopped = true
        playQueue.clear()
        micThread?.interrupt()
        playThread?.interrupt()
        // Stop before release: release alone can leave the capture indicator on until the thread notices.
        try { record?.stop() } catch (_: IllegalStateException) { }
        record?.release()
        try { track?.pause(); track?.flush() } catch (_: IllegalStateException) { }
        track?.release()
        effects.forEach { it.release() }
        effects.clear()
        record = null
        track = null
    }

    private fun captureLoop() {
        val rec = record ?: return
        val frame = ByteArray(FRAME_BYTES)
        while (!stopped) {
            val n = rec.read(frame, 0, frame.size)
            if (n <= 0) {
                if (stopped) return
                // A dead input (another app took it, a call) must not spin; the session ends via the server or the user.
                try { Thread.sleep(50) } catch (_: InterruptedException) { return }
                continue
            }
            if (!live()) continue
            send(VoiceView.frameFor(frame, n, muted()), n)
        }
    }

    private fun playLoop() {
        val out = track ?: return
        while (!stopped) {
            val chunk = try {
                playQueue.poll(200, TimeUnit.MILLISECONDS)
            } catch (_: InterruptedException) {
                return
            } ?: continue
            try {
                out.write(chunk, 0, chunk.size)
            } catch (_: IllegalStateException) {
                return
            }
        }
    }

    companion object {
        const val SEND_RATE = 16_000
        const val RECEIVE_RATE = 24_000

        /** 100 ms of 16 kHz mono PCM16, the frame size the web client sends. */
        const val FRAME_BYTES = 3_200

        /** About 6 s of 100 ms chunks; past it the oldest audio is dropped instead of lagging further behind. */
        const val MAX_QUEUED_CHUNKS = 64
    }
}
