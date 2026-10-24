package com.kravein.connect.network

import com.kravein.connect.commands.CommandRouter
import com.kravein.connect.commands.DeviceAction
import com.kravein.connect.commands.DeviceBackend
import com.kravein.connect.core.ConnectionState
import com.kravein.connect.protocol.CommandResult
import com.kravein.connect.protocol.Direction
import com.kravein.connect.protocol.ErrorCode
import com.kravein.connect.protocol.Frame
import com.kravein.connect.protocol.FrameCodec
import org.json.JSONObject
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertIs
import kotlin.test.assertTrue

class PhoneSessionTest {
    private val ran = mutableListOf<DeviceAction>()
    private val backend = object : DeviceBackend {
        override fun execute(action: DeviceAction, args: JSONObject): CommandResult {
            ran += action
            return CommandResult(true, mapOf("percent" to 55))
        }
    }
    private val session = PhoneSession(CommandRouter(backend), HelloInfo("Pixel", "15", "0.1.0")) { "hello-1" }

    private fun sent(out: SessionOutput): List<Frame> = out.send.map { FrameCodec.decode(it, Direction.PHONE_TO_SERVER) }

    private fun welcome(id: String = "hello-1", protocol: Int = 1) =
        """{"type":"welcome","request_id":"$id","payload":{"protocol":$protocol,"device_id":"ph_9","server_name":"Kravein"}}"""

    private fun execute(id: String, command: String, args: String = "{}") =
        """{"type":"execute","request_id":"$id","payload":{"command":"$command","args":$args}}"""

    @Test
    fun opensWithAHelloAdvertisingExactlyTheImplementedCommands() {
        val hello = sent(session.open()).single()
        assertEquals("hello", hello.type)
        assertEquals("hello-1", hello.requestId)
        val caps = hello.payload.getJSONArray("capabilities")
        assertEquals(DeviceAction.entries.map { it.wire }, (0 until caps.length()).map { caps.getString(it) })
    }

    @Test
    fun welcomeMakesTheSessionReady() {
        session.open()
        val out = session.onText(welcome())
        assertTrue(session.ready)
        assertEquals(SessionEvent.Ready("ph_9", "Kravein"), out.events.single())
    }

    @Test
    fun executeBeforeWelcomeIsRefusedAndNotRun() {
        session.open()
        val reply = sent(session.onText(execute("e1", "flashlight_on"))).single()
        assertEquals("error", reply.type)
        assertEquals("not_ready", reply.payload.getString("code"))
        assertEquals("e1", reply.requestId)
        assertTrue(ran.isEmpty())
    }

    @Test
    fun executeAfterWelcomeAnswersWithTheSameRequestId() {
        session.open()
        session.onText(welcome())
        val reply = sent(session.onText(execute("e2", "get_battery"))).single()
        assertEquals("result", reply.type)
        assertEquals("e2", reply.requestId)
        assertTrue(reply.payload.getBoolean("ok"))
        assertEquals(55, reply.payload.getJSONObject("data").getInt("percent"))
        assertEquals(listOf(DeviceAction.GET_BATTERY), ran)
    }

    @Test
    fun invalidArgumentsGetAFailedResultNotAnExecution() {
        session.open()
        session.onText(welcome())
        val reply = sent(session.onText(execute("e3", "volume_set", """{"percent":500}"""))).single()
        assertFalse(reply.payload.getBoolean("ok"))
        assertEquals("invalid_args", reply.payload.getString("error_code"))
        assertTrue(ran.isEmpty())
    }

    @Test
    fun unknownCommandIsRefusedWithAnErrorFrameAndNeverRun() {
        session.open()
        session.onText(welcome())
        val reply = sent(session.onText(execute("e4", "format_disk"))).single()
        assertEquals("error", reply.type)
        assertEquals("unknown_command", reply.payload.getString("code"))
        assertEquals("e4", reply.requestId)
        assertTrue(ran.isEmpty())
    }

    @Test
    fun pingIsAnsweredWithPongAtAnyTime() {
        val reply = sent(session.onText("""{"type":"ping","request_id":"p1","payload":{}}""")).single()
        assertEquals("pong", reply.type)
        assertEquals("p1", reply.requestId)
    }

