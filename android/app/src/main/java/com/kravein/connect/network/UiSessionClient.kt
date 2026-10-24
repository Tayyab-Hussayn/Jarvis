package com.kravein.connect.network

import com.kravein.connect.core.DeviceCredential
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException

/** A short-lived web-UI token. It lives in memory only; the device credential is the one durable secret. */
class UiToken(val value: String, val expiresAt: Long, val ttlSeconds: Long) {
    override fun toString() = "UiToken(value=<redacted>, expiresAt=$expiresAt)"
}

sealed interface UiSessionResult {
    data class Granted(val token: UiToken) : UiSessionResult

    /** 401: the server no longer knows this device (revoked). Asking again cannot help; the app must be paired again. */
    data object Refused : UiSessionResult

    data class Unreachable(val message: String) : UiSessionResult
    data class Failed(val message: String) : UiSessionResult
}

object UiSessionResponse {
    private const val MAX_TOKEN = 512

    /** `{token, expires_at, ttl_seconds}`; null when the token is missing or oversized, or the numbers are not numbers. */
    fun parse(body: String): UiToken? {
        val json = try {
            JSONObject(body)
        } catch (_: JSONException) {
            return null
        }
        val token = (json.opt("token") as? String)?.takeIf { it.isNotBlank() && it.length <= MAX_TOKEN } ?: return null
        val expiresAt = (json.opt("expires_at") as? Number)?.toLong()?.takeIf { it > 0 } ?: return null
        val ttl = (json.opt("ttl_seconds") as? Number)?.toLong()?.takeIf { it > 0 } ?: return null
        return UiToken(token, expiresAt, ttl)
    }
}

/**
 * `POST /phone/ui-session` with the device credential. Like the socket it sends no `Origin` header (OkHttp never
 * adds one), which the phone endpoints require. Blocking: call it off the main thread.
 */
class UiSessionClient(
    private val clientFor: (String, String?) -> OkHttpClient = { scheme, pin -> PinnedHttp.clientFor(scheme, pin) },
) {
    fun fetch(credential: DeviceCredential): UiSessionResult {
        val url = HttpUrl.Builder().scheme(credential.scheme).host(credential.host).port(credential.port)
            .addPathSegments("phone/ui-session").build()
        val request = Request.Builder().url(url)
            .header("Authorization", credential.authorization())
            .post("".toRequestBody())
            .build()
        return try {
            clientFor(credential.scheme, credential.certSha256).newCall(request).execute().use { response ->
                when {
                    response.code == 401 -> UiSessionResult.Refused
                    !response.isSuccessful -> UiSessionResult.Failed("The PC answered with an error (${response.code}).")
                    else -> {
                        // Bounded read: the reply is one short token, anything bigger is not our server.
                        val token = UiSessionResponse.parse(response.peekBody(4096).string())
                        if (token == null) UiSessionResult.Failed("The PC sent a reply this app cannot use.")
                        else UiSessionResult.Granted(token)
                    }
                }
            }
        } catch (e: IOException) {
            if (TlsFailures.isPinMismatch(e)) return UiSessionResult.Failed(TlsFailures.MISMATCH_MESSAGE)
            UiSessionResult.Unreachable("Cannot reach Kravein at ${credential.host}:${credential.port}.")
        }
    }
}
