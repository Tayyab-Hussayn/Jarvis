package com.kravein.connect.voice

import android.annotation.SuppressLint
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AudioEffect
import android.media.audiofx.NoiseSuppressor
import android.os.SystemClock
import android.util.Log
import com.kravein.connect.BuildConfig
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
 * The session's health is not judged here: this class only reports what it sees ([onMic] per frame) and keeps the two
 * threads from dying quietly; [VoiceWatchdog] turns a dead microphone into a visible end of the session.
 */
class VoiceAudio(
    /** Sends one frame; false = dropped (socket gone or backed up). */
    private val send: (ByteArray, Int) -> Boolean,
    /** Whether frames should leave at all (the session must be live) and whether the user muted. */
    private val live: () -> Boolean,
    private val muted: () -> Boolean,
    /** One frame was captured; [speech] is whether it was loud enough to be someone talking (never true while muted). */
    private val onMic: (speech: Boolean) -> Unit,
) {
    @Volatile private var stopped = false
    private var record: AudioRecord? = null
    @Volatile private var track: AudioTrack? = null
    private var micThread: Thread? = null
    private var playThread: Thread? = null
    private val playQueue = ArrayBlockingQueue<ByteArray>(MAX_QUEUED_CHUNKS)
    private val effects = ArrayList<AudioEffect>()

    // Counters for the debug log only (no audio content, no secrets).
    @Volatile private var framesRead = 0L
    @Volatile private var framesSent = 0L
    @Volatile private var readErrors = 0L
    @Volatile private var chunksIn = 0L
    @Volatile private var bytesIn = 0L
    @Volatile private var writeErrors = 0L
    @Volatile private var clears = 0L
    private val echo = EchoGate()

    /** The recording session id, so the service can ask whether Android silenced this capture. */
    val sessionId: Int get() = record?.audioSessionId ?: 0

    @SuppressLint("MissingPermission") // checked by the service before it starts the foreground type
    fun start() {
        record = openRecord()
        track = buildTrack().also { it.play() }
        record!!.startRecording()
        micThread = Thread(::captureLoop, "kravein-voice-mic").apply { isDaemon = true; start() }
        playThread = Thread(::playLoop, "kravein-voice-play").apply { isDaemon = true; start() }
    }

    @SuppressLint("MissingPermission")
    private fun openRecord(): AudioRecord {
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
        return rec
    }

    private fun buildTrack(): AudioTrack {
        val minTrack = AudioTrack.getMinBufferSize(RECEIVE_RATE, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
        return AudioTrack.Builder()
            // USAGE_MEDIA, not USAGE_ASSISTANT: the assistant stream has its own volume, which was 0 on the speaker of
            // the test phone and cannot be raised with the media keys, so every reply played in perfect silence.
            // Media also follows the volume the user already set, and plays on the loudspeaker (headset/BT when on).
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
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
            // ~0.3 s: enough to ride out network jitter, short enough that barge-in is not heard talking on.
            .setBufferSizeInBytes(maxOf(minTrack, RECEIVE_RATE * 2 * 3 / 10))
            .build()
    }

    /** A chunk of model audio (PCM16 mono 24 kHz). Drops the oldest queued audio when the speaker falls behind. */
    fun play(pcm: ByteArray) {
        if (stopped) return
        chunksIn++
        bytesIn += pcm.size
        echo.onPlayed(SystemClock.elapsedRealtime(), pcm.size)
        while (!playQueue.offer(pcm)) playQueue.poll()
    }

    /** Barge-in: the user talked over the model, what is queued is stale. Applied by the playback thread itself. */
    fun clear() {
        clears++
        echo.onCleared(SystemClock.elapsedRealtime())
        playQueue.clear()
        // Flushing from this thread while the playback thread sits in a blocking write is a race; the marker makes the
        // playback thread do it between writes.
        while (!playQueue.offer(CLEAR)) playQueue.poll()
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
        var rec = record ?: return
        val frame = ByteArray(FRAME_BYTES)
        var failures = 0
        var restarts = 0
        while (!stopped) {
            val n = rec.read(frame, 0, frame.size)
            if (n <= 0) {
                if (stopped) return
                readErrors++
                failures++
                // A negative read means the input died under us (dead object, invalid operation). Try to bring it back a
                // couple of times; past that, stop pretending: no frames reach the watchdog, which ends the session
                // visibly instead of leaving "Listening" on a microphone that is gone.
                if (failures >= 3 && restarts < MAX_CAPTURE_RESTARTS) {
                    restarts++
                    failures = 0
                    try {
                        rec.stop(); rec.release()
                        rec = openRecord().also { it.startRecording() }
                        record = rec
                    } catch (e: Exception) {
                        Log.w(TAG, "capture restart failed: ${e.javaClass.simpleName}")
                    }
                }
                try { Thread.sleep(50) } catch (_: InterruptedException) { return }
                continue
            }
            failures = 0
            framesRead++
            // Silence while the user muted, and while the model's own voice is coming out of the speaker (see EchoGate).
            val silent = muted() || echo.isModelSpeaking(SystemClock.elapsedRealtime())
            onMic(!silent && PcmLevel.isSpeech(frame, n))
            if (live() && send(VoiceView.frameFor(frame, n, silent), n)) framesSent++
            if (BuildConfig.DEBUG && framesRead % 50 == 0L) logStats()
        }
    }

    private fun playLoop() {
        while (!stopped) {
            val chunk = try {
                playQueue.poll(200, TimeUnit.MILLISECONDS)
            } catch (_: InterruptedException) {
                return
            } ?: continue
            val out = track ?: return
            try {
                if (chunk === CLEAR) {
                    out.pause()
                    out.flush()
                    out.play()
                    continue
                }
                val written = out.write(chunk, 0, chunk.size)
                if (written < 0) {
                    writeErrors++
                    // A dead track (audio server restarted, route change): replace it instead of going mute for good.
                    if (written == AudioTrack.ERROR_DEAD_OBJECT) {
                        out.release()
                        track = buildTrack().also { it.play() }
                    }
                }
            } catch (_: IllegalStateException) {
                return
            }
        }
    }

    private fun logStats() {
        val t = track
        Log.i(
            TAG,
            "mic read=$framesRead sent=$framesSent readErr=$readErrors | speaker chunks=$chunksIn bytes=$bytesIn " +
                "writeErr=$writeErrors clears=$clears underruns=${t?.underrunCount ?: -1} queued=${playQueue.size}",
        )
    }

    companion object {
        private const val TAG = "KraveinVoice"
        const val SEND_RATE = 16_000
        const val RECEIVE_RATE = 24_000

        /** 100 ms of 16 kHz mono PCM16, the frame size the web client sends. */
        const val FRAME_BYTES = 3_200

        /** About 6 s of 100 ms chunks; past it the oldest audio is dropped instead of lagging further behind. */
        const val MAX_QUEUED_CHUNKS = 64

        private const val MAX_CAPTURE_RESTARTS = 2

        /** Marks "flush what is playing" in the queue; compared by identity, never played. */
        private val CLEAR = ByteArray(0)
    }
}
