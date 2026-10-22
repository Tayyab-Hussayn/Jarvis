package com.kravein.connect.core

import org.json.JSONException
import org.json.JSONObject
import java.time.Instant

enum class ConnectionState {
    CONNECTING,
    CONNECTED,
    DISCONNECTED,
    RECONNECTING,

    /** The server refused the stored credential (revoked or unknown). Reconnecting cannot fix it. */
    NEEDS_PAIRING,
}

/** What `POST /api/phone/offer` puts in the QR code. The code is single-use and expires, so it is never stored. */
data class PairingOffer(
    val host: String,
    val port: Int,
    val code: String,
    val expiresAt: Long,
) {
    // The code authorises pairing: keep it out of logs and crash reports.
    override fun toString() = "PairingOffer(host=$host, port=$port, code=<redacted>, expiresAt=$expiresAt)"
}

sealed interface OfferParse {
    data class Ok(val offer: PairingOffer) : OfferParse
    data class Rejected(val reason: String) : OfferParse
}

object PairingOfferParser {
    private const val SERVICE = "kravein"
    private const val OFFER_VERSION = 1
    private val HOST = Regex("^[A-Za-z0-9._-]{1,253}$")
    private val IPV6 = Regex("^[0-9A-Fa-f:]{2,45}$")

    /** The manual-entry form: same rules as a scanned code, so the host is checked the same way. */
    fun fromParts(host: String, port: String, code: String, nowEpochSeconds: Long): OfferParse {
        val number = port.trim().toIntOrNull() ?: return OfferParse.Rejected("The port must be a number.")
        val raw = JSONObject()
            .put("service", SERVICE).put("v", OFFER_VERSION)
            .put("host", host.trim()).put("port", number).put("code", code.trim())
            .put("expires_at", nowEpochSeconds + 120)
            .toString()
        return parse(raw, nowEpochSeconds)
    }

    /** Every field is required; an unusable QR code is rejected here with a reason the screen can show. */
    fun parse(raw: String, nowEpochSeconds: Long): OfferParse {
        if (raw.isBlank() || raw.length > 2048) return OfferParse.Rejected("That QR code is not a Kravein pairing code.")
        val json = try {
            JSONObject(raw)
        } catch (_: JSONException) {
            return OfferParse.Rejected("That QR code is not a Kravein pairing code.")
        }
        if (json.opt("service") != SERVICE) return OfferParse.Rejected("That QR code is not a Kravein pairing code.")
        if (json.opt("v") != OFFER_VERSION) return OfferParse.Rejected("This pairing code is from a different Kravein version.")
        val host = json.opt("host") as? String
        // The host goes into a URL: refuse anything that could change where the request really goes.
        if (host == null || !(HOST.matches(host) || IPV6.matches(host))) {
            return OfferParse.Rejected("The pairing code has no usable PC address.")
        }
        val port = (json.opt("port") as? Int)?.takeIf { it in 1..65535 }
            ?: return OfferParse.Rejected("The pairing code has no usable port.")
        val code = (json.opt("code") as? String)?.takeIf { it.isNotBlank() && it.length <= 128 }
            ?: return OfferParse.Rejected("The pairing code is incomplete.")
        val expiresAt = when (val value = json.opt("expires_at")) {
            is Int -> value.toLong()
            is Long -> value
            else -> return OfferParse.Rejected("The pairing code is incomplete.")
        }
        if (expiresAt <= nowEpochSeconds) {
            return OfferParse.Rejected("This pairing code has expired. Show a new QR code on your PC and scan it again.")
        }
        return OfferParse.Ok(PairingOffer(host, port, code, expiresAt))
    }
}

data class DeviceCredential(
    val deviceId: String,
    val deviceSecret: String,
    val deviceName: String,
    val host: String,
    val port: Int,
    val pairedAt: String = Instant.now().toString(),
) {
    /** The only place the secret is placed in a header. */
    fun authorization(): String = "Device $deviceId:$deviceSecret"

    override fun toString() = "DeviceCredential(deviceId=$deviceId, deviceName=$deviceName, host=$host, port=$port, secret=<redacted>)"
}
