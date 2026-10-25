package com.kravein.connect.voice

/** How loud a 16-bit little-endian PCM frame is. Pure, so "is anyone speaking" is testable without a microphone. */
object PcmLevel {
    /** Roughly -30 dBFS: a person talking near the phone, not a fan or a room. */
    const val SPEECH_RMS = 1_000

    fun rms(pcm: ByteArray, length: Int): Int {
        val samples = length / 2
        if (samples == 0) return 0
        var sum = 0.0
        for (i in 0 until samples) {
            val v = ((pcm[2 * i + 1].toInt() shl 8) or (pcm[2 * i].toInt() and 0xFF)).toShort().toInt()
            sum += v.toDouble() * v
        }
        return Math.sqrt(sum / samples).toInt()
    }

    fun isSpeech(pcm: ByteArray, length: Int): Boolean = rms(pcm, length) >= SPEECH_RMS
}

/**
 * Why a voice session that still says "Listening" is not listening. The notification, the strip and the orb must
 * always match reality, so each of these ends the session visibly (and releases the microphone) instead of leaving a
 * stale "Listening" behind:
 *
 * - [Reason.CAPTURE_STALLED]: no microphone frame for [Limits.captureStallMs] (a dead input, a hung audio stack).
 * - [Reason.MIC_SILENCED]: Android reports our capture as silenced for [Limits.silencedMs] (another app, a call, an
 *   OEM background rule): frames still flow, but they are zeros.
 * - [Reason.NOT_RESPONDING]: [Limits.unansweredSpeechMs] of speech went by with nothing at all coming back — not a
 *   transcript, not audio. A live session that hears speech always reports something, so this is a hung model session.
 *   The limit is long on purpose: a noisy room must not end a working session.
 *
 * A dead socket is not decided here: `ChatClient` closes a link that has gone silent, and the resumed session either
 * continues or the replay ends voice with a reason.
 */
class VoiceWatchdog(private val limits: Limits = Limits()) {
    data class Limits(
        val captureStallMs: Long = 5_000,
        val silencedMs: Long = 10_000,
        val unansweredSpeechMs: Long = 30_000,
    )

    enum class Reason(val note: String) {
        CAPTURE_STALLED("Voice stopped: the microphone stopped working. Tap the mic to restart."),
        MIC_SILENCED("Voice stopped: Android is blocking the microphone. Tap the mic to restart."),
        NOT_RESPONDING("Voice stopped: Kravein is not responding. Tap the mic to restart."),
    }

    sealed interface Verdict {
        data object Ok : Verdict
        data class Failed(val reason: Reason) : Verdict
    }

    private var lastMicFrameAt = 0L
    private var silencedSince: Long? = null
    private var unansweredSpeechMs = 0L

    /** A session starts (or is picked up again after a resume): nothing is overdue yet. */
    @Synchronized
    fun start(now: Long) {
        lastMicFrameAt = now
        silencedSince = null
        unansweredSpeechMs = 0
    }

    /** One captured frame of [frameMs]. [speech] is false while muted: muted silence must never look like a hung model. */
    @Synchronized
    fun onMicFrame(now: Long, speech: Boolean, frameMs: Long = 100) {
        lastMicFrameAt = now
        if (speech) unansweredSpeechMs += frameMs
    }

    @Synchronized
    fun onMicSilenced(now: Long, silenced: Boolean) {
        if (!silenced) silencedSince = null else if (silencedSince == null) silencedSince = now
    }

    /** Anything from the voice side of the server: a `voice_state` frame or a chunk of model audio. */
    @Synchronized
    fun onServerActivity() {
        unansweredSpeechMs = 0
    }

    @Synchronized
    fun check(now: Long): Verdict {
        if (now - lastMicFrameAt >= limits.captureStallMs) return Verdict.Failed(Reason.CAPTURE_STALLED)
        val silenced = silencedSince
        if (silenced != null && now - silenced >= limits.silencedMs) return Verdict.Failed(Reason.MIC_SILENCED)
        if (unansweredSpeechMs >= limits.unansweredSpeechMs) return Verdict.Failed(Reason.NOT_RESPONDING)
        return Verdict.Ok
    }
}
