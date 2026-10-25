package com.kravein.connect

import android.app.Application
import com.kravein.connect.chat.ChatHub
import com.kravein.connect.voice.VoiceHub

class KraveinConnectApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        // Both only store a reference; the keystore and the audio stack are touched when first needed.
        ChatHub.install(this)
        VoiceHub.install(this)
    }
}
