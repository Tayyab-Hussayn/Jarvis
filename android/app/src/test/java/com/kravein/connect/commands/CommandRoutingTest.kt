package com.kravein.connect.commands

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class CommandRoutingTest {
    @Test
    fun everyDeclaredActionRoundTripsThroughItsWireName() {
        for (action in DeviceAction.entries) {
            assertEquals(action, DeviceAction.fromWire(action.wire))
        }
    }

    @Test
    fun wireNamesAreCaseInsensitive() {
        assertEquals(DeviceAction.GET_BATTERY, DeviceAction.fromWire("GET_Battery"))
    }

    @Test
    fun unknownOrCapabilityStyleNamesAreRejected() {
        // "battery" is a capability, not an action; "shell" must never route to anything.
        for (name in listOf("battery", "shell", "", " get_battery", "get_battery;rm", "volume")) {
            assertNull(DeviceAction.fromWire(name), "should reject '$name'")
        }
    }

    @Test
    fun requestedAppUsesFirstKeyInPriorityOrderAndTrims() {
        assertEquals("com.x", requestedApp(mapOf("name" to "Foo", "package" to "  com.x ")))
        assertEquals("Foo", requestedApp(mapOf("name" to "Foo")))
        assertEquals("", requestedApp(emptyMap()))
    }

    @Test
    fun normalizeAppQueryStripsEverythingButLettersAndDigits() {
        assertEquals("youtubemusic", normalizeAppQuery("YouTube Music!"))
        assertEquals("comandroidchrome", normalizeAppQuery("com.android.chrome"))
    }

    @Test
    fun parseVolumeTargetAcceptsNumbersAndNumericStrings() {
        assertEquals(40, parseVolumeTarget(mapOf("value" to 40)))
        assertEquals(40, parseVolumeTarget(mapOf("value" to 40.9)))
        assertEquals(70, parseVolumeTarget(mapOf("percentage" to "70")))
        assertNull(parseVolumeTarget(mapOf("value" to "loud")))
        assertNull(parseVolumeTarget(emptyMap()))
    }

    @Test
    fun volumeLevelClampsToTheDeviceRange() {
        assertEquals(15, volumeLevel(100, 15))
        assertEquals(15, volumeLevel(500, 15))
        assertEquals(0, volumeLevel(-20, 15))
        assertEquals(7, volumeLevel(50, 15))
    }
}
