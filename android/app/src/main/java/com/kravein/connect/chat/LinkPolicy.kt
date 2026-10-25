package com.kravein.connect.chat

/** When the chat socket is open. One rule, in one place, so the foreground and the voice service cannot disagree. */
object LinkPolicy {
    /**
     * Open while the app is visible, and while a voice session holds it open (the microphone keeps running after the
     * user leaves; a socket closed under it would end the conversation). Never without a pairing.
     */
    fun shouldBeOpen(paired: Boolean, appVisible: Boolean, voiceHolds: Boolean): Boolean =
        paired && (appVisible || voiceHolds)
}
