package com.kravein.connect.voice

/**
 * Keeps the phone's own speaker out of its own microphone.
 *
 * A phone loudspeaker sits a few centimetres from the microphone, and the platform echo canceller only takes part of it
 * out. The server hears the rest as the user talking over the model, treats it as a barge-in and sends `audio_clear`:
 * the model's answer is cut off after a second or two (the text carries on, the voice does not). A browser avoids this
 * with its own echo cancellation; on the phone the microphone is simply sent as silence while the model is speaking,
 * plus a short tail for the room's reverb. Talking over the model by voice is traded for answers that are heard whole;
 * Mute and End stay on the screen and the notification.
 *
 * Time is milliseconds on a monotonic clock, passed in so this is testable.
 */
class EchoGate(private val tailMs: Long = TAIL_MS) {
    private var playingUntil = 0L

    /** [bytes] of PCM16 mono 24 kHz were queued for the speaker. They play back to back after what is already queued. */
    @Synchronized
    fun onPlayed(now: Long, bytes: Int) {
        playingUntil = maxOf(playingUntil, now) + bytes / BYTES_PER_MS
    }

    /** What was queued was dropped (a barge-in): the speaker goes quiet now. */
    @Synchronized
    fun onCleared(now: Long) {
        playingUntil = now
    }

    /** True while the model's voice is coming out of the speaker, or just stopped. The microphone is sent as silence. */
    @Synchronized
    fun isModelSpeaking(now: Long): Boolean = now < playingUntil + tailMs

    companion object {
        /** 24 000 samples/s * 2 bytes. */
        const val BYTES_PER_MS = 48
        const val TAIL_MS = 300L
    }
}
