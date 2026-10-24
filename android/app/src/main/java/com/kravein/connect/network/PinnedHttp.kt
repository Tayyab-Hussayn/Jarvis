package com.kravein.connect.network

import com.kravein.connect.core.CertPin
import okhttp3.OkHttpClient
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.ConcurrentHashMap
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

/** The server presented a certificate other than the pinned one. Never retried, never offered as "continue anyway". */
class PinMismatchException(message: String) : CertificateException(message)

object TlsFailures {
    private fun chain(t: Throwable?): Sequence<Throwable> = generateSequence(t) { it.cause }.take(8)

    fun isPinMismatch(t: Throwable?): Boolean = chain(t).any { it is PinMismatchException }

    /**
     * What a plain-http request to a TLS-only port looks like: the server drops the connection or answers with
     * TLS bytes instead of a status line. A downed server instead refuses or times out, which is not matched here.
     */
    fun looksLikePlainHttpToTlsServer(t: Throwable?): Boolean = chain(t).any {
        it is java.io.EOFException ||
            it is java.net.ProtocolException ||
            (it is java.net.SocketException && it.message.orEmpty().contains("reset", ignoreCase = true))
    }

    const val MISMATCH_MESSAGE =
        "This PC's certificate is not the one in the pairing QR code. If you did not just reinstall Kravein, " +
            "someone else may be answering at this address. Scan a new pairing QR code to pair again."
    const val RE_PAIR_FOR_TLS_MESSAGE =
        "The PC now uses a secure connection. Scan a new pairing QR code to pair this phone again."
}

/** Accepts a server only when its leaf certificate's DER SHA-256 equals the pin; nothing else is ever trusted. */
class PinTrustManager(pin: String) : X509TrustManager {
    private val pin = CertPin.normalize(pin) ?: throw IllegalArgumentException("not a SHA-256 fingerprint")

    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        val leaf = chain?.firstOrNull() ?: throw CertificateException("the server sent no certificate")
        // Expiry is deliberately not checked: the pin is the identity, and a self-signed certificate that outlives
        // its dates is still the one the owner scanned.
        if (!CertPin.matches(leaf.encoded, pin)) throw PinMismatchException("the server certificate is not the pinned one")
    }

    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        throw CertificateException("this app never accepts client certificates")
    }

    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
}

/**
 * One OkHttp client per paired server. http servers (loopback, `--no-tls`) get the shared plain client unchanged;
 * https servers get a client whose only trust is the pin and whose hostname check is replaced by it. The relaxed
 * hostname verifier lives only on that client, which cannot complete a handshake with any other certificate.
 */
object PinnedHttp {
    private val cache = ConcurrentHashMap<String, OkHttpClient>()

    fun clientFor(scheme: String, pin: String?, base: OkHttpClient = Http.client): OkHttpClient {
        if (scheme != "https") return base
        val normalized = CertPin.normalize(pin.orEmpty()) ?: throw IllegalArgumentException("https needs a certificate pin")
        return cache.getOrPut(normalized) { build(base, PinTrustManager(normalized)) }
    }

    internal fun build(base: OkHttpClient, trust: X509TrustManager): OkHttpClient {
        val context = SSLContext.getInstance("TLS")
        context.init(null, arrayOf(trust), SecureRandom())
        return base.newBuilder()
            .sslSocketFactory(context.socketFactory, trust)
            .hostnameVerifier { _, _ -> true }
            .build()
    }
}
