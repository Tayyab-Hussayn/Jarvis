package com.kravein.connect.protocol

import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

data class Frame(val type: String, val requestId: String, val payload: JSONObject) {
    fun encode(): String = JSONObject()
        .put("type", type)
        .put("request_id", requestId)
        .put("payload", payload)
        .toString()
}

/** What a command produced. Becomes a `result` frame; never carries more than the vocabulary allows. */
data class CommandResult(
    val ok: Boolean,
    val data: Map<String, Any?> = emptyMap(),
    val errorCode: ErrorCode? = null,
    val error: String? = null,
) {
    companion object {
        fun failure(code: ErrorCode, message: String) = CommandResult(false, errorCode = code, error = message)
    }
}

object FrameCodec {
    /**
     * Parse and validate one frame. [sender] is who is allowed to have sent it: the app decodes with
     * [Direction.SERVER_TO_PHONE]; tests decode what the app sends with [Direction.PHONE_TO_SERVER].
     */
    fun decode(text: String, sender: Direction = Direction.SERVER_TO_PHONE): Frame {
        if (text.toByteArray(Charsets.UTF_8).size > PhoneProtocol.MAX_FRAME_BYTES) {
            throw ProtocolException(ErrorCode.FRAME_TOO_LARGE, "frame exceeds ${PhoneProtocol.MAX_FRAME_BYTES} bytes")
        }
        val root = try {
            JSONObject(text)
        } catch (_: JSONException) {
            throw ProtocolException(ErrorCode.MALFORMED_FRAME, "frame is not a JSON object")
        }
        val requestId = (root.opt("request_id") as? String).orEmpty().take(64)
        try {
            FieldValidator.checkFields("frame", FrameSpecs.ENVELOPE, root, ErrorCode.MALFORMED_FRAME)
        } catch (e: ProtocolException) {
            throw ProtocolException(e.code, e.message.orEmpty(), requestId)
        }
        val type = root.getString("type")
        val spec = FrameSpecs.FRAMES[type]
        if (spec == null || (spec.direction != sender && spec.direction != Direction.BOTH)) {
            throw ProtocolException(ErrorCode.UNKNOWN_FRAME, "unknown frame type $type", requestId)
        }
        val payload = root.getJSONObject("payload")
        try {
            FieldValidator.checkFields(type, spec.payload, payload, ErrorCode.MALFORMED_FRAME)
        } catch (e: ProtocolException) {
            throw ProtocolException(e.code, e.message.orEmpty(), requestId)
        }
        return Frame(type, requestId, payload)
    }

    fun hello(
        requestId: String,
        capabilities: List<String>,
        deviceName: String,
        osVersion: String,
        appVersion: String,
    ): Frame {
        val payload = JSONObject()
            .put("protocol", PhoneProtocol.VERSION)
            .put("capabilities", JSONArray(capabilities))
        // The server rejects over-long optional fields outright, so cut them here instead of failing to connect.
        deviceName.trim().take(80).takeIf { it.isNotEmpty() }?.let { payload.put("device_name", it) }
        osVersion.trim().take(40).takeIf { it.isNotEmpty() }?.let { payload.put("os_version", it) }
        appVersion.trim().take(40).takeIf { it.isNotEmpty() }?.let { payload.put("app_version", it) }
        return Frame("hello", requestId, payload)
    }

    fun result(requestId: String, result: CommandResult): Frame {
        val payload = JSONObject().put("ok", result.ok)
        if (result.ok) {
            payload.put("data", toJsonObject(result.data))
        } else {
            payload.put("error_code", (result.errorCode ?: ErrorCode.FAILED).wire)
            result.error?.takeIf { it.isNotBlank() }?.let { payload.put("error", it.take(500)) }
        }
        return Frame("result", requestId, payload)
    }

    fun confirmReply(requestId: String, approve: Boolean) =
        Frame("confirm_reply", requestId, JSONObject().put("approve", approve))

    fun pong(requestId: String) = Frame("pong", requestId, JSONObject())

    fun error(requestId: String, code: ErrorCode, message: String) =
        Frame("error", requestId, JSONObject().put("code", code.wire).put("message", message.take(500)))

    fun toJsonObject(map: Map<*, *>): JSONObject {
        val json = JSONObject()
        for ((key, value) in map) json.put(key.toString(), toJsonValue(value))
        return json
    }

    private fun toJsonValue(value: Any?): Any = when (value) {
        null -> JSONObject.NULL
        is Map<*, *> -> toJsonObject(value)
        is Iterable<*> -> JSONArray(value.map { toJsonValue(it) })
        is Boolean, is Int, is Long, is String -> value
        is Number -> value.toDouble()
        else -> value.toString()
    }
}