    @Test
    fun garbageGetsAnErrorFrame() {
        val reply = sent(session.onText("not json")).single()
        assertEquals("malformed_frame", reply.payload.getString("code"))
        assertTrue(ran.isEmpty())
    }

    @Test
    fun aWelcomeThatAnswersNoHelloIsRefused() {
        session.open()
        val reply = sent(session.onText(welcome(id = "someone-else"))).single()
        assertEquals("unknown_request", reply.payload.getString("code"))
        assertFalse(session.ready)
    }

    @Test
    fun protocolMismatchClosesTheSession() {
        session.open()
        val out = session.onText(welcome(protocol = 2))
        assertTrue(out.close)
        assertFalse(session.ready)
        assertEquals(ErrorCode.PROTOCOL_MISMATCH, assertIs<SessionEvent.ServerError>(out.events.single()).code)
    }

    @Test
    fun serverErrorAnsweringHelloClosesButLaterOnesDoNot() {
        session.open()
        val refused = session.onText("""{"type":"error","request_id":"hello-1","payload":{"code":"unknown_capability","message":"nope"}}""")
        assertTrue(refused.close)

        session.onText(welcome())
        val later = session.onText("""{"type":"error","request_id":"zzz","payload":{"code":"failed","message":"meh"}}""")
        assertFalse(later.close)
        assertEquals("meh", assertIs<SessionEvent.ServerError>(later.events.single()).message)
    }

    @Test
    fun anOversizedResultIsReplacedByASmallFailure() {
        val huge = object : DeviceBackend {
            override fun execute(action: DeviceAction, args: JSONObject) =
                CommandResult(true, mapOf("blob" to "x".repeat(70_000)))
        }
        val s = PhoneSession(CommandRouter(huge), HelloInfo("P", "1", "1")) { "h" }
        s.open()
        s.onText(welcome(id = "h"))
        val reply = sent(s.onText(execute("e5", "get_device_info"))).single()
        assertFalse(reply.payload.getBoolean("ok"))
        assertEquals("failed", reply.payload.getString("error_code"))
    }

    // -- connection policy: the revoked-credential path ------------------------------------------

    @Test
    fun aRefusedHandshakeMeansPairAgainAndStopsReconnecting() {
        for (status in listOf(401, 403)) {
            val action = ConnectionPolicy.onFailure(status, attempt = 1)
            assertEquals(FailureAction.NeedsPairing, action)
            assertEquals(ConnectionState.NEEDS_PAIRING, ConnectionPolicy.stateAfter(action))
        }
    }

    @Test
    fun otherFailuresRetryWithGrowingCappedBackoff() {
        assertEquals(FailureAction.Retry(2_000), ConnectionPolicy.onFailure(null, 1))
        assertEquals(FailureAction.Retry(2_000), ConnectionPolicy.onFailure(500, 1))
        // TLS: a pin mismatch stops at once; a pre-TLS credential only after three plain-http-to-TLS looking failures.
        val mismatch = javax.net.ssl.SSLHandshakeException("bad").apply { initCause(PinMismatchException("pin")) }
        assertEquals(FailureAction.PinMismatch, ConnectionPolicy.onFailure(null, 1, mismatch))
        assertEquals(FailureAction.Retry(2_000), ConnectionPolicy.onFailure(null, 1, java.io.EOFException(), tlsHints = 2))
        assertEquals(FailureAction.NeedsTlsRepair, ConnectionPolicy.onFailure(null, 3, java.io.EOFException(), tlsHints = 3))
        assertEquals(ConnectionState.NEEDS_PAIRING, ConnectionPolicy.stateAfter(FailureAction.PinMismatch))
        assertEquals(ConnectionState.NEEDS_PAIRING, ConnectionPolicy.stateAfter(FailureAction.NeedsTlsRepair))
        assertEquals(ConnectionState.RECONNECTING, ConnectionPolicy.stateAfter(ConnectionPolicy.onFailure(null, 2)))
        assertEquals(listOf(2_000L, 4_000L, 8_000L, 16_000L, 30_000L, 30_000L), (1..6).map { ConnectionPolicy.backoffMs(it) })
        assertEquals(30_000L, ConnectionPolicy.backoffMs(1_000))
    }
}
