package com.kravein.connect.voice

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import com.kravein.connect.chat.ChatClient
import com.kravein.connect.chat.ChatHub
import com.kravein.connect.chat.ChatState
import com.kravein.connect.chat.ServerFrame
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit

/**
 * Runs [VoiceController]'s decisions. Every event is handled on one thread, in order, with no locks held while it
 * calls out: the chat client and [ChatHub] take their own locks, and a lock-order cycle between them and a hub lock
 * would hang the app.
 */
object VoiceHub : ChatClient.VoiceSink {
    /** What the screens, the notification and the bubble show about the local side of voice. */
    data class Status(val active: Boolean = false, val muted: Boolean = false, val reconnecting: Boolean = false)

    private val controller = VoiceController()
    private val worker = Executors.newSingleThreadScheduledExecutor { r -> Thread(r, "kravein-voice").apply { isDaemon = true } }
    private var deadline: ScheduledFuture<*>? = null
    private var linkOpen = false

    private val _status = MutableStateFlow(Status())
    val status: StateFlow<Status> = _status.asStateFlow()

    private val _micRationale = MutableStateFlow(0)

    /** Ticks when the mic is needed but not granted; the Chat shows its reason and then asks (a counter, so twice fires twice). */
    val micRationale: StateFlow<Int> = _micRationale.asStateFlow()

    @Volatile private var app: Context? = null

    /** Set by the service while it runs; receives model audio. */
    @Volatile var audio: VoiceAudio? = null

    fun install(context: Context) {
        app = context.applicationContext
        ChatHub.client.voiceSink = this
    }

    // -- user intents (any thread) -------------------------------------------------------------------------------

    fun start() = run { apply(controller.onUserStart(micGranted(), ChatHub.client.isConnected())) }

    fun stop() = run { apply(controller.onUserStop()) }

    fun toggleMute() = run {
        controller.toggleMute()
        publish()
    }

    /** The system's answer to the microphone request the Chat made. */
    fun onMicResult(granted: Boolean) = run {
        if (granted) apply(controller.onUserStart(true, ChatHub.client.isConnected()))
        else ChatHub.client.voiceEnded("Voice needs the microphone. You can allow it in the app's permissions.")
    }

    // -- ChatClient.VoiceSink (OkHttp threads) --------------------------------------------------------------------

    override fun onFrame(frame: ServerFrame, state: ChatState) {
        run {
            when (frame) {
                is ServerFrame.Replay -> apply(controller.onReplay(frame.voice, canStartService()))
                ServerFrame.AudioClear -> audio?.clear()
                else -> apply(controller.onFrame(frame, state))
            }
        }
    }

    override fun onAudio(pcm: ByteArray) {
        audio?.play(pcm)
    }

    override fun onLink(open: Boolean) = run {
        linkOpen = open
        deadline?.cancel(false)
        val at = controller.onLink(open, System.currentTimeMillis())
        if (at != null) {
            deadline = worker.schedule(
                { apply(controller.onDeadline(System.currentTimeMillis())); publish() },
                (at - System.currentTimeMillis()).coerceAtLeast(0), TimeUnit.MILLISECONDS,
            )
        }
        publish()
    }

    // ------------------------------------------------------------------------------------------------------------

    private fun run(block: () -> Unit) {
        worker.execute {
            try {
                block()
                publish()
            } catch (e: Exception) {
                // Never take the worker down: a dead worker would leave a microphone service nobody can stop.
                android.util.Log.w("KraveinVoice", "voice event failed: ${e.javaClass.simpleName}")
            }
        }
    }

    private fun micGranted(): Boolean {
        val ctx = app ?: return false
        return ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
    }

    /** Android lets a microphone service start only from the foreground, so a resume needs the app in front. */
    private fun canStartService(): Boolean = micGranted() && ChatHub.foreground.value

    private fun publish() {
        _status.value = Status(controller.active, controller.muted, reconnecting = controller.active && !linkOpen)
        ChatHub.setVoiceHolds(controller.active)
    }

    private fun apply(effects: List<VoiceEffect>) {
        val ctx = app ?: return
        for (effect in effects) when (effect) {
            VoiceEffect.RequestMic -> _micRationale.value += 1
            VoiceEffect.StartSession -> if (!ChatHub.client.startVoice()) {
                // The link dropped between the check and the send: undo, never leave a service without a session.
                apply(controller.onUserStop().filter { it != VoiceEffect.SendStop })
                ChatHub.client.voiceEnded("Not connected to Kravein.")
            }
            VoiceEffect.SendStop -> ChatHub.client.stopVoice()
            VoiceEffect.StartService -> VoiceService.start(ctx)
            VoiceEffect.StopService -> VoiceService.stop(ctx)
            is VoiceEffect.EndedHere -> ChatHub.client.voiceEnded(effect.note)
            is VoiceEffect.Note -> ChatHub.client.voiceEnded(effect.text)
        }
    }
}
