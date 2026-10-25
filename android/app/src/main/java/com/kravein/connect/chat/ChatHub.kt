package com.kravein.connect.chat

import android.content.Context
import com.kravein.connect.core.DeviceCredential
import com.kravein.connect.pairing.PairingStorage
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * The process-wide chat: one [ChatClient] whose socket is open while the app is visible, or while a live voice session
 * holds it open (see [LinkPolicy]), and only while paired. The activity reports visibility (`onStart`/`onStop`), the
 * shell reports the credential, the voice side reports whether it needs the socket; nothing else decides.
 * The conversation survives a rotation or a tab switch because it lives here, not in a composable.
 */
object ChatHub {
    val client = ChatClient()

    private var visible = false
    private var voiceHolds = false
    private var credential: DeviceCredential? = null

    private val _foreground = MutableStateFlow(false)

    /** Whether Kravein's own screen is in front. The floating bubble only exists while it is not. */
    val foreground: StateFlow<Boolean> = _foreground.asStateFlow()

    private val _prefill = MutableStateFlow("")

    /** Text shared from another app, waiting to land in the composer (never sent by itself). */
    val prefill: StateFlow<String> = _prefill.asStateFlow()

    /** Gives the client its encrypted store for the session id. Cheap; the keystore is only touched on first use. */
    fun install(context: Context) {
        val app = context.applicationContext
        val storage by lazy { PairingStorage(app) }
        client.sessionIds = object : SessionIdStore {
            override fun get(deviceId: String) = storage.loadSessionId(deviceId)
            override fun put(deviceId: String, sessionId: String) = storage.saveSessionId(deviceId, sessionId)
            override fun clear(deviceId: String) = storage.clearSessionId(deviceId)
        }
    }

    fun offer(text: String) {
        _prefill.value = text
    }

    fun prefillUsed() {
        _prefill.value = ""
    }

    @Synchronized
    fun onVisible(visible: Boolean) {
        this.visible = visible
        _foreground.value = visible
        sync()
    }

    @Synchronized
    fun onCredential(credential: DeviceCredential?) {
        this.credential = credential
        sync()
    }

    /** A live device voice session needs the socket even with the app out of sight. */
    @Synchronized
    fun setVoiceHolds(holds: Boolean) {
        if (voiceHolds == holds) return
        voiceHolds = holds
        sync()
    }

    private fun sync() {
        val cred = credential
        if (LinkPolicy.shouldBeOpen(cred != null, visible, voiceHolds)) client.start(cred!!) else client.stop()
    }
}
