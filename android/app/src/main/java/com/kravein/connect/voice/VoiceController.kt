package com.kravein.connect.voice

import com.kravein.connect.chat.ChatState
import com.kravein.connect.chat.ReplayVoice
import com.kravein.connect.chat.ServerFrame
import com.kravein.connect.chat.VoicePhase

/** What the Android side must do after a voice event. The controller decides; [VoiceHub] only executes. */
sealed interface VoiceEffect {
    /** Ask the user for the microphone (rationale first), then call `onUserStart` again. */
    data object RequestMic : VoiceEffect

    /** Send `voice_start` for this phone's mic and speaker. */
    data object StartSession : VoiceEffect

    /** Send `voice_stop`. */
    data object SendStop : VoiceEffect

    /** Start the foreground service that owns the microphone and speaker. */
    data object StartService : VoiceEffect

    /** Stop it (releases the microphone, removes the notification and the bubble). */
    data object StopService : VoiceEffect

    /** Voice ended on this side without the server saying so: show [note] and close the chat's voice state. */
    data class EndedHere(val note: String) : VoiceEffect

    data class Note(val text: String) : VoiceEffect
}

/**
 * The voice session's lifecycle on the phone, as a state machine with no Android in it.
 *
 * `active` means "this phone owns the microphone for a server voice session", i.e. the foreground service should be
 * up. It is set only by the user starting voice or by a resumed session that says its audio is this device, and it
 * ends on: the user's stop, the server's `stopped`, a fatal voice error, a session the server no longer has, the
 * link staying down past [RECONNECT_LIMIT_MS] (the server stops an unattended device session after its own grace
 * period, 60 s by default, so waiting longer only keeps a microphone open for nothing). Every one of them releases
 * the microphone — the visible notification is the privacy guarantee, so there must be no way to be `active`
 * without it. Losing audio focus does NOT end it: opening YouTube takes focus, and that is exactly when the session
 * must go on.
 */
class VoiceController {
    var active = false
        private set

    var muted = false
        private set

    private var lostAt: Long? = null

    /** The user tapped the mic. [micGranted] is RECORD_AUDIO; [linkOpen] whether the chat socket is up. */
    fun onUserStart(micGranted: Boolean, linkOpen: Boolean): List<VoiceEffect> {
        if (active) return emptyList()
        if (!micGranted) return listOf(VoiceEffect.RequestMic)
        if (!linkOpen) return listOf(VoiceEffect.Note("Not connected to Kravein."))
        active = true
        muted = false
        return listOf(VoiceEffect.StartSession, VoiceEffect.StartService)
    }

    /** Stop from the Chat, the notification or the bubble. Always asks the server too: the session may be the PC's. */
    fun onUserStop(): List<VoiceEffect> {
        val was = active
        release()
        return if (was) listOf(VoiceEffect.SendStop, VoiceEffect.StopService) else listOf(VoiceEffect.SendStop)
    }

    fun toggleMute(): Boolean {
        if (active) muted = !muted
        return muted
    }

    /** A server frame, with the chat state after it was applied. */
    fun onFrame(frame: ServerFrame, state: ChatState): List<VoiceEffect> = when {
        frame is ServerFrame.VoiceState && frame.phase == "stopped" -> if (active) { release(); listOf(VoiceEffect.StopService) } else emptyList()
        // An error with no turn to blame is fatal (the reducer set ERROR): the session is dead, so the microphone must
        // not stay open under it. Asking the server to end it too is harmless; a refused start has nothing to end.
        frame is ServerFrame.VoiceState && frame.phase == "error" && state.voice.phase == VoicePhase.ERROR ->
            if (active) { release(); listOf(VoiceEffect.SendStop, VoiceEffect.StopService) } else emptyList()
        // A session the server did not resume has no voice (restart, expiry): nothing is listening to our microphone.
        frame is ServerFrame.Hello && frame.resumed != true ->
            if (active) { release(); listOf(VoiceEffect.StopService, VoiceEffect.EndedHere("Voice ended: the server restarted.")) } else emptyList()
        else -> emptyList()
    }

    /**
     * The resumed session told us what survived. [canStartService] is "the app is in front and RECORD_AUDIO is held":
     * Android only lets a microphone service start from the foreground.
     */
    fun onReplay(voice: ReplayVoice, canStartService: Boolean): List<VoiceEffect> {
        lostAt = null
        val ours = voice.active && voice.audio == "device"
        return when {
            ours && active -> emptyList() // the service never stopped; it just keeps streaming
            ours && canStartService -> { active = true; muted = false; listOf(VoiceEffect.StartService) }
            // The server is listening to a microphone we cannot give it: end it rather than leave it waiting.
            ours -> listOf(VoiceEffect.SendStop, VoiceEffect.Note("Voice stopped: the microphone could not be reopened."))
            active -> { release(); listOf(VoiceEffect.StopService) }
            else -> emptyList()
        }
    }

    /** The chat socket opened or closed. Returns the time at which a still-down link must end the session, if any. */
    fun onLink(open: Boolean, now: Long): Long? {
        if (open) { lostAt = null; return null }
        if (active && lostAt == null) lostAt = now
        return lostAt?.plus(RECONNECT_LIMIT_MS)
    }

    /** The deadline from [onLink] passed. Does nothing if the link came back meanwhile. */
    fun onDeadline(now: Long): List<VoiceEffect> {
        val lost = lostAt ?: return emptyList()
        if (!active || now - lost < RECONNECT_LIMIT_MS) return emptyList()
        release()
        return listOf(VoiceEffect.StopService, VoiceEffect.EndedHere("Voice stopped: Kravein could not be reached."))
    }

    private fun release() {
        active = false
        muted = false
        lostAt = null
    }

    companion object {
        const val RECONNECT_LIMIT_MS = 60_000L
    }
}
