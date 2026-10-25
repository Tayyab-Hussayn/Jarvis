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
        assertEquals(ServerFrame.Ignored, ChatFrames.parse("""{"t":"job_event","id":"j","title":"t","text":"x","failed":false}"""))
    }

    // Server #111 (resumable sessions): the session id in `hello`, the `replay` and the `user_turn` frames inside it.
    @Test fun helloCarriesTheSessionHandle() {
        val hello = ChatFrames.parse("""{"t":"hello","assistant_name":"Kravein","skills":168,"provider":"p","model":"m","environment":"e","session_id":"s1","resumed":true,"session_ttl_minutes":30}""")
        assertEquals(ServerFrame.Hello("Kravein", "m", sessionId = "s1", resumed = true), hello)
        // A server that predates resume says nothing: no handle, resumed unknown (not "false").
        assertEquals(ServerFrame.Hello("Kravein", "m", null, null), ChatFrames.parse("""{"t":"hello","assistant_name":"Kravein","model":"m"}"""))
        // A handle that is not a short string is not ours to store.
        assertEquals(null, (ChatFrames.parse("""{"t":"hello","model":"m","session_id":"${"x".repeat(200)}"}""") as ServerFrame.Hello).sessionId)
        assertEquals(null, (ChatFrames.parse("""{"t":"hello","model":"m","session_id":"  "}""") as ServerFrame.Hello).sessionId)
    }

    @Test fun replayCarriesTheRecordedConversation() {
        val replay = ChatFrames.parse(
            """{"t":"replay","busy":true,"voice":{"active":true,"mode":"live","audio":"device"},"frames":[
                {"t":"user_turn","id":"t1","text":"hi"},{"t":"turn_start","id":"t1"},{"t":"answer","id":"t1","text":"yo"},
                {"t":"voice_state","phase":"heard","text":"open youtube"},{"t":"job_event","id":"j"},{"nonsense":1},"junk"]}""",
        )
        assertIs<ServerFrame.Replay>(replay)
        assertEquals(true, replay.busy)
        assertEquals(ReplayVoice(true, "live", "device"), replay.voice)
        // Unknown and broken recorded frames are dropped one by one; the conversation survives.
        assertEquals(
            listOf(ServerFrame.UserTurn("t1", "hi"), ServerFrame.TurnStart("t1"), ServerFrame.Answer("t1", "yo"), ServerFrame.VoiceState("heard", "open youtube", null)),
            replay.frames,
        )
    }

    @Test fun replayRefusesNestingAndHugeFrameLists() {
        assertEquals(
            emptyList(),
            (ChatFrames.parse("""{"t":"replay","frames":[{"t":"replay","frames":[{"t":"turn_start","id":"t1"}]}],"busy":false}""") as ServerFrame.Replay).frames,
        )
        val many = (1..ChatFrames.MAX_REPLAY_FRAMES + 1).joinToString(",") { """{"t":"turn_start","id":"t$it"}""" }
        assertEquals(ServerFrame.Malformed, ChatFrames.parse("""{"t":"replay","frames":[$many],"busy":false}"""))
        assertEquals(ServerFrame.Malformed, ChatFrames.parse("""{"t":"replay","busy":false}"""))
        // No voice object means no voice, not a crash.
        assertEquals(ReplayVoice.NONE, (ChatFrames.parse("""{"t":"replay","frames":[],"busy":false}""") as ServerFrame.Replay).voice)
    }

    @Test fun onlyAReplayMayBeLargerThanTheFrameCap() {
        val big = "x".repeat(ChatFrames.MAX_FRAME_CHARS)
        assertEquals(ServerFrame.Malformed, ChatFrames.parse("""{"t":"answer","id":"t1","text":"$big"}"""))
        val replay = ChatFrames.parse("""{"t":"replay","busy":false,"frames":[{"t":"answer","id":"t1","text":"$big"}]}""")
        assertIs<ServerFrame.Replay>(replay)
    }

    @Test fun voiceFramesParseStrictly() {
        assertEquals(ServerFrame.VoiceState("started", "live", "device"), ChatFrames.parse("""{"t":"voice_state","phase":"started","text":"live","audio":"device"}"""))
        assertEquals(ServerFrame.VoiceState("listening", "", null), ChatFrames.parse("""{"t":"voice_state","phase":"listening"}"""))
        assertEquals(ServerFrame.Malformed, ChatFrames.parse("""{"t":"voice_state","text":"x"}"""))
        assertEquals(ServerFrame.AudioClear, ChatFrames.parse("""{"t":"audio_clear"}"""))
    }

    @Test fun voiceRequestsMatchTheServerProtocol() {
        assertEquals("""{"t":"voice_start","mode":"live","audio":"device"}""", ChatFrames.voiceStartDevice())
        assertEquals("""{"t":"voice_stop"}""", ChatFrames.voiceStop())
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
