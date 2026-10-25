package com.kravein.connect.chat

import com.kravein.connect.core.DeviceCredential
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * The process-wide chat: one [ChatClient] whose socket is open exactly while the app is visible AND paired. The
 * activity reports visibility (`onStart`/`onStop`), the shell reports the credential; nothing else decides.
 * The conversation survives a rotation or a tab switch because it lives here, not in a composable.
 */
object ChatHub {
    val client = ChatClient()

    private var visible = false
    private var credential: DeviceCredential? = null

    private val _prefill = MutableStateFlow("")

    /** Text shared from another app, waiting to land in the composer (never sent by itself). */
    val prefill: StateFlow<String> = _prefill.asStateFlow()

    fun offer(text: String) {
        _prefill.value = text
    }

    fun prefillUsed() {
        _prefill.value = ""
    }

    @Synchronized
    fun onVisible(visible: Boolean) {
        this.visible = visible
        sync()
    }

    @Synchronized
    fun onCredential(credential: DeviceCredential?) {
        this.credential = credential
        sync()
    }

    private fun sync() {
        val cred = credential
        if (visible && cred != null) client.start(cred) else client.stop()
    }
}
