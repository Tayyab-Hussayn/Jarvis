package com.kravein.connect.protocol

import com.kravein.connect.commands.DeviceAction
import org.json.JSONArray
import org.json.JSONObject
import java.net.URI
import java.net.URISyntaxException

/**
 * The phone side of `kravein/phone_protocol.json`. That file is the one definition of the channel; everything
 * here is a typed copy of it, and `PhoneProtocolJsonTest` fails when the two stop agreeing.
 */
object PhoneProtocol {
    const val VERSION = 1
    const val MAX_FRAME_BYTES = 65536
}

enum class ErrorCode(val wire: String) {
    MALFORMED_FRAME("malformed_frame"),
    UNKNOWN_FRAME("unknown_frame"),
    UNKNOWN_COMMAND("unknown_command"),
    UNKNOWN_CAPABILITY("unknown_capability"),
    INVALID_ARGS("invalid_args"),
    PROTOCOL_MISMATCH("protocol_mismatch"),
    NOT_READY("not_ready"),
    UNKNOWN_REQUEST("unknown_request"),
    FRAME_TOO_LARGE("frame_too_large"),
    PERMISSION_DENIED("permission_denied"),
    UNAVAILABLE("unavailable"),
    FAILED("failed"),
    ;

    companion object {
        fun fromWire(value: String): ErrorCode? = entries.firstOrNull { it.wire == value }
    }
}

enum class FieldType(val wire: String) {
    STRING("string"),
    INTEGER("integer"),
    BOOLEAN("boolean"),
    OBJECT("object"),
    COMMAND("command"),
    LIST_OF_COMMANDS("list_of_commands"),
    ERROR_CODE("error_code"),
    URL("url"),
}

data class Field(
    val type: FieldType,
    val required: Boolean,
    val maxLength: Int? = null,
    val min: Int? = null,
    val max: Int? = null,
)

enum class Direction(val wire: String) {
    PHONE_TO_SERVER("phone_to_server"),
    SERVER_TO_PHONE("server_to_phone"),
    BOTH("both"),
}

data class FrameSpec(val direction: Direction, val payload: Map<String, Field>)

object FrameSpecs {
    val ENVELOPE: Map<String, Field> = mapOf(
        "type" to Field(FieldType.STRING, true),
        "request_id" to Field(FieldType.STRING, true, maxLength = 64),
        "payload" to Field(FieldType.OBJECT, true),
    )

    val FRAMES: Map<String, FrameSpec> = mapOf(
        "hello" to FrameSpec(
            Direction.PHONE_TO_SERVER,
            mapOf(
                "protocol" to Field(FieldType.INTEGER, true),
                "capabilities" to Field(FieldType.LIST_OF_COMMANDS, true),
                "device_name" to Field(FieldType.STRING, false, maxLength = 80),
                "os_version" to Field(FieldType.STRING, false, maxLength = 40),
                "app_version" to Field(FieldType.STRING, false, maxLength = 40),
            ),
        ),
        "welcome" to FrameSpec(
            Direction.SERVER_TO_PHONE,
            mapOf(
                "protocol" to Field(FieldType.INTEGER, true),
                "device_id" to Field(FieldType.STRING, true),
                "server_name" to Field(FieldType.STRING, true),
            ),
        ),
        "execute" to FrameSpec(
            Direction.SERVER_TO_PHONE,
            mapOf(
                "command" to Field(FieldType.COMMAND, true),
                "args" to Field(FieldType.OBJECT, true),
            ),
        ),
        "result" to FrameSpec(
            Direction.PHONE_TO_SERVER,
            mapOf(
                "ok" to Field(FieldType.BOOLEAN, true),
                "data" to Field(FieldType.OBJECT, false),
                "error_code" to Field(FieldType.ERROR_CODE, false),
                "error" to Field(FieldType.STRING, false, maxLength = 500),
            ),
        ),
        // Confirmations: the PC asks, the phone answers with the same request_id, the PC says when it is settled.
        // danger/outcome are plain strings here; their allowed values are checked in confirm/Confirmations.kt.
        "confirm_request" to FrameSpec(
            Direction.SERVER_TO_PHONE,
            mapOf(
                "action" to Field(FieldType.STRING, true, maxLength = 300),
                "detail" to Field(FieldType.STRING, true, maxLength = 1000),
                "danger" to Field(FieldType.STRING, true),
                "expires_at" to Field(FieldType.INTEGER, true),
            ),
        ),
        "confirm_reply" to FrameSpec(
            Direction.PHONE_TO_SERVER,
            mapOf("approve" to Field(FieldType.BOOLEAN, true)),
        ),
        "confirm_closed" to FrameSpec(
            Direction.SERVER_TO_PHONE,
            mapOf("outcome" to Field(FieldType.STRING, true)),
        ),
        // One-way: the PC tells the owner about a reminder or finished job. `kind` is checked in notice/Notice.kt.
        "notify" to FrameSpec(
            Direction.SERVER_TO_PHONE,
            mapOf(
                "title" to Field(FieldType.STRING, true, maxLength = 120),
                "body" to Field(FieldType.STRING, true, maxLength = 1000),
                "kind" to Field(FieldType.STRING, true),
                "at" to Field(FieldType.INTEGER, true),
            ),
        ),
        "ping" to FrameSpec(Direction.BOTH, emptyMap()),
        "pong" to FrameSpec(Direction.BOTH, emptyMap()),
        "error" to FrameSpec(
            Direction.BOTH,
            mapOf(
                "code" to Field(FieldType.ERROR_CODE, true),
                "message" to Field(FieldType.STRING, true, maxLength = 500),
            ),
        ),
    )
}

