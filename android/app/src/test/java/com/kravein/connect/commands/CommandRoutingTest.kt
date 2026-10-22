package com.kravein.connect.commands

import com.kravein.connect.protocol.CommandResult
import com.kravein.connect.protocol.ErrorCode
import org.json.JSONObject
import java.io.File
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

private class RecordingBackend(private val boom: Boolean = false) : DeviceBackend {
    val calls = mutableListOf<Pair<DeviceAction, JSONObject>>()

    override fun execute(action: DeviceAction, args: JSONObject): CommandResult {
        if (boom) error("camera exploded")
        calls += action to args
        return CommandResult(true, mapOf("ran" to action.wire))
    }
}

class CommandRoutingTest {
    private val backend = RecordingBackend()
    private val router = CommandRouter(backend)

    private fun args(json: String = "{}") = JSONObject(json)

    /** Valid arguments for each command, taken from the protocol so a new command forces a new line here. */
    private val validArgs = mapOf(
        "get_device_info" to "{}",
        "get_battery" to "{}",
        "flashlight_on" to "{}",
        "flashlight_off" to "{}",
        "volume_get" to "{}",
        "volume_set" to """{"percent":40}""",
        "launch_app" to """{"app":"Maps"}""",
        "open_url" to """{"url":"https://example.com/a"}""",
    )

    @Test
    fun everyCommandInTheProtocolRoutesToTheBackend() {
        val protocol = JSONObject(File(System.getProperty("kravein.protocol.json")!!).readText()).getJSONObject("commands")
        for (name in protocol.keys()) {
            val result = router.route(name, args(validArgs.getValue(name)))
            assertTrue(result.ok, "$name should run")
            assertEquals(name, result.data["ran"])
        }
        assertEquals(protocol.length(), backend.calls.size)
    }

    @Test
    fun capabilitiesAreExactlyTheCommandNames() {
        assertEquals(DeviceAction.entries.map { it.wire }, router.capabilities)
    }

    @Test
    fun unknownCommandsNeverReachTheBackend() {
        // "battery" is the old capability name; case variants and padding must not slip through either.
        for (name in listOf("battery", "shell", "", "GET_BATTERY", " get_battery", "get_battery;rm", "volume")) {
            val result = router.route(name, args())
            assertEquals(false, result.ok, "should refuse '$name'")
            assertEquals(ErrorCode.UNKNOWN_COMMAND, result.errorCode)
        }
        assertTrue(backend.calls.isEmpty())
    }

    @Test
    fun invalidArgumentsAreRefusedBeforeTheBackend() {
        val bad = listOf(
            "volume_set" to "{}",
            "volume_set" to """{"percent":101}""",
            "volume_set" to """{"percent":-1}""",
            "volume_set" to """{"percent":"40"}""",
            "volume_set" to """{"percent":40,"extra":1}""",
            "launch_app" to """{"app":""" + "\"" + "a".repeat(201) + "\"}",
            "launch_app" to """{"package":"x"}""",
            "open_url" to """{"url":"file:///etc/passwd"}""",
            "open_url" to """{"url":"javascript:alert(1)"}""",
            "open_url" to """{"url":"intent://x#Intent;end"}""",
            "open_url" to """{"url":"http://"}""",
            "get_battery" to """{"x":1}""",
        )
        for ((name, json) in bad) {
            val result = router.route(name, args(json))
            assertEquals(false, result.ok, "$name $json")
            assertEquals(ErrorCode.INVALID_ARGS, result.errorCode, "$name $json")
        }
        assertTrue(backend.calls.isEmpty())
    }

    @Test
    fun aThrowingBackendStillAnswers() {
        val result = CommandRouter(RecordingBackend(boom = true)).route("flashlight_on", args())
        assertEquals(false, result.ok)
        assertEquals(ErrorCode.FAILED, result.errorCode)
        assertEquals("camera exploded", result.error)
    }

    @Test
    fun wireNamesAreExact() {
        assertNull(DeviceAction.fromWire("Get_Battery"))
        assertEquals(DeviceAction.GET_BATTERY, DeviceAction.fromWire("get_battery"))
    }

    private val apps = listOf(
        AppEntry("Google", "com.google.android.googlequicksearchbox"),
        AppEntry("Google Maps", "com.google.android.apps.maps"),
        AppEntry("YouTube Music", "com.google.android.apps.youtube.music"),
        AppEntry("Chrome", "com.android.chrome"),
    )

    @Test
    fun resolveAppPrefersAnExactLabelOverASubstring() {
        assertEquals(AppMatch.Found("com.google.android.apps.maps"), resolveApp("google maps", apps))
        assertEquals(AppMatch.Found("com.google.android.googlequicksearchbox"), resolveApp("Google", apps))
    }

    @Test
    fun resolveAppAcceptsAPackageName() {
        assertEquals(AppMatch.Found("com.android.chrome"), resolveApp("com.android.chrome", apps))
    }

    @Test
    fun resolveAppRefusesToGuessBetweenSeveralMatches() {
        val result = resolveApp("youtube", apps + AppEntry("YouTube", "com.google.android.youtube") + AppEntry("YouTube Kids", "com.google.android.apps.youtube.kids"))
        // "YouTube" is an exact label, so it wins; a name that is only a substring of two apps is ambiguous.
        assertEquals(AppMatch.Found("com.google.android.youtube"), result)
        val ambiguous = resolveApp("youtube", apps + AppEntry("YouTube Kids", "com.google.android.apps.youtube.kids"))
        assertTrue(ambiguous is AppMatch.Ambiguous && ambiguous.candidates.size == 2)
    }

    @Test
    fun resolveAppReportsNotFoundForBlankOrUnknownNames() {
        assertEquals(AppMatch.NotFound, resolveApp("!!!", apps))
        assertEquals(AppMatch.NotFound, resolveApp("spotify", apps))
        // A long request is not matched backwards against short labels ("open chrome please" != "Chrome").
        assertEquals(AppMatch.NotFound, resolveApp("open chrome please", apps))
    }

    @Test
    fun volumeLevelClampsToTheDeviceRange() {
        assertEquals(15, volumeLevel(100, 15))
        assertEquals(15, volumeLevel(500, 15))
        assertEquals(0, volumeLevel(-20, 15))
        assertEquals(7, volumeLevel(50, 15))
    }
}
