package com.kravein.connect.network

import com.kravein.connect.commands.CommandRouter
import com.kravein.connect.protocol.ErrorCode
import com.kravein.connect.protocol.Frame
import com.kravein.connect.protocol.FrameCodec
import com.kravein.connect.protocol.PhoneProtocol
import com.kravein.connect.protocol.ProtocolException

data class HelloInfo(val deviceName: String, val osVersion: String, val appVersion: String)

sealed interface SessionEvent {
    data class Ready(val deviceId: String, val serverName: String) : SessionEvent
    data class ServerError(val code: ErrorCode, val message: String) : SessionEvent
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
    private val newRequestId: () -> String,
) {
    private var helloId: String? = null
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

    private fun onError(frame: Frame): SessionOutput {
        val code = ErrorCode.fromWire(frame.payload.getString("code")) ?: ErrorCode.FAILED
        val event = SessionEvent.ServerError(code, frame.payload.getString("message"))
        // An error answering our hello means the server refused this session; do not keep a half-open socket.
        return SessionOutput(events = listOf(event), close = !ready && frame.requestId == helloId)
    }

    private fun reply(to: Frame, code: ErrorCode, message: String) =
        SessionOutput(send = listOf(FrameCodec.error(to.requestId, code, message).encode()))
}
