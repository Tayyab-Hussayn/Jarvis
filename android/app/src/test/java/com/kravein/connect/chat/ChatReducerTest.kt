package com.kravein.connect.chat

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class ChatReducerTest {
    private fun feed(vararg lines: String, from: ChatState = ChatState(), now: Long = 1_000): ChatState =
        lines.fold(from) { s, l -> ChatReducer.onFrame(s, ChatFrames.parse(l), now) }

    private fun turn(s: ChatState, id: String = "t1") = s.messages.filterIsInstance<ChatMessage.Assistant>().first { it.id == id }

    private val start = """{"t":"turn_start","id":"t1"}"""
    private val call = """{"t":"tool_call","id":"t1","call_id":"c1","name":"phone_status","args":{},"display":"phone_status()"}"""
    private val confirm = """{"t":"confirm_request","id":"t1","confirm_id":"k1","action":"Delete","detail":"x","danger":"DESTRUCTIVE","timeout":60}"""

    @Test fun fullTurn() {
        val s = feed(
            start,
            """{"t":"thinking","id":"t1","text":"hmm"}""",
            call,
            """{"t":"tool_result","id":"t1","call_id":"c1","name":"phone_status","result":"ok"}""",
            """{"t":"answer","id":"t1","text":"Done"}""",
            """{"t":"turn_end","id":"t1","iterations":2}""",
        )
        val m = turn(s)
        assertEquals("Done", m.text)
        assertEquals(listOf("hmm"), m.thinking)
        assertEquals(ToolStatus.DONE, m.tools.single().status)
        assertEquals("ok", m.tools.single().result)
        assertFalse(m.pending)
        assertFalse(s.busy)
    }

    @Test fun blankThinkingIgnored() {
        assertTrue(turn(feed(start, """{"t":"thinking","id":"t1","text":"  "}""")).thinking.isEmpty())
    }

    @Test fun cancelledTurnIsNotAnError() {
        val s = feed(start, """{"t":"turn_end","id":"t1","iterations":1,"cancelled":true,"error":"x"}""")
        assertTrue(turn(s).cancelled)
        assertEquals(null, turn(s).error)
    }

    @Test fun stopClearsOnCancelledFrameEvenWithNothingRunning() {
        var s = ChatReducer.onStopRequested(ChatState(busy = true), sent = true)
        assertTrue(s.cancelling)
        s = ChatReducer.onFrame(s, ServerFrame.Cancelled(null), 0)
        assertFalse(s.cancelling)
        // Not busy, or not sent: the flag is never set (it could not be cleared).
        assertFalse(ChatReducer.onStopRequested(ChatState(busy = false), true).cancelling)
        assertFalse(ChatReducer.onStopRequested(ChatState(busy = true), false).cancelling)
    }

    @Test fun disconnectTurnsRunningCardsIntoUnknownAndDeniesConfirms() {
        val s = ChatReducer.onDisconnected(feed(start, call, confirm))
        val m = turn(s)
        assertEquals(ToolStatus.UNKNOWN, m.tools.single().status)
        assertEquals(LOST_RESULT, m.tools.single().result)
        assertEquals(ConfirmStatus.DENIED, m.confirms.single().status)
        assertTrue(m.interrupted)
        assertFalse(m.pending)
        assertFalse(s.busy)
    }

    @Test fun disconnectLeavesSettledTurnsAlone() {
        val done = feed(start, call, """{"t":"tool_result","id":"t1","call_id":"c1","name":"n","result":"r"}""", """{"t":"turn_end","id":"t1","iterations":1}""")
        assertEquals(done.messages, ChatReducer.onDisconnected(done).messages)
    }

    @Test fun confirmResolvedElsewhereClosesCard() {
        val s = feed(start, confirm, """{"t":"confirm_resolved","id":"t1","confirm_id":"k1","outcome":"denied","by":"phone"}""")
        val c = turn(s).confirms.single()
        assertEquals(ConfirmStatus.DENIED, c.status)
        assertEquals("phone", c.by)
        assertTrue(s.pendingConfirms.isEmpty())
    }

    @Test fun localAnswerIsNotOverwrittenByLateResolve() {
        var s = ChatReducer.onConfirmAnswered(feed(start, confirm), "k1", approve = true)
        s = feed("""{"t":"confirm_resolved","id":"t1","confirm_id":"k1","outcome":"expired","by":""}""", from = s)
        assertEquals(ConfirmStatus.APPROVED, turn(s).confirms.single().status)
    }

    @Test fun confirmWithoutTurnIsKeptNotDropped() {
        val s = feed("""{"t":"confirm_request","id":"v9","confirm_id":"k2","action":"Open","detail":"","danger":"MODERATE","timeout":30}""", now = 5_000)
        assertEquals(35_000L, s.looseConfirms.single().expiresAt)
        assertEquals(1, s.pendingConfirms.size)
        assertEquals(ConfirmStatus.EXPIRED, feed("""{"t":"confirm_expired","id":"v9","confirm_id":"k2"}""", from = s).looseConfirms.single().status)
    }

    @Test fun errorOnLiveTurnAnnotatesItErrorWithoutTurnIsOwnMessage() {
        val s = feed(start, """{"t":"error","id":"t1","message":"bad"}""")
        assertEquals("bad", turn(s).error)
        assertTrue(turn(s).pending)
        val s2 = feed("""{"t":"error","id":"t7","message":"A request is already running."}""")
        assertEquals("A request is already running.", (s2.messages.single() as ChatMessage.Assistant).error)
    }

    @Test fun askNotSentShowsOutcome() {
        val s = ChatReducer.onAskNotSent(ChatReducer.onAsk(ChatState(), "t1", "hi"), "t1")
        assertEquals("Not connected.", (s.messages.last() as ChatMessage.Assistant).error)
    }

    @Test fun resetClearsConversation() {
        assertTrue(feed(start, """{"t":"reset_ok"}""").messages.isEmpty())
    }

    @Test fun retryBackoffAndOffline() {
        assertEquals(Link.RECONNECTING, ChatRetry.next(0).link)
        assertEquals(500L, ChatRetry.next(0).delayMs)
        assertEquals(Link.OFFLINE, ChatRetry.next(3).link)
        assertEquals(ChatRetry.MAX_MS, ChatRetry.next(40).delayMs)
    }
}
