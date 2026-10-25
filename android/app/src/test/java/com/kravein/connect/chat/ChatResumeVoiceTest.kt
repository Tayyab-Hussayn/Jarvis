package com.kravein.connect.chat

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** Resume (#111 replay) and spoken turns in the reducer. The spoken turns follow web/lib/useKravein.ts. */
class ChatResumeVoiceTest {
    private fun feed(vararg lines: String, from: ChatState = ChatState()): ChatState =
        lines.fold(from) { s, l -> ChatReducer.onFrame(s, ChatFrames.parse(l), 1_000) }

    private fun voice(phase: String, text: String = "") = """{"t":"voice_state","phase":"$phase","text":"$text"}"""

    private fun assistant(s: ChatState, id: String) = s.messages.filterIsInstance<ChatMessage.Assistant>().first { it.id == id }

    // ---- replay ----

    private val replay = """{"t":"replay","busy":false,"voice":{"active":false,"mode":"","audio":""},"frames":[
        {"t":"user_turn","id":"t1","text":"battery?"},{"t":"turn_start","id":"t1"},
        {"t":"tool_call","id":"t1","call_id":"c1","name":"phone_status","args":{},"display":"phone_status()"},
        {"t":"tool_result","id":"t1","call_id":"c1","name":"phone_status","result":"80%"},
        {"t":"answer","id":"t1","text":"80 percent"},{"t":"turn_end","id":"t1","iterations":1}]}"""

    @Test fun replayRebuildsTheTimelineInPlaceOfWhatWasThere() {
        val stale = feed("""{"t":"turn_start","id":"t9"}""", """{"t":"tool_call","id":"t9","call_id":"x","name":"n","args":{},"display":""}""")
        val s = feed(replay, from = stale)
        assertEquals(listOf("t1-user", "t1"), s.messages.map { it.id })
        assertEquals("battery?", (s.messages[0] as ChatMessage.User).text)
        val m = assistant(s, "t1")
        assertEquals(ToolStatus.DONE, m.tools.single().status)
        assertEquals("80 percent", m.text)
        assertFalse(m.pending)
        assertFalse(s.busy)
    }

    @Test fun replayContinuesTheTurnCounterSoNoIdIsReused() {
        val s = feed(replay)
        assertEquals(1, s.seq)
        assertEquals("t2", "t${s.seq + 1}")
        val withSpoken = feed(
            """{"t":"replay","busy":false,"voice":{"active":false,"mode":"","audio":""},"frames":[
              {"t":"user_turn","id":"t4","text":"a"},{"t":"turn_start","id":"t4"},{"t":"turn_end","id":"t4","iterations":1}]}""",
        )
        assertEquals(4, withSpoken.seq)
    }

    @Test fun replayWithARunningTurnKeepsItBusyAndOpen() {
        val s = feed("""{"t":"replay","busy":true,"voice":{"active":false,"mode":"","audio":""},"frames":[{"t":"user_turn","id":"t1","text":"x"},{"t":"turn_start","id":"t1"}]}""")
        assertTrue(s.busy)
        assertTrue(assistant(s, "t1").pending)
    }

    @Test fun replayRestoresALiveDeviceVoiceSession() {
        val s = feed("""{"t":"replay","busy":false,"voice":{"active":true,"mode":"live","audio":"device"},"frames":[]}""")
        assertEquals(VoicePhase.IDLE, s.voice.phase)
        assertEquals("live", s.voice.mode)
        assertEquals("device", s.voice.audio)
    }

    @Test fun replayWithNoVoiceEndsAStaleVoiceState() {
        val live = feed(voice("started", "live"), voice("listening"))
        val s = feed("""{"t":"replay","busy":false,"voice":{"active":false,"mode":"","audio":""},"frames":[]}""", from = live)
        assertEquals(VoicePhase.OFF, s.voice.phase)
    }

    @Test fun helloOfAFreshSessionNotesTheLostConversationAndDropsVoice() {
        val before = feed(replay, voice("started", "live"))
        val s = feed("""{"t":"hello","model":"m","session_id":"new","resumed":false}""", from = before)
        assertEquals(VoicePhase.OFF, s.voice.phase)
        assertEquals(SESSION_LOST_NOTE, (s.messages.last() as ChatMessage.Assistant).text)
        // The first ever connection has nothing to lose, so says nothing.
        assertTrue(feed("""{"t":"hello","model":"m","session_id":"new","resumed":false}""").messages.isEmpty())
    }

    @Test fun helloOfAResumedSessionKeepsVoice() {
        val s = feed("""{"t":"hello","model":"m","session_id":"a","resumed":true}""", from = feed(voice("started", "live")))
        assertEquals(VoicePhase.IDLE, s.voice.phase)
    }

    // ---- spoken turns ----

    @Test fun aSpokenTurnIsOpenedByTheFirstWordsAndFilledInPlace() {
        val s = feed(voice("started", "live"), voice("heard_partial", "open you"), voice("heard_partial", "open youtube"), voice("heard", "open youtube"))
        assertEquals(listOf("s1-user", "s1"), s.messages.map { it.id })
        assertEquals("open youtube", (s.messages[0] as ChatMessage.User).text)
        assertEquals(VoicePhase.THINKING, s.voice.phase)
        assertEquals(1, s.seq)
    }

    @Test fun listeningClosesTheTurnAndTheNextOneGetsANewId() {
        val s = feed(voice("started", "live"), voice("heard", "a"), voice("answer", "b"), voice("listening"), voice("heard", "c"))
        assertEquals(listOf("s1-user", "s1", "s2-user", "s2"), s.messages.map { it.id })
        assertFalse(assistant(s, "s1").pending)
        assertTrue(assistant(s, "s2").pending)
    }

    @Test fun typedAndSpokenTurnsShareOneCounter() {
        val typed = ChatReducer.onAsk(ChatState(), "t1", "hi")
        val s = feed(voice("started", "live"), voice("heard", "x"), from = typed)
        assertEquals("s2", s.voice.turnId)
    }

    @Test fun spokenToolCallsAreSplitNotParsed() {
        val s = feed(voice("started", "live"), voice("tool", "open_app(name='YouTube')"), voice("tool_result", "ok"), voice("answer", "Done"))
        val tool = assistant(s, "s1").tools.single()
        assertEquals("open_app", tool.name)
        assertEquals("name='YouTube'", tool.args)
        assertEquals("open_app(name='YouTube')", tool.display)
        assertEquals(ToolStatus.DONE, tool.status)
        assertEquals("ok", tool.result)
    }

    @Test fun anErrorInsideATurnAnnotatesItAndTheSessionLives() {
        val s = feed(voice("started", "live"), voice("heard", "x"), voice("error", "tool timed out"))
        assertEquals("tool timed out", assistant(s, "s1").error)
        assertTrue(s.voice.phase.live)
    }

    @Test fun anErrorWithNoTurnIsFatal() {
        val s = feed(voice("started", "live"), voice("error", "no key"))
        assertEquals(VoicePhase.ERROR, s.voice.phase)
        assertFalse(s.voice.phase.live)
        assertTrue("no key" in s.voice.notes)
    }

    @Test fun stoppedEndsTheSessionAndSettlesTheTurn() {
        val s = feed(voice("started", "live"), voice("heard", "x"), voice("stopped"))
        assertEquals(VoicePhase.OFF, s.voice.phase)
        assertNull(s.voice.turnId)
        assertFalse(assistant(s, "s1").pending)
    }

    @Test fun aLatePhaseFrameCannotRaiseAFinishedSession() {
        val s = feed(voice("started", "live"), voice("stopped"), voice("listening"), voice("speaking"))
        assertEquals(VoicePhase.OFF, s.voice.phase)
    }

    @Test fun stoppingCannotWedgeOnceTheSessionIsOver() {
        var s = feed(voice("started", "live"))
        s = ChatReducer.onVoiceStopRequested(s)
        assertTrue(s.voice.isStopping)
        s = feed(voice("stopped"), from = s)
        assertFalse(s.voice.isStopping)
        // Stopping something that is not running never sets the flag at all.
        assertFalse(ChatReducer.onVoiceStopRequested(ChatState()).voice.stopping)
    }

    @Test fun aLostSocketKeepsTheVoicePhaseButSettlesTheTurn() {
        val s = ChatReducer.onDisconnected(feed(voice("started", "live"), voice("heard", "x")))
        // Not reset: the server keeps the session for its grace period and the resumed replay decides.
        assertEquals(VoicePhase.THINKING, s.voice.phase)
        assertNull(s.voice.turnId)
        assertFalse(assistant(s, "s1").pending)
    }

    @Test fun voiceRequestedShowsStartingOnlyWhenNothingIsLive() {
        assertEquals(VoicePhase.STARTING, ChatReducer.onVoiceRequested(ChatState()).voice.phase)
        val live = feed(voice("started", "live"), voice("listening"))
        assertEquals(VoicePhase.LISTENING, ChatReducer.onVoiceRequested(live).voice.phase)
    }

    @Test fun voiceOffEndsItHereAndNotesWhy() {
        val s = ChatReducer.onVoiceOff(feed(voice("started", "live"), voice("heard", "x")), "Voice stopped.")
        assertEquals(VoicePhase.OFF, s.voice.phase)
        assertTrue("Voice stopped." in s.voice.notes)
        assertFalse(assistant(s, "s1").pending)
    }

    @Test fun anEmptySpokenRequestIsNotATimelineNode() {
        val s = feed(voice("started", "live"), voice("answer", "Hello"))
        assertEquals(1, ChatTimeline.rows(s.messages).count { it is TimelineRow.Answer })
        assertTrue(ChatTimeline.rows(s.messages).none { it is TimelineRow.Request })
    }
}
