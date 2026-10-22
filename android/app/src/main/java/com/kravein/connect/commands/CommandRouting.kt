package com.kravein.connect.commands

/**
 * Pure routing/argument logic for [DeviceCommandHandler], kept free of Android types so JVM tests can cover it.
 * The server is untrusted input: anything not in this vocabulary is rejected before any device API is touched.
 */
enum class DeviceAction(val wire: String) {
    GET_DEVICE_INFO("get_device_info"),
    GET_BATTERY("get_battery"),
    FLASHLIGHT_ON("flashlight_on"),
    FLASHLIGHT_OFF("flashlight_off"),
    LAUNCH_APP("launch_app"),
    OPEN_URL("open_url"),
    VOLUME_GET("volume_get"),
    VOLUME_SET("volume_set"),
    ;

    companion object {
        fun fromWire(action: String): DeviceAction? {
            val key = action.lowercase()
            return entries.firstOrNull { it.wire == key }
        }
    }
}

internal val APP_NAME_KEYS = listOf("package", "package_name", "app_name", "app", "name")

/** First non-null app identifier among [APP_NAME_KEYS], trimmed; blank when none was sent. */
internal fun requestedApp(parameters: Map<String, Any?>): String =
    APP_NAME_KEYS.firstNotNullOfOrNull { key -> parameters[key]?.toString()?.trim() }.orEmpty()

internal fun normalizeAppQuery(value: String): String = value.lowercase().replace(Regex("[^a-z0-9]+"), "")

/** Volume percentage from a numeric or numeric-string parameter; null when absent or not a number. */
internal fun parseVolumeTarget(parameters: Map<String, Any?>): Int? {
    val raw = parameters["value"] ?: parameters["percentage"] ?: return null
    return when (raw) {
        is Number -> raw.toInt()
        else -> raw.toString().toIntOrNull()
    }
}

internal fun volumeLevel(percentage: Int, max: Int): Int {
    val clamped = percentage.coerceIn(0, 100)
    return ((clamped / 100f) * max).toInt().coerceIn(0, max)
}
