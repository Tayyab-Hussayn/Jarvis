package com.kravein.connect.core

import java.security.MessageDigest

/**
 * The server's self-signed certificate is identified by the SHA-256 of its DER, carried in the pairing QR. This is
 * the only trust anchor for an https server: no system CA, no hostname (the server is an IP), no prompt.
 */
object CertPin {
    private val HEX64 = Regex("^[0-9a-f]{64}$")

    /**
     * Lower-case 64-hex form of [raw], or null. Accepts upper case and `AA:BB:…` / spaced forms (what `openssl` and
     * browsers print) so a hand-typed or differently formatted fingerprint still compares equal.
     */
    fun normalize(raw: String): String? {
        val cleaned = raw.trim().lowercase().replace(":", "").replace(" ", "")
        return cleaned.takeIf { HEX64.matches(it) }
    }

    fun fingerprint(der: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(der).joinToString("") { "%02x".format(it) }

    /** True only for a well-formed [pin] that equals the certificate's fingerprint. A malformed pin never matches. */
    fun matches(der: ByteArray?, pin: String?): Boolean {
        if (der == null || pin == null) return false
        val wanted = normalize(pin) ?: return false
        // Constant-time: the pin is not secret, but there is no reason to leak how many leading characters matched.
        return MessageDigest.isEqual(fingerprint(der).toByteArray(), wanted.toByteArray())
    }
}
