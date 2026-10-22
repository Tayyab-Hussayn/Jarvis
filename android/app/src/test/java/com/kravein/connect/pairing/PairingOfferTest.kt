package com.kravein.connect.pairing

import com.kravein.connect.core.DeviceCredential
import com.kravein.connect.core.OfferParse
import com.kravein.connect.core.PairingOfferParser
import com.kravein.connect.network.PairResponse
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertIs
import kotlin.test.assertNull
import kotlin.test.assertTrue

class PairingOfferTest {
    private val now = 1_000_000L

    private fun offer(
        service: String = "\"kravein\"",
        v: String = "1",
        host: String = "\"192.168.1.5\"",
        port: String = "8787",
        code: String = "\"abc123\"",
        expires: String = "${now + 100}",
    ) = """{"v":$v,"service":$service,"host":$host,"port":$port,"code":$code,"expires_at":$expires}"""

    private fun rejected(raw: String) = assertIs<OfferParse.Rejected>(PairingOfferParser.parse(raw, now))

    @Test
    fun parsesAWellFormedOffer() {
        val ok = assertIs<OfferParse.Ok>(PairingOfferParser.parse(offer(), now))
        assertEquals("192.168.1.5", ok.offer.host)
        assertEquals(8787, ok.offer.port)
        assertEquals("abc123", ok.offer.code)
    }

    @Test
    fun missingFieldsAreRejected() {
        for (field in listOf("v", "service", "host", "port", "code", "expires_at")) {
            val json = org.json.JSONObject(offer()).also { it.remove(field) }
            rejected(json.toString())
        }
    }

    @Test
    fun blankNonJsonAndForeignQrCodesAreRejected() {
        rejected("")
        rejected("   ")
        rejected("https://example.com")
        rejected("[1,2]")
        rejected(offer(service = "\"other\""))
        rejected("x".repeat(3000))
    }

    @Test
    fun wrongTypesAndRangesAreRejected() {
        rejected(offer(port = "0"))
        rejected(offer(port = "70000"))
        rejected(offer(port = "\"8787\""))
        rejected(offer(code = "\"\""))
        rejected(offer(code = "123"))
        rejected(offer(v = "2"))
    }

    @Test
    fun anExpiredOfferIsRejectedWithAClearReason() {
        val result = rejected(offer(expires = "${now - 1}"))
        assertTrue(result.reason.contains("expired"))
        rejected(offer(expires = "$now"))
    }

    @Test
    fun hostsThatCouldRedirectTheRequestAreRejected() {
        for (host in listOf("evil.com/x", "a@b.com", "host name", "h?x=1", "h#f", "http://h", "")) {
            rejected(offer(host = "\"$host\""))
        }
        assertIs<OfferParse.Ok>(PairingOfferParser.parse(offer(host = "\"kravein-pc.local\""), now))
        assertIs<OfferParse.Ok>(PairingOfferParser.parse(offer(host = "\"fe80::1\""), now))
    }

    @Test
    fun manualEntryGoesThroughTheSameChecks() {
        assertIs<OfferParse.Ok>(PairingOfferParser.fromParts(" 10.0.0.2 ", "8787", " code ", now))
        assertIs<OfferParse.Rejected>(PairingOfferParser.fromParts("10.0.0.2", "abc", "code", now))
        assertIs<OfferParse.Rejected>(PairingOfferParser.fromParts("10.0.0.2/x", "8787", "code", now))
        assertIs<OfferParse.Rejected>(PairingOfferParser.fromParts("10.0.0.2", "8787", "  ", now))
    }

    @Test
    fun theOfferCodeNeverAppearsInItsStringForm() {
        val ok = assertIs<OfferParse.Ok>(PairingOfferParser.parse(offer(code = "\"s3cr3t-code\""), now))
        assertFalse(ok.offer.toString().contains("s3cr3t-code"))
    }

    // -- pair response + credential --------------------------------------------------------------

    private val parsedOffer = (PairingOfferParser.parse(offer(), now) as OfferParse.Ok).offer

    @Test
    fun parsesAPairResponseIntoACredential() {
        val credential = PairResponse.parse("""{"device_id":"ph_1","device_secret":"sek","name":"Pixel"}""", parsedOffer, "fallback")!!
        assertEquals("ph_1", credential.deviceId)
        assertEquals("Pixel", credential.deviceName)
        assertEquals("192.168.1.5", credential.host)
        assertEquals(8787, credential.port)
        assertEquals("Device ph_1:sek", credential.authorization())
    }

    @Test
    fun incompletePairResponsesAreRefused() {
        assertNull(PairResponse.parse("""{"device_id":"ph_1"}""", parsedOffer, "f"))
        assertNull(PairResponse.parse("""{"device_secret":"x"}""", parsedOffer, "f"))
        assertNull(PairResponse.parse("""{"device_id":"","device_secret":"x"}""", parsedOffer, "f"))
        assertNull(PairResponse.parse("<html>", parsedOffer, "f"))
    }

    @Test
    fun aRefusedPairingExplainsWhatToDo() {
        assertTrue(PairResponse.errorMessage(403).contains("new QR"))
        assertTrue(PairResponse.errorMessage(500).contains("500"))
    }

    @Test
    fun theDeviceSecretNeverAppearsInLogsOrStringForm() {
        val credential = DeviceCredential("ph_1", "TOP-SECRET-VALUE", "Pixel", "h", 1)
        assertFalse(credential.toString().contains("TOP-SECRET-VALUE"))
    }
}
