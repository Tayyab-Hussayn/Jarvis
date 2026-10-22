package com.kravein.connect.protocol

import org.json.JSONObject
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class FrameCodecTest {
    private fun decodeAsServer(text: String) = FrameCodec.decode(text, Direction.SERVER_TO_PHONE)

    private fun errorOf(text: String): ProtocolException = assertFailsWith { decodeAsServer(text) }

    @Test
    fun decodesAWelcome() {
        val frame = decodeAsServer("""{"type":"welcome","request_id":"r1","payload":{"protocol":1,"device_id":"ph_1","server_name":"Kravein"}}""")
        assertEquals("welcome", frame.type)
        assertEquals("r1", frame.requestId)
        assertEquals("ph_1", frame.payload.getString("device_id"))
    }

    @Test
    fun decodesExecuteWithKnownCommand() {
        val frame = decodeAsServer("""{"type":"execute","request_id":"r2","payload":{"command":"volume_set","args":{"percent":30}}}""")
        assertEquals("volume_set", frame.payload.getString("command"))
    }

    @Test
    fun rejectsNonJsonAndNonObjects() {
        assertEquals(ErrorCode.MALFORMED_FRAME, errorOf("nope").code)
        assertEquals(ErrorCode.MALFORMED_FRAME, errorOf("[1]").code)
    }

    @Test
    fun rejectsMissingEnvelopeFieldsAndKeepsTheRequestId() {
        val e = errorOf("""{"type":"ping","request_id":"abc"}""")
        assertEquals(ErrorCode.MALFORMED_FRAME, e.code)
        assertEquals("abc", e.requestId)
    }

    @Test
    fun rejectsUnexpectedFieldsEverywhere() {
        assertEquals(ErrorCode.MALFORMED_FRAME, errorOf("""{"type":"ping","request_id":"a","payload":{},"timestamp":"x"}""").code)
        assertEquals(ErrorCode.MALFORMED_FRAME, errorOf("""{"type":"ping","request_id":"a","payload":{"x":1}}""").code)
    }

    @Test
    fun rejectsOverlongRequestId() {
        val id = "x".repeat(65)
        assertEquals(ErrorCode.MALFORMED_FRAME, errorOf("""{"type":"ping","request_id":"$id","payload":{}}""").code)
    }

    @Test
    fun rejectsFramesOnlyThePhoneMaySend() {
        // hello and result are phone_to_server: the server must never be able to feed them to the phone.
        val hello = """{"type":"hello","request_id":"a","payload":{"protocol":1,"capabilities":[]}}"""
        assertEquals(ErrorCode.UNKNOWN_FRAME, errorOf(hello).code)
        assertEquals(ErrorCode.UNKNOWN_FRAME, errorOf("""{"type":"teleport","request_id":"a","payload":{}}""").code)
    }

    @Test
    fun unknownCommandInExecuteIsRejectedAtDecode() {
        assertEquals(ErrorCode.UNKNOWN_COMMAND, errorOf("""{"type":"execute","request_id":"a","payload":{"command":"rm_rf","args":{}}}""").code)
    }

    @Test
    fun oversizedFrameIsRefusedBeforeParsing() {
        val big = """{"type":"ping","request_id":"a","payload":{},"pad":"${"x".repeat(PhoneProtocol.MAX_FRAME_BYTES)}"}"""
        assertEquals(ErrorCode.FRAME_TOO_LARGE, errorOf(big).code)
    }

    @Test
    fun helloRoundTripsAndFitsServerLimits() {
        val frame = FrameCodec.hello("id1", listOf("get_battery", "open_url"), "P".repeat(200), "14", "0.1.0")
        val decoded = FrameCodec.decode(frame.encode(), Direction.PHONE_TO_SERVER)
        assertEquals("hello", decoded.type)
        assertEquals(PhoneProtocol.VERSION, decoded.payload.getInt("protocol"))
        assertEquals(80, decoded.payload.getString("device_name").length)
        assertEquals(2, decoded.payload.getJSONArray("capabilities").length())
    }

    @Test
    fun helloOmitsBlankOptionalFields() {
        val payload = FrameCodec.hello("id1", emptyList(), "  ", "", "1").payload
        assertFalse(payload.has("device_name"))
        assertFalse(payload.has("os_version"))
        assertTrue(payload.has("app_version"))
    }

    @Test
    fun successAndFailureResultsAreValidFrames() {
        val ok = FrameCodec.result("r", CommandResult(true, mapOf("percent" to 80, "charging" to false)))
        val decodedOk = FrameCodec.decode(ok.encode(), Direction.PHONE_TO_SERVER)
        assertEquals(80, decodedOk.payload.getJSONObject("data").getInt("percent"))

        val failed = FrameCodec.result("r", CommandResult.failure(ErrorCode.UNAVAILABLE, "x".repeat(900)))
        val decodedFail = FrameCodec.decode(failed.encode(), Direction.PHONE_TO_SERVER)
        assertEquals("unavailable", decodedFail.payload.getString("error_code"))
        assertEquals(500, decodedFail.payload.getString("error").length)
        assertFalse(decodedFail.payload.has("data"))
    }

    @Test
    fun pongAndErrorAreValidFrames() {
        FrameCodec.decode(FrameCodec.pong("p").encode(), Direction.PHONE_TO_SERVER)
        val err = FrameCodec.decode(FrameCodec.error("", ErrorCode.NOT_READY, "wait").encode(), Direction.PHONE_TO_SERVER)
        assertEquals("not_ready", err.payload.getString("code"))
    }

    @Test
    fun nestedDataBecomesJson() {
        val json: JSONObject = FrameCodec.toJsonObject(mapOf("a" to mapOf("b" to listOf(1, 2)), "c" to null))
        assertEquals(2, json.getJSONObject("a").getJSONArray("b").length())
        assertTrue(json.isNull("c"))
    }
}
