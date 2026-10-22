package com.kravein.connect.protocol

import com.kravein.connect.commands.DeviceAction
import org.json.JSONObject
import java.io.File
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * `kravein/phone_protocol.json` is the one definition of the phone channel. These tests fail the build when
 * the app's copy of it (commands, argument specs, frames, error codes, limits) drifts from the file.
 */
class PhoneProtocolJsonTest {
    private val spec: JSONObject = run {
        val path = System.getProperty("kravein.protocol.json")
        assertTrue(!path.isNullOrBlank(), "kravein.protocol.json system property is not set (see app/build.gradle.kts)")
        JSONObject(File(path!!).readText())
    }

    private fun fieldsOf(json: JSONObject): Map<String, Field> = json.keys().asSequence().associateWith { name ->
        val f = json.getJSONObject(name)
        Field(
            type = FieldType.entries.single { it.wire == f.getString("type") },
            required = f.getBoolean("required"),
            maxLength = if (f.has("max_length")) f.getInt("max_length") else null,
            min = if (f.has("min")) f.getInt("min") else null,
            max = if (f.has("max")) f.getInt("max") else null,
        )
    }

    @Test
    fun versionAndFrameLimitMatch() {
        assertEquals(spec.getInt("protocol"), PhoneProtocol.VERSION)
        assertEquals(spec.getInt("max_frame_bytes"), PhoneProtocol.MAX_FRAME_BYTES)
    }

    @Test
    fun errorCodesMatch() {
        val fromJson = spec.getJSONArray("error_codes").let { a -> (0 until a.length()).map { a.getString(it) } }
        assertEquals(fromJson.sorted(), ErrorCode.entries.map { it.wire }.sorted())
    }

    @Test
    fun envelopeMatches() {
        assertEquals(fieldsOf(spec.getJSONObject("envelope")), FrameSpecs.ENVELOPE)
    }

    @Test
    fun framesMatch() {
        val frames = spec.getJSONObject("frames")
        assertEquals(frames.keys().asSequence().toSet(), FrameSpecs.FRAMES.keys)
        for (name in frames.keys()) {
            val f = frames.getJSONObject(name)
            val ours = FrameSpecs.FRAMES.getValue(name)
            assertEquals(f.getString("direction"), ours.direction.wire, "direction of $name")
            assertEquals(fieldsOf(f.getJSONObject("payload")), ours.payload, "payload of $name")
        }
    }

    @Test
    fun everyCommandIsImplementedWithTheSameArguments() {
        val commands = spec.getJSONObject("commands")
        // A command in the JSON the app lacks would be advertised to nobody; one the app has that the JSON lacks
        // would be a second vocabulary. Both directions must be empty.
        assertEquals(commands.keys().asSequence().toSet(), DeviceAction.entries.map { it.wire }.toSet())
        for (action in DeviceAction.entries) {
            assertEquals(
                fieldsOf(commands.getJSONObject(action.wire).getJSONObject("args")),
                action.args,
                "args of ${action.wire}",
            )
        }
    }
}
