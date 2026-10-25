package com.kravein.connect.bubble

import com.kravein.connect.chat.ChatMessage
import com.kravein.connect.chat.VoicePhase
import com.kravein.connect.voice.VoiceView
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class BubblePolicyTest {
    @Test fun showsOnlyWhenEveryConditionHolds() {
        assertTrue(BubblePolicy.shouldShow(enabled = true, overlayGranted = true, voiceActive = true, appInFront = false))
        assertFalse(BubblePolicy.shouldShow(enabled = false, overlayGranted = true, voiceActive = true, appInFront = false))
        assertFalse(BubblePolicy.shouldShow(enabled = true, overlayGranted = false, voiceActive = true, appInFront = false))
        assertFalse(BubblePolicy.shouldShow(enabled = true, overlayGranted = true, voiceActive = false, appInFront = false))
        assertFalse(BubblePolicy.shouldShow(enabled = true, overlayGranted = true, voiceActive = true, appInFront = true))
    }

    @Test fun neverShowsWithVoiceOffWhateverElseIsOn() {
        for (enabled in listOf(true, false)) for (overlay in listOf(true, false)) for (front in listOf(true, false)) {
            assertFalse(BubblePolicy.shouldShow(enabled, overlay, voiceActive = false, appInFront = front))
        }
    }

    @Test fun snapsToTheNearerEdge() {
        assertEquals(0, BubbleGeometry.snapX(x = 100, bubbleWidth = 160, screenWidth = 1080))
        assertEquals(1080 - 160, BubbleGeometry.snapX(x = 700, bubbleWidth = 160, screenWidth = 1080))
        // The exact middle goes right, away from the system back-gesture edge being the same either way.
        assertEquals(1080 - 160 - 16, BubbleGeometry.snapX(x = 460, bubbleWidth = 160, screenWidth = 1080, margin = 16))
        assertEquals(16, BubbleGeometry.snapX(x = 0, bubbleWidth = 160, screenWidth = 1080, margin = 16))
    }

    @Test fun neverSnapsOffScreenOnATinyWindow() {
        assertEquals(0, BubbleGeometry.snapX(x = 50, bubbleWidth = 400, screenWidth = 300))
    }

    @Test fun clampsVerticallyBetweenTheBars() {
        assertEquals(80, BubbleGeometry.clampY(y = -50, bubbleHeight = 160, screenHeight = 2000, top = 80, bottom = 120))
        assertEquals(2000 - 120 - 160, BubbleGeometry.clampY(y = 5000, bubbleHeight = 160, screenHeight = 2000, top = 80, bottom = 120))
        assertEquals(500, BubbleGeometry.clampY(y = 500, bubbleHeight = 160, screenHeight = 2000, top = 80, bottom = 120))
    }

    @Test fun smallMovementIsATapNotADrag() {
        assertTrue(BubbleGeometry.isTap(3f, 4f, slop = 5f))
        assertFalse(BubbleGeometry.isTap(6f, 0f, slop = 5f))
    }

    @Test fun labelsFollowTheSessionWithMuteAndReconnectWinning() {
        assertEquals("Listening", VoiceView.label(VoicePhase.LISTENING, muted = false, reconnecting = false))
        assertEquals("Speaking", VoiceView.label(VoicePhase.SPEAKING, muted = false, reconnecting = false))
        assertEquals("Muted", VoiceView.label(VoicePhase.LISTENING, muted = true, reconnecting = false))
        assertEquals("Reconnecting…", VoiceView.label(VoicePhase.LISTENING, muted = true, reconnecting = true))
        assertEquals("Stopping…", VoiceView.label(VoicePhase.LISTENING, muted = false, reconnecting = false, stopping = true))
    }

    @Test fun lastLineIsTheNewestNonEmptyWordsOneLineLong() {
        val messages = listOf(
            ChatMessage.User("s1-user", "open youtube"),
            ChatMessage.Assistant("s1", text = "Opening it.\nSecond line", pending = false),
            ChatMessage.User("s2-user", ""),
            ChatMessage.Assistant("s2", text = "", pending = true),
        )
        assertEquals("Opening it.", VoiceView.lastLine(messages))
        assertEquals("", VoiceView.lastLine(emptyList()))
        assertEquals(10, VoiceView.lastLine(listOf(ChatMessage.User("u", "x".repeat(50))), max = 10).length)
    }
}
