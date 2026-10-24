package com.kravein.connect.network

import com.kravein.connect.commands.CommandRouter
import com.kravein.connect.confirm.ConfirmationBook
import com.kravein.connect.confirm.Outcome
import com.kravein.connect.confirm.PendingConfirmation
import com.kravein.connect.confirm.ReplyCheck
import com.kravein.connect.confirm.ReplyOutcome
import com.kravein.connect.notice.Notice
import com.kravein.connect.notice.NoticeDedupe
import com.kravein.connect.protocol.ErrorCode
import com.kravein.connect.protocol.Frame
import com.kravein.connect.protocol.FrameCodec
import com.kravein.connect.protocol.PhoneProtocol
import com.kravein.connect.protocol.ProtocolException

data class HelloInfo(val deviceName: String, val osVersion: String, val appVersion: String)

sealed interface SessionEvent {
    data class Ready(val deviceId: String, val serverName: String) : SessionEvent
    data class ServerError(val code: ErrorCode, val message: String) : SessionEvent
    data class ConfirmRequested(val confirmation: PendingConfirmation) : SessionEvent
    data class ConfirmClosed(val requestId: String, val outcome: Outcome) : SessionEvent
    data class ReplyRejected(val requestId: String) : SessionEvent
    data class NoticeReceived(val notice: Notice) : SessionEvent
}

/** A tap turned into the frame to send, or the reason nothing may be sent. */
sealed interface ReplyPlan {
    data class Send(val confirmation: PendingConfirmation, val frame: String) : ReplyPlan
    data class Refused(val outcome: ReplyOutcome) : ReplyPlan
}

/** What one inbound frame caused: frames to send back, things the UI should know, and whether to hang up. */
data class SessionOutput(
    val send: List<String> = emptyList(),
    val events: List<SessionEvent> = emptyList(),
    val close: Boolean = false,
)

/**
 * One websocket connection's protocol state, with no sockets or Android types so JVM tests can drive it.
 * `hello` goes out on open; `execute` is refused until `welcome` arrives; every inbound frame gets an answer
 * or an event; anything malformed gets an `error` frame and never reaches the command router.
 */
