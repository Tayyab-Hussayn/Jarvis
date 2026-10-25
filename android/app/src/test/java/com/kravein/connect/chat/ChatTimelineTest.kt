package com.kravein.connect.chat

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertTrue

class ChatTimelineTest {
    private fun rows(vararg lines: String): List<TimelineRow> {
        var s = ChatReducer.onAsk(ChatState(), "t1", "hello")
        for (l in lines) s = ChatReducer.onFrame(s, ChatFrames.parse(l), 0)
        return ChatTimeline.rows(s.messages)
    }

    private val start = """{"t":"turn_start","id":"t1"}"""
    private val call = """{"t":"tool_call","id":"t1","call_id":"c1","name":"phone_status","args":{},"display":"phone_status()"}"""

    @Test fun requestToolAnswerAreOneOrderedPath() {
        val r = rows(
            start, call,
            """{"t":"tool_result","id":"t1","call_id":"c1","name":"n","result":"ok"}""",
            """{"t":"answer","id":"t1","text":"Done"}""",
            """{"t":"turn_end","id":"t1","iterations":1}""",
        )
        assertEquals(
            listOf("Request", "Tool", "Answer"),
            r.map { it::class.simpleName },
        )
    }

    @Test fun runningTurnEndsWithAWorkingNode() {
        assertIs<TimelineRow.Working>(rows(start).last())
        // While a tool runs, the tool card itself is the live node.
        assertTrue(rows(start, call).none { it is TimelineRow.Working })
    }

    @Test fun disconnectShowsUnknownCardAndLostNote() {
        var s = ChatReducer.onAsk(ChatState(), "t1", "hi")
        for (l in listOf(start, call)) s = ChatReducer.onFrame(s, ChatFrames.parse(l), 0)
        val r = ChatTimeline.rows(ChatReducer.onDisconnected(s).messages)
        assertEquals(ToolStatus.UNKNOWN, r.filterIsInstance<TimelineRow.Tool>().single().tool.status)
        assertEquals(StatusKind.LOST, r.filterIsInstance<TimelineRow.Status>().single().kind)
        assertTrue(r.none { it is TimelineRow.Working })
    }

    @Test fun stoppedIsNotAnError() {
        val r = rows(start, """{"t":"turn_end","id":"t1","iterations":1,"cancelled":true}""")
        assertEquals(StatusKind.STOPPED, r.filterIsInstance<TimelineRow.Status>().single().kind)
    }

    @Test fun keysAreUnique() {
        val r = rows(start, call, """{"t":"thinking","id":"t1","text":"x"}""", """{"t":"answer","id":"t1","text":"a"}""")
        assertEquals(r.size, r.map { it.key }.toSet().size)
    }
}
