package com.kravein.connect.commands

import com.kravein.connect.protocol.CommandResult
import com.kravein.connect.protocol.ErrorCode
import com.kravein.connect.protocol.Field
import com.kravein.connect.protocol.FieldType
import com.kravein.connect.protocol.FieldValidator
import com.kravein.connect.protocol.ProtocolException
import org.json.JSONObject

/**
 * The commands this app implements, with the argument specs from `kravein/phone_protocol.json`. A capability
 * name IS a command name, so the hello frame advertises exactly these. Anything else is refused before any
 * device API is touched: the server is untrusted input.
 */
enum class DeviceAction(val wire: String, val args: Map<String, Field> = emptyMap()) {
    GET_DEVICE_INFO("get_device_info"),
    GET_BATTERY("get_battery"),
    FLASHLIGHT_ON("flashlight_on"),
    FLASHLIGHT_OFF("flashlight_off"),
    VOLUME_GET("volume_get"),
    VOLUME_SET("volume_set", mapOf("percent" to Field(FieldType.INTEGER, true, min = 0, max = 100))),
    LAUNCH_APP("launch_app", mapOf("app" to Field(FieldType.STRING, true, maxLength = 200))),
    OPEN_URL("open_url", mapOf("url" to Field(FieldType.URL, true, maxLength = 2000))),
    ;

    companion object {
        /** Exact match only: no case folding, no trimming. */
        fun fromWire(action: String): DeviceAction? = entries.firstOrNull { it.wire == action }
    }
}

/** The Android side of the commands. Receives only arguments the router already validated. */
interface DeviceBackend {
    fun execute(action: DeviceAction, args: JSONObject): CommandResult
}

class CommandRouter(private val backend: DeviceBackend) {
    val capabilities: List<String> = DeviceAction.entries.map { it.wire }

    fun route(command: String, args: JSONObject): CommandResult {
        val action = DeviceAction.fromWire(command)
            ?: return CommandResult.failure(ErrorCode.UNKNOWN_COMMAND, "Unsupported command: ${command.take(60)}")
        try {
            FieldValidator.checkFields(action.wire, action.args, args, ErrorCode.INVALID_ARGS)
        } catch (e: ProtocolException) {
            return CommandResult.failure(e.code, e.message.orEmpty())
        }
        return try {
            backend.execute(action, args)
        } catch (e: Exception) {
            // A device API throwing must still produce a result, or the server waits out its timeout.
            CommandResult.failure(ErrorCode.FAILED, e.message ?: "${action.wire} failed")
        }
    }
}

data class AppEntry(val label: String, val packageName: String)

sealed interface AppMatch {
    data class Found(val packageName: String) : AppMatch
    data object NotFound : AppMatch
    data class Ambiguous(val candidates: List<AppEntry>) : AppMatch
}

internal fun normalizeAppQuery(value: String): String = value.lowercase().replace(Regex("[^a-z0-9]+"), "")

/**
 * Resolve a spoken app name to one package. An exact label/package match wins; otherwise a substring match.
 * Several candidates are refused rather than guessed: launching the first of "Google" / "Google Maps" is how
 * the wrong app opens.
 */
fun resolveApp(requested: String, apps: List<AppEntry>): AppMatch {
    val wanted = normalizeAppQuery(requested)
    if (wanted.isEmpty()) return AppMatch.NotFound
    fun pick(matches: List<AppEntry>): AppMatch? {
        val distinct = matches.distinctBy { it.packageName }
        return when (distinct.size) {
            0 -> null
            1 -> AppMatch.Found(distinct[0].packageName)
            else -> AppMatch.Ambiguous(distinct)
        }
    }
    pick(apps.filter { normalizeAppQuery(it.label) == wanted || normalizeAppQuery(it.packageName) == wanted })
        ?.let { return it }
    pick(
        apps.filter {
            normalizeAppQuery(it.label).contains(wanted) || normalizeAppQuery(it.packageName).contains(wanted)
        },
    )?.let { return it }
    return AppMatch.NotFound
}

internal fun volumeLevel(percentage: Int, max: Int): Int {
    val clamped = percentage.coerceIn(0, 100)
    return ((clamped / 100f) * max).toInt().coerceIn(0, max)
}
