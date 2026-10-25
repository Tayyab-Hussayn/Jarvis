package com.kravein.connect.voice

import com.kravein.connect.chat.ChatMessage
import com.kravein.connect.chat.VoicePhase

/** Words and small derivations shared by the Chat strip, the notification and the bubble. Pure. */
object VoiceView {
    /** What the session is doing, in a few words. [muted] and [reconnecting] win over the server's phase. */
    fun label(phase: VoicePhase, muted: Boolean, reconnecting: Boolean, stopping: Boolean = false): String = when {
        stopping -> "Stopping…"
        reconnecting -> "Reconnecting…"
        muted -> "Muted"
        else -> when (phase) {
            VoicePhase.STARTING -> "Starting…"
            VoicePhase.IDLE -> "Ready"
            VoicePhase.LISTENING -> "Listening"
            VoicePhase.THINKING -> "Thinking"
            VoicePhase.SPEAKING -> "Speaking"
            VoicePhase.ERROR -> "Voice error"
            VoicePhase.OFF -> "Off"
        }
    }

    /** The last thing said in the conversation, one line, for the bubble panel. Empty when nothing was said yet. */
    fun lastLine(messages: List<ChatMessage>, max: Int = 140): String {
        for (m in messages.asReversed()) {
            val text = when (m) {
                is ChatMessage.User -> m.text
                is ChatMessage.Assistant -> m.text
            }.trim()
            if (text.isNotEmpty()) {
                val line = text.lineSequence().firstOrNull { it.isNotBlank() }?.trim() ?: continue
                return if (line.length > max) line.take(max - 1) + "…" else line
            }
        }
        return ""
    }

    /**
     * What a muted microphone sends: silence of the same length, not nothing. The server's voice activity detection
     * then still sees a steady stream (and ends a half-spoken sentence) instead of waiting on a stalled one.
     */
    fun frameFor(frame: ByteArray, length: Int, muted: Boolean): ByteArray {
        if (!muted) return frame
        java.util.Arrays.fill(frame, 0, length, 0)
        return frame
    }
}