class ProtocolException(val code: ErrorCode, message: String, val requestId: String = "") : Exception(message)

/** Mirrors the server's `_check_fields`: strict, so a missing required field and an unknown field are both errors. */
object FieldValidator {
    fun checkFields(where: String, specs: Map<String, Field>, data: JSONObject, code: ErrorCode) {
        val extra = mutableListOf<String>()
        for (key in data.keys()) {
            if (key !in specs) extra += key
        }
        if (extra.isNotEmpty()) throw ProtocolException(code, "$where: unexpected fields ${extra.sorted()}")
        for ((name, spec) in specs) {
            if (!data.has(name)) {
                if (spec.required) throw ProtocolException(code, "$where: missing $name")
                continue
            }
            checkValue("$where.$name", spec, data.get(name), code)
        }
    }

    fun checkValue(name: String, spec: Field, value: Any, code: ErrorCode) {
        val ok = when (spec.type) {
            FieldType.STRING -> value is String
            FieldType.INTEGER -> value is Int || value is Long
            FieldType.BOOLEAN -> value is Boolean
            FieldType.OBJECT -> value is JSONObject
            FieldType.COMMAND -> {
                if (value !is String || DeviceAction.fromWire(value) == null) {
                    throw ProtocolException(ErrorCode.UNKNOWN_COMMAND, "unknown command $value")
                }
                true
            }
            FieldType.LIST_OF_COMMANDS -> {
                val items = (value as? JSONArray)?.let { array -> (0 until array.length()).map { array.get(it) } }
                if (items == null || items.any { it !is String }) {
                    throw ProtocolException(code, "$name must be a list of command names")
                }
                val unknown = items.filter { DeviceAction.fromWire(it as String) == null }
                if (unknown.isNotEmpty()) {
                    throw ProtocolException(ErrorCode.UNKNOWN_CAPABILITY, "unknown capabilities $unknown")
                }
                true
            }
            FieldType.ERROR_CODE -> value is String && ErrorCode.fromWire(value) != null
            FieldType.URL -> value is String && isHttpUrl(value)
        }
        if (!ok) throw ProtocolException(code, "$name must be ${spec.type.wire}")
        if (value is String && spec.maxLength != null && value.codePointCount(0, value.length) > spec.maxLength) {
            throw ProtocolException(code, "$name is longer than ${spec.maxLength}")
        }
        if (spec.type == FieldType.INTEGER) {
            val number = (value as Number).toLong()
            if (spec.min != null && number < spec.min) throw ProtocolException(code, "$name is below ${spec.min}")
            if (spec.max != null && number > spec.max) throw ProtocolException(code, "$name is above ${spec.max}")
        }
    }

    /** Only http(s) with a host: `file:`, `intent:` and `javascript:` must never reach an ACTION_VIEW intent. */
    fun isHttpUrl(value: String): Boolean = try {
        val uri = URI(value)
        uri.scheme?.lowercase() in setOf("http", "https") && !uri.rawAuthority.isNullOrEmpty()
    } catch (_: URISyntaxException) {
        false
    }
}
