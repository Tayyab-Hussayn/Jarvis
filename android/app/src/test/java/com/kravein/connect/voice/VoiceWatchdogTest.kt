package com.kravein.connect.voice

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/** The silent stall (#115): a session must never keep saying "Listening" while nothing listens. */
class VoiceWatchdogTest {
    private val limits = VoiceWatchdog.Limits(captureStallMs = 5_000, silencedMs = 10_000, unansweredSpeechMs = 30_000)

    private fun dog(now: Long = 0) = VoiceWatchdog(limits).also { it.start(now) }

    private fun failed(v: VoiceWatchdog.Verdict) = (v as? VoiceWatchdog.Verdict.Failed)?.reason

    @Test fun aHealthySessionStaysOk() {
        val w = dog()
        for (i in 1..200) {
            w.onMicFrame(now = i * 100L, speech = false)
            assertEquals(VoiceWatchdog.Verdict.Ok, w.check(i * 100L))
        }
    }

    @Test fun aMicThatStopsDeliveringFramesEndsTheSession() {
        val w = dog()
        w.onMicFrame(1_000, false)
        assertEquals(VoiceWatchdog.Verdict.Ok, w.check(5_999))
        assertEquals(VoiceWatchdog.Reason.CAPTURE_STALLED, failed(w.check(6_000)))
    }

    @Test fun aSessionThatNeverGotAFrameFailsFromItsStart() {
        // Covers a capture thread that died before its first read.
        assertEquals(VoiceWatchdog.Reason.CAPTURE_STALLED, failed(dog(now = 100).check(5_100)))
    }

    @Test fun aSilencedMicEndsTheSessionAfterTheLimit() {
        val w = dog()
        w.onMicFrame(1_000, false)
        w.onMicSilenced(1_000, true)
        w.onMicFrame(10_900, false)
        assertEquals(VoiceWatchdog.Verdict.Ok, w.check(10_900))
        w.onMicFrame(11_000, false)
        assertEquals(VoiceWatchdog.Reason.MIC_SILENCED, failed(w.check(11_000)))
    }

    @Test fun aMicThatComesBackFromSilenceResetsTheClock() {
        val w = dog()
        w.onMicSilenced(0, true)
        w.onMicFrame(9_000, false)
        w.onMicSilenced(9_000, false)
        w.onMicFrame(30_000, false)
        assertEquals(VoiceWatchdog.Verdict.Ok, w.check(30_000))
    }

    @Test fun repeatedSilencedReportsDoNotRestartTheClock() {
        val w = dog()
        w.onMicSilenced(0, true)
        w.onMicSilenced(8_000, true)
        w.onMicFrame(10_000, false)
        assertEquals(VoiceWatchdog.Reason.MIC_SILENCED, failed(w.check(10_000)))
    }

    @Test fun speechWithNothingComingBackEndsTheSession() {
        val w = dog()
        var now = 0L
        repeat(299) { now += 100; w.onMicFrame(now, speech = true) }
        assertEquals(VoiceWatchdog.Verdict.Ok, w.check(now))
        now += 100
        w.onMicFrame(now, speech = true)
        assertEquals(VoiceWatchdog.Reason.NOT_RESPONDING, failed(w.check(now)))
    }

    @Test fun anyServerActivityMeansTheSessionIsAlive() {
        val w = dog()
        var now = 0L
        repeat(250) { now += 100; w.onMicFrame(now, speech = true) }
        w.onServerActivity()
        repeat(250) { now += 100; w.onMicFrame(now, speech = true) }
        assertEquals(VoiceWatchdog.Verdict.Ok, w.check(now))
    }

    @Test fun mutedSilenceNeverLooksLikeAHungModel() {
        val w = dog()
        var now = 0L
        repeat(100_000) { now += 100; w.onMicFrame(now, speech = false) }
        assertEquals(VoiceWatchdog.Verdict.Ok, w.check(now))
    }

    @Test fun startingAgainForgetsTheLastSessionsTrouble() {
        val w = dog()
        w.onMicSilenced(0, true)
        var now = 0L
        repeat(400) { now += 100; w.onMicFrame(now, speech = true) }
        w.start(now)
        w.onMicFrame(now + 100, false)
        assertEquals(VoiceWatchdog.Verdict.Ok, w.check(now + 100))
    }

    @Test fun everyReasonTellsTheUserWhatToDo() {
        for (r in VoiceWatchdog.Reason.entries) assertTrue(r.note.startsWith("Voice stopped") && "restart" in r.note, r.name)
    }

    // ---- level detection ----

    private fun pcm(vararg samples: Int): ByteArray = ByteArray(samples.size * 2).also { b ->
        samples.forEachIndexed { i, s -> b[2 * i] = (s and 0xFF).toByte(); b[2 * i + 1] = ((s shr 8) and 0xFF).toByte() }
    }

    @Test fun silenceIsNotSpeech() {
        val silent = ByteArray(3_200)
        assertEquals(0, PcmLevel.rms(silent, silent.size))
        assertFalse(PcmLevel.isSpeech(silent, silent.size))
    }

    @Test fun aLoudFrameIsSpeechAndANegativeSampleCountsTheSame() {
        val loud = pcm(*IntArray(160) { if (it % 2 == 0) 5_000 else -5_000 })
        assertEquals(5_000, PcmLevel.rms(loud, loud.size))
        assertTrue(PcmLevel.isSpeech(loud, loud.size))
    }

    @Test fun aQuietRoomIsBelowTheSpeechLine() {
        val hiss = pcm(*IntArray(160) { if (it % 2 == 0) 300 else -300 })
        assertFalse(PcmLevel.isSpeech(hiss, hiss.size))
    }

    @Test fun onlyTheReadLengthCounts() {
        val buffer = pcm(*IntArray(8) { 4_000 }) + ByteArray(100) // stale tail past the bytes actually read
        assertEquals(4_000, PcmLevel.rms(buffer, 16))
        assertEquals(0, PcmLevel.rms(buffer, 1))
    }
}
