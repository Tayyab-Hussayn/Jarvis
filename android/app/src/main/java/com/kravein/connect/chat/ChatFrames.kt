package com.kravein.connect.chat

import org.json.JSONException
import org.json.JSONObject

/** The server's confirmation danger levels (`kravein/safety.py`), as the `/ws` protocol spells them. */
enum class Danger { SAFE, MODERATE, DESTRUCTIVE, CRITICAL;
    companion object {
        /** An unknown level reads as the most dangerous one: a card must never look milder than the server meant. */
        fun parse(value: String?): Danger = entries.firstOrNull { it.name == value } ?: CRITICAL
    }
}

enum class ConfirmOutcome { APPROVED, DENIED, EXPIRED;
    companion object {
        fun parse(value: String?): ConfirmOutcome? = when (value) {
            "approved" -> APPROVED
            "denied" -> DENIED
            "expired" -> EXPIRED
            else -> null
        }
    }
}

/**
 * Frames the server sends on `/ws` that the chat acts on (`web/lib/protocol.ts` is the source of truth).
 * Frames the chat has no use for (`voice_state`, `routine_event`, ...) parse to [Ignored], not to an error.
 */
sealed interface ServerFrame {
    data class Hello(val assistantName: String, val model: String) : ServerFrame
    data class TurnStart(val id: String) : ServerFrame
    data class Thinking(val id: String, val text: String) : ServerFrame
    data class ToolCall(val id: String, val callId: String, val name: String, val display: String, val args: String) : ServerFrame
    data class ToolResult(val id: String, val callId: String, val result: String) : ServerFrame
    data class ConfirmRequest(
        val id: String, val confirmId: String, val action: String, val detail: String, val danger: Danger, val timeoutSeconds: Int,
    ) : ServerFrame
    data class ConfirmExpired(val id: String, val confirmId: String) : ServerFrame
    data class ConfirmResolved(val confirmId: String, val outcome: ConfirmOutcome, val by: String) : ServerFrame
    data class Answer(val id: String, val text: String) : ServerFrame
    data class TurnEnd(val id: String, val iterations: Int?, val error: String?, val cancelled: Boolean) : ServerFrame
    data class Cancelled(val id: String?) : ServerFrame
    data class Error(val id: String?, val message: String) : ServerFrame
    data object ResetOk : ServerFrame
    data object Pong : ServerFrame

    /** A well-formed frame of a kind the chat does not show. */
    data object Ignored : ServerFrame

    /** Not JSON, no `t`, or a known frame with a missing/mistyped field. Dropped; never tears the socket down. */
    data object Malformed : ServerFrame
}

object ChatFrames {
    /** One frame is a chat message or a tool result; anything larger is not our server. */
    const val MAX_FRAME_CHARS = 1_000_000

    /** The server sets no cap on an `ask`; this one is ours so a runaway paste cannot become a multi-MB frame. */
    const val MAX_ASK_CHARS = 8000

    fun parse(text: String): ServerFrame {
        if (text.length > MAX_FRAME_CHARS) return ServerFrame.Malformed
        val json = try {
            JSONObject(text)
        } catch (_: JSONException) {
            return ServerFrame.Malformed
        }
        val t = json.opt("t") as? String ?: return ServerFrame.Malformed
        fun str(key: String): String? = json.opt(key) as? String
        // Optional fields: absent or null is fine, a wrong type is not.
        fun optStr(key: String): String? = if (!json.has(key) || json.isNull(key)) null else str(key)
        return when (t) {
            "hello" -> ServerFrame.Hello(str("assistant_name") ?: "Kravein", str("model") ?: "")
            "turn_start" -> ServerFrame.TurnStart(str("id") ?: return ServerFrame.Malformed)
            "thinking" -> ServerFrame.Thinking(str("id") ?: return ServerFrame.Malformed, str("text") ?: return ServerFrame.Malformed)
            "tool_call" -> ServerFrame.ToolCall(
                id = str("id") ?: return ServerFrame.Malformed,
                callId = str("call_id") ?: return ServerFrame.Malformed,
                name = str("name") ?: return ServerFrame.Malformed,
                display = str("display") ?: "",
                args = (json.opt("args") as? JSONObject)?.toString() ?: "",
            )
            "tool_result" -> ServerFrame.ToolResult(
                str("id") ?: return ServerFrame.Malformed,
                str("call_id") ?: return ServerFrame.Malformed,
                str("result") ?: return ServerFrame.Malformed,
            )
            "confirm_request" -> ServerFrame.ConfirmRequest(
                id = str("id") ?: "",
                confirmId = str("confirm_id") ?: return ServerFrame.Malformed,
                action = str("action") ?: return ServerFrame.Malformed,
                detail = str("detail") ?: "",
                danger = Danger.parse(str("danger")),
                timeoutSeconds = (json.opt("timeout") as? Number)?.toInt()?.coerceIn(1, 3600) ?: 120,
            )
            "confirm_expired" -> ServerFrame.ConfirmExpired(str("id") ?: "", str("confirm_id") ?: return ServerFrame.Malformed)
            "confirm_resolved" -> ServerFrame.ConfirmResolved(
                confirmId = str("confirm_id") ?: return ServerFrame.Malformed,
                outcome = ConfirmOutcome.parse(str("outcome")) ?: return ServerFrame.Malformed,
                by = str("by") ?: "",
            )
            "answer" -> ServerFrame.Answer(str("id") ?: return ServerFrame.Malformed, str("text") ?: return ServerFrame.Malformed)
            "turn_end" -> ServerFrame.TurnEnd(
                id = str("id") ?: return ServerFrame.Malformed,
                iterations = (json.opt("iterations") as? Number)?.toInt(),
                error = optStr("error")?.takeIf { it.isNotBlank() },
                cancelled = json.opt("cancelled") == true,
            )
            "cancelled" -> ServerFrame.Cancelled(optStr("id"))
            "error" -> ServerFrame.Error(optStr("id"), str("message") ?: "Something went wrong.")
            "reset_ok" -> ServerFrame.ResetOk
            "pong" -> ServerFrame.Pong
            else -> ServerFrame.Ignored
        }
    }

    fun ask(id: String, text: String): String = JSONObject().put("t", "ask").put("id", id).put("text", text).toString()
    fun confirmReply(confirmId: String, approve: Boolean): String =
        JSONObject().put("t", "confirm_reply").put("confirm_id", confirmId).put("approve", approve).toString()
    fun cancel(): String = """{"t":"cancel"}"""
    fun reset(): String = """{"t":"reset"}"""
    fun ping(): String = """{"t":"ping"}"""
}
