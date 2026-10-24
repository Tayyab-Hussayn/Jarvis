package com.kravein.connect.pairing

import com.kravein.connect.core.CertPin
import com.kravein.connect.core.DeviceCredential
import com.kravein.connect.core.OfferParse
import com.kravein.connect.core.PairingOfferParser
import com.kravein.connect.network.PairResponse
import com.kravein.connect.web.NavigationPolicy
import com.kravein.connect.web.SslErrorPolicy
import org.json.JSONObject
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertIs
import kotlin.test.assertNull
import kotlin.test.assertTrue

class PairingOfferTlsTest {
    private val now = 1_000L
    private val pin = "ab".repeat(32)

    private fun offer(vararg extra: Pair<String, Any?>): String {
        val json = JSONObject()
            .put("service", "kravein").put("v", 1).put("host", "192.168.1.5").put("port", 8787)
            .put("code", "123456").put("expires_at", now + 100)
        extra.forEach { (k, v) -> json.put(k, v) }
        return json.toString()
    }

    private fun parsed(raw: String) = PairingOfferParser.parse(raw, now)

    @Test
    fun anHttpsOfferCarriesItsPin() {
        val ok = assertIs<OfferParse.Ok>(parsed(offer("scheme" to "https", "cert_sha256" to pin)))
        assertEquals("https", ok.offer.scheme)
        assertEquals(pin, ok.offer.certSha256)
    }

    @Test
    fun theFingerprintIsNormalisedToLowerCaseHex() {
        val colons = pin.uppercase().chunked(2).joinToString(":")
        assertEquals(pin, assertIs<OfferParse.Ok>(parsed(offer("scheme" to "https", "cert_sha256" to colons))).offer.certSha256)
    }

    @Test
    fun httpsNeedsAWellFormedFingerprint() {
        assertIs<OfferParse.Rejected>(parsed(offer("scheme" to "https")))
        assertIs<OfferParse.Rejected>(parsed(offer("scheme" to "https", "cert_sha256" to "abc")))
        assertIs<OfferParse.Rejected>(parsed(offer("scheme" to "https", "cert_sha256" to "zz".repeat(32))))
        assertIs<OfferParse.Rejected>(parsed(offer("scheme" to "https", "cert_sha256" to 12345)))
        assertIs<OfferParse.Rejected>(parsed(offer("scheme" to "https", "cert_sha256" to pin + "00")))
    }

    @Test
    fun unknownOrWrongTypedSchemesAreRejected() {
        assertIs<OfferParse.Rejected>(parsed(offer("scheme" to "ftp")))
        assertIs<OfferParse.Rejected>(parsed(offer("scheme" to "HTTPS", "cert_sha256" to pin)))
        assertIs<OfferParse.Rejected>(parsed(offer("scheme" to 5)))
    }

    @Test
    fun anHttpOfferStillWorksWithOrWithoutTheFields() {
        val plain = assertIs<OfferParse.Ok>(parsed(offer())).offer
        assertEquals("http", plain.scheme)
        assertNull(plain.certSha256)
        val explicit = assertIs<OfferParse.Ok>(parsed(offer("scheme" to "http"))).offer
        assertEquals("http", explicit.scheme)
        // A pin on an http offer protects nothing and is dropped rather than trusted.
        assertNull(assertIs<OfferParse.Ok>(parsed(offer("scheme" to "http", "cert_sha256" to pin))).offer.certSha256)
    }

    @Test
    fun manualEntryIsStillPlainHttp() {
        val ok = assertIs<OfferParse.Ok>(PairingOfferParser.fromParts("192.168.1.5", "8787", "123456", now))
        assertEquals("http", ok.offer.scheme)
    }

    @Test
    fun pairingCarriesSchemeAndPinIntoTheCredential() {
        val offer = assertIs<OfferParse.Ok>(parsed(offer("scheme" to "https", "cert_sha256" to pin))).offer
        val credential = PairResponse.parse("""{"device_id":"d","device_secret":"s","name":"n"}""", offer, "Pixel")!!
        assertEquals("https", credential.scheme)
        assertEquals(pin, credential.certSha256)
        assertTrue(credential.toString().contains("secret=<redacted>"))
    }

    @Test
    fun aCredentialWithoutTlsFieldsIsPlainHttp() {
        val old = DeviceCredential("d", "s", "n", "h", 1)
        assertEquals("http", old.scheme)
        assertNull(old.certSha256)
    }

    // --- fingerprints -------------------------------------------------------------------------------------------

    @Test
    fun fingerprintMatchesTheKnownSha256() {
        assertEquals("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", CertPin.fingerprint(ByteArray(0)))
    }

    @Test
    fun comparisonIgnoresCaseAndSeparators() {
        val der = "kravein".toByteArray()
        val fp = CertPin.fingerprint(der)
        assertTrue(CertPin.matches(der, fp))
        assertTrue(CertPin.matches(der, fp.uppercase()))
        assertTrue(CertPin.matches(der, fp.chunked(2).joinToString(":")))
        assertTrue(CertPin.matches(der, " $fp "))
    }

    @Test
    fun aWrongOrMalformedPinNeverMatches() {
        val der = "kravein".toByteArray()
        assertFalse(CertPin.matches(der, "0".repeat(64)))
        assertFalse(CertPin.matches(der, CertPin.fingerprint(der).dropLast(2)))
        assertFalse(CertPin.matches(der, ""))
        assertFalse(CertPin.matches(der, null))
        assertFalse(CertPin.matches(null, CertPin.fingerprint(der)))
        assertFalse(CertPin.matches("other".toByteArray(), CertPin.fingerprint(der)))
    }

    // --- WebView ------------------------------------------------------------------------------------------------

    @Test
    fun httpsOriginsAndDefaultPortsFollowTheScheme() {
        val policy = NavigationPolicy("192.168.1.5", 8787, "https")
        assertEquals("https://192.168.1.5:8787", policy.origin())
        assertTrue(policy.isPairedOrigin("https://192.168.1.5:8787/x"))
        assertFalse(policy.isPairedOrigin("http://192.168.1.5:8787/x"))
        assertTrue(NavigationPolicy("pc", 443, "https").isPairedOrigin("https://pc/"))
        assertFalse(NavigationPolicy("pc", 80, "https").isPairedOrigin("https://pc/"))
        assertTrue(NavigationPolicy("pc", 80).isPairedOrigin("http://pc/"))
    }

    @Test
    fun sslErrorsProceedOnlyForThePinnedCertificateOnThePairedOrigin() {
        val der = "cert".toByteArray()
        val fp = CertPin.fingerprint(der)
        val policy = NavigationPolicy("192.168.1.5", 8787, "https")
        val url = "https://192.168.1.5:8787/"
        assertTrue(SslErrorPolicy.mayProceed(policy, fp, url, der))
        assertFalse(SslErrorPolicy.mayProceed(policy, fp, url, "other".toByteArray()))
        assertFalse(SslErrorPolicy.mayProceed(policy, fp, url, null))
        assertFalse(SslErrorPolicy.mayProceed(policy, null, url, der))
        assertFalse(SslErrorPolicy.mayProceed(policy, fp, "https://evil.example:8787/", der))
        assertFalse(SslErrorPolicy.mayProceed(policy, fp, null, der))
        assertFalse(SslErrorPolicy.mayProceed(null, fp, url, der))
    }
}
