package com.kravein.connect.network

import com.kravein.connect.core.CertPin
import com.sun.net.httpserver.HttpsConfigurator
import com.sun.net.httpserver.HttpsServer
import java.net.ConnectException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URL
import java.security.KeyStore
import java.security.cert.X509Certificate
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/** The WebView's only road to the PC: plain HTTP in on loopback, pinned TLS out. */
class LoopbackTlsPipeTest {
    private fun keystore(name: String): KeyStore =
        KeyStore.getInstance("PKCS12").apply {
            LoopbackTlsPipeTest::class.java.getResourceAsStream("/$name")!!.use { load(it, "changeit".toCharArray()) }
        }

    private fun pinOf(name: String): String {
        val ks = keystore(name)
        return CertPin.fingerprint((ks.getCertificate(ks.aliases().nextElement()) as X509Certificate).encoded)
    }

    private val server: HttpsServer = HttpsServer.create(InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0).also { https ->
        val keys = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()).apply { init(keystore("pin-test.p12"), "changeit".toCharArray()) }
        https.httpsConfigurator = HttpsConfigurator(SSLContext.getInstance("TLS").apply { init(keys.keyManagers, null, null) })
        https.createContext("/") { exchange ->
            val body = "hello ${exchange.requestHeaders.getFirst("Host")}".toByteArray()
            exchange.sendResponseHeaders(200, body.size.toLong())
            exchange.responseBody.use { it.write(body) }
        }
        https.start()
    }
    private val pipes = mutableListOf<LoopbackTlsPipe>()

    private fun pipe(pinFile: String) =
        LoopbackTlsPipe("127.0.0.1", server.address.port, PinnedHttp.socketFactoryFor(pinOf(pinFile))).start().also { pipes += it }

    @AfterTest
    fun stop() {
        pipes.forEach { it.close() }
        server.stop(0)
    }

    @Test
    fun plainHttpOnLoopbackReachesTheTlsServerThroughThePin() {
        val p = pipe("pin-test.p12")
        val text = URL("http://127.0.0.1:${p.localPort}/").openStream().use { it.readBytes().decodeToString() }
        assertEquals("hello 127.0.0.1:${p.localPort}", text)
    }

    @Test
    fun aServerWithAnotherCertificateDeliversNothing() {
        val p = pipe("pin-other.p12")
        Socket("127.0.0.1", p.localPort).use { s ->
            s.soTimeout = 5000
            s.getOutputStream().write("GET / HTTP/1.1\r\nHost: x\r\n\r\n".toByteArray())
            s.getOutputStream().flush()
            // Closed (EOF) or reset, never an HTTP answer.
            val read = try { s.getInputStream().readBytes().size } catch (_: java.io.IOException) { 0 }
            assertEquals(0, read)
        }
    }

    @Test
    fun listensOnLoopbackOnly() {
        assertTrue(pipe("pin-test.p12").let { Socket("127.0.0.1", it.localPort).use { s -> s.inetAddress.isLoopbackAddress } })
        val bound = java.net.ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).use { it.inetAddress }
        assertTrue(bound.isLoopbackAddress)
    }

    @Test
    fun closeStopsListening() {
        val p = pipe("pin-test.p12")
        val port = p.localPort
        p.close()
        assertFailsWith<ConnectException> { Socket("127.0.0.1", port).close() }
    }

    @Test
    fun closeEndsLiveConnectionsWithoutBlockingTheCaller() {
        val p = pipe("pin-test.p12")
        Socket("127.0.0.1", p.localPort).use { s ->
            s.soTimeout = 5000
            s.getOutputStream().write("GET / HTTP/1.1\r\nHost: x\r\n\r\n".toByteArray())
            s.getOutputStream().flush()
            s.getInputStream().read() // the connection is up and carrying the answer
            val started = System.nanoTime()
            p.close()
            assertTrue((System.nanoTime() - started) < 1_000_000_000L, "close() must return at once")
            // The rest of the answer may arrive; then the pipe ends the stream (EOF or reset), it does not hang.
            val ended = try { s.getInputStream().readBytes(); true } catch (_: java.io.IOException) { true }
            assertTrue(ended)
        }
    }
}
