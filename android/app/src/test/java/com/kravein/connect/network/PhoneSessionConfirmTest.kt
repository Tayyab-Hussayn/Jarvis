package com.kravein.connect.network

import com.kravein.connect.commands.CommandRouter
import com.kravein.connect.commands.DeviceAction
import com.kravein.connect.commands.DeviceBackend
import com.kravein.connect.confirm.ConfirmationBook
import com.kravein.connect.confirm.Danger
import com.kravein.connect.confirm.Outcome
import com.kravein.connect.confirm.ReplyOutcome
import com.kravein.connect.protocol.CommandResult
import com.kravein.connect.protocol.Direction
import com.kravein.connect.protocol.FrameCodec
import org.json.JSONObject
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertIs
import kotlin.test.assertTrue

class PhoneSessionConfirmTest {
    private var now = 1_000L
    private val book = ConfirmationBook()
    private val backend = object : DeviceBackend {
        override fun execute(action: DeviceAction, args: JSONObject) = CommandResult(true)
    }
    private val session = PhoneSession(CommandRouter(backend), HelloInfo("Pixel", "15", "0.1.0"), book, { now }) { "hello-1" }

    private fun ready() {
        session.open()
        session.onText("""{"type":"welcome","request_id":"hello-1","payload":{"protocol":1,"device_id":"ph_9","server_name":"Kravein"}}""")
    }

    private fun request(id: String = "c1", danger: String = "destructive", expiresAt: Long = 1_100) =
        """{"type":"confirm_request","request_id":"$id","payload":{"action":"Delete files","detail":"rm ~/old","danger":"$danger","expires_at":$expiresAt}}"""

    private fun closed(id: String = "c1", outcome: String = "denied") =
        """{"type":"confirm_closed","request_id":"$id","payload":{"outcome":"$outcome"}}"""

    private fun errorCode(out: SessionOutput) =
        FrameCodec.decode(out.send.single(), Direction.PHONE_TO_SERVER).payload.getString("code")

    @Test
    fun aRequestBecomesAnEventAndIsRemembered() {
        ready()
        val out = session.onText(request(danger = "critical"))
        val event = assertIs<SessionEvent.ConfirmRequested>(out.events.single())
        assertEquals("c1", event.confirmation.requestId)
        assertEquals("Delete files", event.confirmation.action)
        assertEquals(Danger.CRITICAL, event.confirmation.danger)
        assertTrue(out.send.isEmpty())
        assertEquals(event.confirmation, book.get("c1"))
    }

    @Test
    fun requestsBeforeWelcomeAreRefused() {
        session.open()
        assertEquals("not_ready", errorCode(session.onText(request())))
        assertEquals(null, book.get("c1"))
    }

    @Test
    fun unusableRequestsAreErrorsAndShowNothing() {
        ready()
        assertEquals("malformed_frame", errorCode(session.onText(request(danger = "mild"))))
        // Missing field: the strict codec rejects it before the session sees it.
        val missing = """{"type":"confirm_request","request_id":"c2","payload":{"action":"x","detail":"y","danger":"critical"}}"""
        assertEquals("malformed_frame", errorCode(session.onText(missing)))
        // The phone never receives its own frame type.
        val wrongWay = """{"type":"confirm_reply","request_id":"c3","payload":{"approve":true}}"""
        assertEquals("unknown_frame", errorCode(session.onText(wrongWay)))
        assertEquals(null, book.get("c1"))
    }

    @Test
    fun aRequestThatIsAlreadyExpiredIsDropped() {
        ready()
        val out = session.onText(request(expiresAt = 1_000))
        assertTrue(out.events.isEmpty())
        assertEquals(null, book.get("c1"))
    }

    @Test
    fun closedRemovesItAndReportsTheOutcome() {
        ready()
        session.onText(request())
        val out = session.onText(closed(outcome = "approved"))
        assertEquals(SessionEvent.ConfirmClosed("c1", Outcome.APPROVED), out.events.single())
        assertEquals(null, book.get("c1"))
        assertEquals("malformed_frame", errorCode(session.onText(closed(outcome = "maybe"))))
    }

    @Test
    fun approveBuildsTheReplyFrameAndOnlyOnce() {
        ready()
        session.onText(request())
        val plan = assertIs<ReplyPlan.Send>(session.planReply("c1", approve = true))
        val frame = FrameCodec.decode(plan.frame, Direction.PHONE_TO_SERVER)
        assertEquals("confirm_reply", frame.type)
        assertEquals("c1", frame.requestId)
        assertEquals(true, frame.payload.getBoolean("approve"))
        session.replySent("c1")
        assertEquals(ReplyPlan.Refused(ReplyOutcome.Unknown), session.planReply("c1", approve = true))
    }

    @Test
    fun aRefusedReplyIsAQuietEventNotASessionError() {
        ready()
        session.onText(request())
        session.replySent("c1")
        val out = session.onText("""{"type":"error","request_id":"c1","payload":{"code":"unknown_request","message":"late"}}""")
        assertEquals(SessionEvent.ReplyRejected("c1"), out.events.single())
        assertFalse(out.close)
        // The same code for an id we never answered stays a visible server error.
        val other = session.onText("""{"type":"error","request_id":"zz","payload":{"code":"unknown_request","message":"x"}}""")
        assertIs<SessionEvent.ServerError>(other.events.single())
    }

    @Test
    fun overLongTextIsRefusedByTheSchema() {
        ready()
        val long = "x".repeat(301)
        val frame = """{"type":"confirm_request","request_id":"c9","payload":{"action":"$long","detail":"d","danger":"critical","expires_at":1100}}"""
        assertEquals("malformed_frame", errorCode(session.onText(frame)))
    }

    @Test
    fun declineIsAFalseApprove() {
        ready()
        session.onText(request())
        val plan = assertIs<ReplyPlan.Send>(session.planReply("c1", approve = false))
        assertFalse(FrameCodec.decode(plan.frame, Direction.PHONE_TO_SERVER).payload.getBoolean("approve"))
    }

    @Test
    fun noReplyAfterExpiry() {
        ready()
        session.onText(request(expiresAt = 1_100))
        now = 1_100
        assertEquals(ReplyPlan.Refused(ReplyOutcome.Expired), session.planReply("c1", approve = true))
    }

    @Test
    fun noReplyBeforeWelcomeOrForAnUnknownId() {
        session.open()
        assertEquals(ReplyPlan.Refused(ReplyOutcome.NotConnected), session.planReply("c1", approve = true))
        ready()
        assertEquals(ReplyPlan.Refused(ReplyOutcome.Unknown), session.planReply("nope", approve = true))
    }

    @Test
    fun aNewSessionKeepsTheOpenQuestionButCannotAnswerUntilWelcomed() {
        ready()
        session.onText(request())
        val reconnected = PhoneSession(CommandRouter(backend), HelloInfo("Pixel", "15", "0.1.0"), book, { now }) { "hello-2" }
        reconnected.open()
        assertEquals(ReplyPlan.Refused(ReplyOutcome.NotConnected), reconnected.planReply("c1", approve = true))
    }
}