class PhoneSession(
    private val router: CommandRouter,
    private val info: HelloInfo,
    private val book: ConfirmationBook = ConfirmationBook(),
    private val nowSeconds: () -> Long = { System.currentTimeMillis() / 1000 },
    private val notices: NoticeDedupe = NoticeDedupe(),
    private val newRequestId: () -> String,
) {
    private var helloId: String? = null
    private val answered = LinkedHashSet<String>()
    var ready: Boolean = false
        private set

    fun open(): SessionOutput {
        val id = newRequestId()
        helloId = id
        val hello = FrameCodec.hello(id, router.capabilities, info.deviceName, info.osVersion, info.appVersion)
        return SessionOutput(send = listOf(hello.encode()))
    }

    fun onText(text: String): SessionOutput {
        val frame = try {
            FrameCodec.decode(text)
        } catch (e: ProtocolException) {
            return SessionOutput(send = listOf(FrameCodec.error(e.requestId, e.code, e.message.orEmpty()).encode()))
        }
        return when (frame.type) {
            "welcome" -> onWelcome(frame)
            "execute" -> onExecute(frame)
            "confirm_request" -> onConfirmRequest(frame)
            "confirm_closed" -> onConfirmClosed(frame)
            "notify" -> onNotify(frame)
            "ping" -> SessionOutput(send = listOf(FrameCodec.pong(frame.requestId).encode()))
            "pong" -> SessionOutput()
            "error" -> onError(frame)
            else -> reply(frame, ErrorCode.UNKNOWN_FRAME, "unexpected ${frame.type}")
        }
    }

    private fun onWelcome(frame: Frame): SessionOutput {
        if (ready || frame.requestId != helloId) {
            return reply(frame, ErrorCode.UNKNOWN_REQUEST, "no hello is waiting on ${frame.requestId}")
        }
        if (frame.payload.getInt("protocol") != PhoneProtocol.VERSION) {
            return SessionOutput(
                events = listOf(SessionEvent.ServerError(ErrorCode.PROTOCOL_MISMATCH, "server speaks protocol ${frame.payload.getInt("protocol")}")),
                close = true,
            )
        }
        ready = true
        return SessionOutput(
            events = listOf(SessionEvent.Ready(frame.payload.getString("device_id"), frame.payload.getString("server_name"))),
        )
    }

    private fun onExecute(frame: Frame): SessionOutput {
        if (!ready) return reply(frame, ErrorCode.NOT_READY, "hello has not been welcomed yet")
        val result = router.route(frame.payload.getString("command"), frame.payload.getJSONObject("args"))
        var text = FrameCodec.result(frame.requestId, result).encode()
        if (text.toByteArray(Charsets.UTF_8).size > PhoneProtocol.MAX_FRAME_BYTES) {
            // A result the server would reject as oversized (and then hang up on) is replaced by a small failure.
            text = FrameCodec.result(
                frame.requestId,
                com.kravein.connect.protocol.CommandResult.failure(ErrorCode.FAILED, "result too large"),
            ).encode()
        }
        return SessionOutput(send = listOf(text))
    }

    private fun onConfirmRequest(frame: Frame): SessionOutput {
        if (!ready) return reply(frame, ErrorCode.NOT_READY, "hello has not been welcomed yet")
        val confirmation = PendingConfirmation.fromFrame(frame)
            ?: return reply(frame, ErrorCode.MALFORMED_FRAME, "unusable confirm_request")
        // Already past its time: the PC has denied it, so there is nothing left to ask.
        if (confirmation.isExpired(nowSeconds())) return SessionOutput()
        book.add(confirmation)
        return SessionOutput(events = listOf(SessionEvent.ConfirmRequested(confirmation)))
    }

    private fun onConfirmClosed(frame: Frame): SessionOutput {
        if (!ready) return reply(frame, ErrorCode.NOT_READY, "hello has not been welcomed yet")
        val outcome = Outcome.fromWire(frame.payload.getString("outcome"))
            ?: return reply(frame, ErrorCode.MALFORMED_FRAME, "unknown outcome")
        // Event even for an id the book never held: the notification may outlive a restart of the service.
        book.close(frame.requestId)
        return SessionOutput(events = listOf(SessionEvent.ConfirmClosed(frame.requestId, outcome)))
    }

    private fun onNotify(frame: Frame): SessionOutput {
        if (!ready) return reply(frame, ErrorCode.NOT_READY, "hello has not been welcomed yet")
        val notice = Notice.fromFrame(frame) ?: return reply(frame, ErrorCode.MALFORMED_FRAME, "unknown notice kind")
        // No reply to a notify, so a resend (or a replay after reconnect) is dropped silently.
        if (!notices.firstTime(notice.requestId)) return SessionOutput()
        return SessionOutput(events = listOf(SessionEvent.NoticeReceived(notice)))
    }

    /**
     * The owner tapped Approve or Decline. A frame is produced only while this connection is welcomed and the
     * question is still open and in time; every other case is a refusal the caller reports. Never approves by itself.
     */
    fun planReply(requestId: String, approve: Boolean): ReplyPlan {
        if (!ready) return ReplyPlan.Refused(ReplyOutcome.NotConnected)
        return when (val check = book.check(requestId, nowSeconds())) {
            is ReplyCheck.Ok -> ReplyPlan.Send(check.confirmation, FrameCodec.confirmReply(requestId, approve).encode())
            ReplyCheck.Expired -> ReplyPlan.Refused(ReplyOutcome.Expired)
            ReplyCheck.Unknown -> ReplyPlan.Refused(ReplyOutcome.Unknown)
        }
    }

    /** The reply went out: the question is answered, a second tap must not send a second answer. */
    fun replySent(requestId: String) {
        book.close(requestId)
        answered.add(requestId)
        if (answered.size > 50) answered.remove(answered.first())
    }

    private fun onError(frame: Frame): SessionOutput {
        val code = ErrorCode.fromWire(frame.payload.getString("code")) ?: ErrorCode.FAILED
        // The PC refusing one of our confirm_reply frames (late, duplicate, settled elsewhere) is not a session
        // fault: only the notification needs correcting, and nothing was approved.
        if (code == ErrorCode.UNKNOWN_REQUEST && frame.requestId in answered) {
            return SessionOutput(events = listOf(SessionEvent.ReplyRejected(frame.requestId)))
        }
        val event = SessionEvent.ServerError(code, frame.payload.getString("message"))
        // An error answering our hello means the server refused this session; do not keep a half-open socket.
        return SessionOutput(events = listOf(event), close = !ready && frame.requestId == helloId)
    }

    private fun reply(to: Frame, code: ErrorCode, message: String) =
        SessionOutput(send = listOf(FrameCodec.error(to.requestId, code, message).encode()))
}
