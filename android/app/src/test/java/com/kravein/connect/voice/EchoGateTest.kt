package com.kravein.connect.voice

import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class EchoGateTest {
    private val gate = EchoGate(tailMs = 300)

    // 4 800 bytes of 24 kHz PCM16 mono = 100 ms of speech.
    private val chunk = 4_800

    @Test fun nothingPlayedMeansTheMicrophoneIsOpen() {
        assertFalse(gate.isModelSpeaking(now = 1_000))
    }

    @Test fun theMicrophoneIsClosedForTheLengthOfTheQueuedSpeechPlusTheTail() {
        gate.onPlayed(1_000, chunk)
        assertTrue(gate.isModelSpeaking(1_000))
        assertTrue(gate.isModelSpeaking(1_399))
        assertFalse(gate.isModelSpeaking(1_400))
    }

    @Test fun chunksThatArriveFasterThanRealtimeAddUpInsteadOfOverlapping() {
        // Ten 100 ms chunks arrive in the same instant: 1 s of speech to play.
        repeat(10) { gate.onPlayed(1_000, chunk) }
        assertTrue(gate.isModelSpeaking(2_299))
        assertFalse(gate.isModelSpeaking(2_300))
    }

    @Test fun aGapBetweenAnswersReopensTheMicrophone() {
        gate.onPlayed(1_000, chunk)
        assertFalse(gate.isModelSpeaking(5_000))
        gate.onPlayed(5_000, chunk)
        assertTrue(gate.isModelSpeaking(5_050))
    }

    @Test fun aBargeInFlushReopensTheMicrophoneAfterTheTailOnly() {
        repeat(10) { gate.onPlayed(1_000, chunk) }
        gate.onCleared(1_500)
        assertTrue(gate.isModelSpeaking(1_799))
        assertFalse(gate.isModelSpeaking(1_800))
    }
}
