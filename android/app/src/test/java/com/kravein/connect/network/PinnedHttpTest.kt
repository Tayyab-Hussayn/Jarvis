package com.kravein.connect.network

import com.kravein.connect.core.CertPin
import com.sun.net.httpserver.HttpsConfigurator
import com.sun.net.httpserver.HttpsServer
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.security.KeyStore
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertSame
import kotlin.test.assertTrue

/** A real TLS server with a self-signed certificate, like `kravein serve --lan`, to prove the pin is what decides. */
class PinnedHttpTest {
    private fun keystore(name: String): KeyStore =
        KeyStore.getInstance("PKCS12").apply {
            PinnedHttpTest::class.java.getResourceAsStream("/$name")!!.use { load(it, "changeit".toCharArray()) }
        }

    private fun pinOf(name: String): String {
        val ks = keystore(name)
        return CertPin.fingerprint((ks.getCertificate(ks.aliases().nextElement()) as X509Certificate).encoded)
    }

    private val server: HttpsServer = HttpsServer.create(InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0).also { https ->
        val keys = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()).apply { init(keystore("pin-test.p12"), "changeit".toCharArray()) }
        https.httpsConfigurator = HttpsConfigurator(SSLContext.getInstance("TLS").apply { init(keys.keyManagers, null, null) })
        https.createContext("/") { exchange ->
            val body = "hello".toByteArray()
            exchange.sendResponseHeaders(200, body.size.toLong())
            exchange.responseBody.use { it.write(body) }
        }
        https.start()
    }
    private val url = "https://127.0.0.1:${server.address.port}/"

    @AfterTest
    fun stop() = server.stop(0)

    private fun get(client: OkHttpClient) = client.newCall(Request.Builder().url(url).build()).execute().use { it.code to it.body!!.string() }

    @Test
    fun theServerWithTheRightFingerprintIsReachedWithoutAnyCaOrHostnameCheck() {
        assertEquals(200 to "hello", get(PinnedHttp.clientFor("https", pinOf("pin-test.p12"))))
    }

    @Test
    fun theFingerprintMayBeWrittenUpperCaseWithColons() {
        val pin = pinOf("pin-test.p12").uppercase().chunked(2).joinToString(":")
        assertEquals(200, get(PinnedHttp.clientFor("https", pin)).first)
    }

    @Test
    fun aDifferentCertificateIsAHardFailure() {
        val e = assertFailsWith<IOException> { get(PinnedHttp.clientFor("https", pinOf("pin-other.p12"))) }
        assertTrue(TlsFailures.isPinMismatch(e), "expected a pin mismatch, got $e")
    }

    @Test
    fun anUnrelatedValidLookingPinFails() {
        assertFailsWith<IOException> { get(PinnedHttp.clientFor("https", "0".repeat(64))) }
    }

    @Test
    fun theSameServerIsNotTrustedByTheSharedPlainClient() {
        assertFailsWith<IOException> { get(Http.client) }
    }

    @Test
    fun httpKeepsTheSharedClientUnchanged() {
        assertSame(Http.client, PinnedHttp.clientFor("http", null))
    }

    @Test
    fun httpsWithoutAUsablePinIsRefusedUpFront() {
        assertFailsWith<IllegalArgumentException> { PinnedHttp.clientFor("https", null) }
        assertFailsWith<IllegalArgumentException> { PinnedHttp.clientFor("https", "abc") }
    }

    @Test
    fun theTrustManagerChecksOnlyTheLeaf() {
        val ks = keystore("pin-test.p12")
        val cert = ks.getCertificate(ks.aliases().nextElement()) as X509Certificate
        val trust = PinTrustManager(pinOf("pin-test.p12"))
        trust.checkServerTrusted(arrayOf(cert), "ECDHE_ECDSA")
        assertFailsWith<CertificateException> { trust.checkServerTrusted(emptyArray(), "ECDHE_ECDSA") }
        assertFailsWith<CertificateException> { trust.checkServerTrusted(null, "ECDHE_ECDSA") }
        assertFailsWith<CertificateException> { PinTrustManager(pinOf("pin-other.p12")).checkServerTrusted(arrayOf(cert), "x") }
        assertFailsWith<CertificateException> { trust.checkClientTrusted(arrayOf(cert), "x") }
        assertEquals(0, trust.acceptedIssuers.size)
    }

    @Test
    fun failureClassifiers() {
        assertTrue(TlsFailures.looksLikePlainHttpToTlsServer(java.io.EOFException("unexpected end of stream")))
        assertTrue(TlsFailures.looksLikePlainHttpToTlsServer(java.net.SocketException("Connection reset")))
        assertTrue(TlsFailures.looksLikePlainHttpToTlsServer(IOException("wrapped", java.net.ProtocolException("Unexpected status line"))))
        assertFalse(TlsFailures.looksLikePlainHttpToTlsServer(java.net.ConnectException("Connection refused")))
        assertFalse(TlsFailures.looksLikePlainHttpToTlsServer(java.net.SocketTimeoutException("timeout")))
        assertFalse(TlsFailures.looksLikePlainHttpToTlsServer(null))
        assertFalse(TlsFailures.isPinMismatch(IOException("plain")))
    }
}
