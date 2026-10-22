package com.kravein.connect.pairing

import android.content.Context
import android.os.Build
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.kravein.connect.core.DeviceCredential
import org.json.JSONObject

/** The device credential, in encrypted preferences only. Nothing else about pairing is persisted. */
class PairingStorage(context: Context) {
    private val prefs = EncryptedSharedPreferences.create(
        context,
        "kravein_connect_secure",
        MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    init {
        // Entries from the old gateway protocol: a credential the new server never issued, and a saved offer
        // (an offer is single-use and short-lived, so keeping one is pointless).
        prefs.edit().remove("device_credential").remove("last_pairing_offer").apply()
    }

    fun saveCredential(credential: DeviceCredential) {
        prefs.edit()
            .putString(
                KEY,
                JSONObject()
                    .put("device_id", credential.deviceId)
                    .put("device_secret", credential.deviceSecret)
                    .put("device_name", credential.deviceName)
                    .put("host", credential.host)
                    .put("port", credential.port)
                    .put("paired_at", credential.pairedAt)
                    .toString(),
            )
            .apply()
    }

    fun loadCredential(): DeviceCredential? {
        val raw = prefs.getString(KEY, null) ?: return null
        return try {
            val json = JSONObject(raw)
            DeviceCredential(
                deviceId = json.getString("device_id"),
                deviceSecret = json.getString("device_secret"),
                deviceName = json.optString("device_name", Build.MODEL),
                host = json.getString("host"),
                port = json.getInt("port"),
                pairedAt = json.optString("paired_at"),
            )
        } catch (_: Exception) {
            null
        }
    }

    fun clearCredential() {
        prefs.edit().remove(KEY).apply()
    }

    private companion object {
        const val KEY = "phone_credential"
    }
}
