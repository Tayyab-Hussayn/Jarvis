package com.kravein.connect.chat

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class SessionResumeTest {
    @Test fun firstConnectionAsksForNothing() {
        assertNull(SessionResume.resumeParam(MemorySessionIds(), "dev"))
    }

    @Test fun theIdFromHelloIsRememberedAndAskedForNextTime() {
        val store = MemorySessionIds()
        SessionResume.onHello(store, "dev", ServerFrame.Hello("K", "m", sessionId = "abc", resumed = false))
        assertEquals("abc", SessionResume.resumeParam(store, "dev"))
    }

    @Test fun aRefusedResumeReplacesTheOldIdWithTheNewOne() {
        val store = MemorySessionIds().apply { put("dev", "old") }
        SessionResume.onHello(store, "dev", ServerFrame.Hello("K", "m", sessionId = "fresh", resumed = false))
        assertEquals("fresh", SessionResume.resumeParam(store, "dev"))
    }

    @Test fun aHelloWithoutAnIdKeepsWhatWeHave() {
        val store = MemorySessionIds().apply { put("dev", "keep") }
        SessionResume.onHello(store, "dev", ServerFrame.Hello("K", "m"))
        assertEquals("keep", SessionResume.resumeParam(store, "dev"))
    }

    @Test fun idsBelongToTheDeviceThatPaired() {
        val store = MemorySessionIds().apply { put("a", "one") }
        assertNull(SessionResume.resumeParam(store, "b"))
        store.clear("a")
        assertNull(SessionResume.resumeParam(store, "a"))
    }

    @Test fun anOversizedStoredIdIsNeverSent() {
        val store = MemorySessionIds().apply { put("dev", "x".repeat(ChatFrames.MAX_SESSION_ID + 1)) }
        assertNull(SessionResume.resumeParam(store, "dev"))
    }

    @Test fun aReplacedSocketDoesNotRetry() {
        assertFalse(ChatRetry.retriesAfter(ChatRetry.CLOSE_REPLACED))
        assertTrue(ChatRetry.retriesAfter(1000))
        assertTrue(ChatRetry.retriesAfter(ChatRetry.CLOSE_POLICY_VIOLATION))
        assertTrue(ChatRetry.retriesAfter(0))
    }

    @Test fun theSocketStaysOpenForVisibleAppOrLiveVoiceButNeverUnpaired() {
        assertTrue(LinkPolicy.shouldBeOpen(paired = true, appVisible = true, voiceHolds = false))
        assertTrue(LinkPolicy.shouldBeOpen(paired = true, appVisible = false, voiceHolds = true))
        assertFalse(LinkPolicy.shouldBeOpen(paired = true, appVisible = false, voiceHolds = false))
        assertFalse(LinkPolicy.shouldBeOpen(paired = false, appVisible = true, voiceHolds = true))
    }
}
