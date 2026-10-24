package com.kravein.connect.tile

import com.kravein.connect.core.ConnectionState
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class TileModelTest {
    @Test
    fun `only a live connection lights the tile`() {
        for (state in ConnectionState.entries) {
            assertEquals(state == ConnectionState.CONNECTED, TileModel.from(true, state).active, state.name)
        }
        assertEquals("Connected", TileModel.from(true, ConnectionState.CONNECTED).subtitle)
    }

    @Test
    fun `unpaired phone says so whatever the connection state claims`() {
        val model = TileModel.from(false, ConnectionState.CONNECTED)
        assertFalse(model.active)
        assertEquals("Not paired", model.subtitle)
    }

    @Test
    fun `reconnecting and refused credentials are told apart`() {
        assertEquals("Connecting", TileModel.from(true, ConnectionState.RECONNECTING).subtitle)
        assertEquals("Pair again", TileModel.from(true, ConnectionState.NEEDS_PAIRING).subtitle)
        assertTrue(TileModel.from(true, ConnectionState.DISCONNECTED).subtitle.startsWith("Not"))
    }
}
