package com.kravein.connect.pairing

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull

class PairingPayloadParserTest {
    private val valid = """
        {"service":"kravein","host":"192.168.1.5","port":8787,"pairing_token":"tok","pairing_code":"123456","expires":300,"created_at":"2026-10-22T10:00:00Z"}
    """.trimIndent()

    @Test
    fun parsesAWellFormedOffer() {
        val offer = assertNotNull(PairingPayloadParser.parse(valid))
        assertEquals("192.168.1.5", offer.host)
        assertEquals(8787, offer.port)
        assertEquals("tok", offer.pairingToken)
        assertEquals("123456", offer.pairingCode)
        assertEquals(300, offer.expiresInSeconds)
    }

    @Test
    fun blankAndNonJsonPayloadsAreRejected() {
        assertNull(PairingPayloadParser.parse(""))
        assertNull(PairingPayloadParser.parse("   "))
        assertNull(PairingPayloadParser.parse("not json"))
        assertNull(PairingPayloadParser.parse("[1,2,3]"))
    }

    @Test
    fun offerSurvivesAJsonRoundTrip() {
        val offer = assertNotNull(PairingPayloadParser.parse(valid))
        assertEquals(offer, PairingPayloadParser.parse(offer.toJson().toString()))
    }
}
