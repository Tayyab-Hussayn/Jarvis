package com.kravein.connect.network

import com.kravein.connect.core.DeviceCredential
import com.kravein.connect.core.PairingOffer
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

sealed interface PairResult {
    data class Paired(val credential: DeviceCredential) : PairResult
    data class Failed(val message: String) : PairResult
}

object PairResponse {
    /** `{device_id, device_secret, name}` from `POST /phone/pair`; null when either credential half is missing. */
    fun parse(body: String, offer: PairingOffer, fallbackName: String): DeviceCredential? {
        val json = try {
            JSONObject(body)
        } catch (_: JSONException) {
            return null
        }
        val id = (json.opt("device_id") as? String)?.takeIf { it.isNotBlank() } ?: return null
        val secret = (json.opt("device_secret") as? String)?.takeIf { it.isNotBlank() } ?: return null
        val name = (json.opt("name") as? String)?.takeIf { it.isNotBlank() } ?: fallbackName
        return DeviceCredential(id, secret, name, offer.host, offer.port)
    }

    fun errorMessage(httpStatus: Int): String = when (httpStatus) {
        403 -> "The PC refused the pairing code: it was already used or has expired. Show a new QR code on your PC and scan it again."
        else -> "The PC answered the pairing request with an error ($httpStatus)."
    }
}

/** Redeems a pairing offer for a device credential. Blocking: call it off the main thread. */
class PairingClient(
    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .callTimeout(15, TimeUnit.SECONDS)
        .build(),
) {
    fun redeem(offer: PairingOffer, deviceName: String): PairResult {
        val url = HttpUrl.Builder().scheme("http").host(offer.host).port(offer.port).addPathSegments("phone/pair").build()
        val body = JSONObject().put("code", offer.code).put("device_name", deviceName.take(80)).toString()
        val request = Request.Builder().url(url).post(body.toRequestBody("application/json".toMediaType())).build()
        return try {
            client.newCall(request).execute().use { response ->
                // Bounded read: the reply is two short strings, anything bigger is not our server.
                val text = response.peekBody(8192).string()
                if (!response.isSuccessful) return PairResult.Failed(PairResponse.errorMessage(response.code))
                val credential = PairResponse.parse(text, offer, deviceName)
                    ?: return PairResult.Failed("The PC sent a pairing reply this app cannot use.")
                PairResult.Paired(credential)
            }
        } catch (_: IOException) {
            PairResult.Failed("Cannot reach Kravein at ${offer.host}:${offer.port}. Is the phone on the same network as the PC?")
        }
    }
}
