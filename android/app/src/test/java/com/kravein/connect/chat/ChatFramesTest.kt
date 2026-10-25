package com.kravein.connect.chat

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertTrue

class ChatFramesTest {
    @Test fun toolCallFrame() {
        val f = ChatFrames.parse("""{"t":"tool_call","id":"t1","call_id":"c1","name":"phone_status","args":{"a":1},"display":"phone_status()"}""")
        assertEquals(ServerFrame.ToolCall("t1", "c1", "phone_status", "phone_status()", """{"a":1}"""), f)
    }

    @Test fun confirmRequestDangerAndDeadline() {
        val f = ChatFrames.parse("""{"t":"confirm_request","id":"t1","confirm_id":"k1","action":"Delete file","detail":"/tmp/x","danger":"DESTRUCTIVE","timeout":120}""")
        assertIs<ServerFrame.ConfirmRequest>(f)
        assertEquals(Danger.DESTRUCTIVE, f.danger)
        assertEquals(120, f.timeoutSeconds)
    }

    @Test fun unknownDangerReadsAsCritical() {
        val f = ChatFrames.parse("""{"t":"confirm_request","id":"","confirm_id":"k","action":"a","detail":"","danger":"???","timeout":5}""")
        assertEquals(Danger.CRITICAL, (f as ServerFrame.ConfirmRequest).danger)
    }

    @Test fun turnEndCancelledAndError() {
        val f = ChatFrames.parse("""{"t":"turn_end","id":"t1","iterations":3,"cancelled":true}""")
        assertEquals(ServerFrame.TurnEnd("t1", 3, null, true), f)
        assertEquals("boom", (ChatFrames.parse("""{"t":"turn_end","id":"t1","iterations":1,"error":"boom"}""") as ServerFrame.TurnEnd).error)
    }

    @Test fun confirmResolved() {
        assertEquals(
            ServerFrame.ConfirmResolved("k1", ConfirmOutcome.APPROVED, "phone"),
            ChatFrames.parse("""{"t":"confirm_resolved","id":"t1","confirm_id":"k1","outcome":"approved","by":"phone"}"""),
        )
        assertEquals(ServerFrame.Malformed, ChatFrames.parse("""{"t":"confirm_resolved","confirm_id":"k1","outcome":"maybe"}"""))
    }

    @Test fun errorWithoutId() {
        assertEquals(ServerFrame.Error(null, "nope"), ChatFrames.parse("""{"t":"error","message":"nope"}"""))
    }

    @Test fun uninterestingFramesAreIgnoredNotErrors() {
        assertEquals(ServerFrame.Ignored, ChatFrames.parse("""{"t":"voice_state","phase":"idle","text":""}"""))
        assertEquals(ServerFrame.Ignored, ChatFrames.parse("""{"t":"job_event","id":"j","title":"t","text":"x","failed":false}"""))
    }

    // Server #111 (resumable sessions) added fields to `hello` and the `replay`/`user_turn` frames; a phone that does
    // not resume yet must neither choke on the new hello nor mistake a replay for live conversation.
    @Test fun resumableSessionFramesDoNotBreakTheClient() {
        val hello = ChatFrames.parse("""{"t":"hello","assistant_name":"Kravein","skills":168,"provider":"p","model":"m","environment":"e","session_id":"s1","resumed":true,"session_ttl_minutes":30}""")
        assertEquals(ServerFrame.Hello("Kravein", "m"), hello)
        assertEquals(ServerFrame.Ignored, ChatFrames.parse("""{"t":"replay","frames":[{"t":"user_turn","id":"t1","text":"hi"}],"busy":false,"voice":{"active":false,"mode":"","audio":""}}"""))
        assertEquals(ServerFrame.Ignored, ChatFrames.parse("""{"t":"user_turn","id":"t1","text":"hi"}"""))
    }

    @Test fun garbageIsMalformed() {
        assertEquals(ServerFrame.Malformed, ChatFrames.parse("not json"))
        assertEquals(ServerFrame.Malformed, ChatFrames.parse("""{"id":"x"}"""))
        assertEquals(ServerFrame.Malformed, ChatFrames.parse("""{"t":"answer","id":"t1","text":5}"""))
        assertEquals(ServerFrame.Malformed, ChatFrames.parse("""{"t":"tool_call","id":"t1"}"""))
        assertEquals(ServerFrame.Malformed, ChatFrames.parse("""{"t":"answer","id":"t1","text":"${"x".repeat(ChatFrames.MAX_FRAME_CHARS)}"}"""))
    }

    @Test fun builders() {
        assertTrue(ChatFrames.ask("t1", "hi \"there\"").contains("""\"there\""""))
        // JSONObject does not keep key order, so compare the parsed frame.
        val reply = org.json.JSONObject(ChatFrames.confirmReply("k", false))
        assertEquals("confirm_reply", reply.getString("t"))
        assertEquals("k", reply.getString("confirm_id"))
        assertEquals(false, reply.getBoolean("approve"))
        assertEquals("""{"t":"cancel"}""", ChatFrames.cancel())
    }
}
