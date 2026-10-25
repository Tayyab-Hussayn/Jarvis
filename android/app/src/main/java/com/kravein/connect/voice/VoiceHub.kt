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

    private const val HEALTH_PERIOD_MS = 5_000L

    private val controller = VoiceController()
    private val watchdog = VoiceWatchdog()
    private var ticker: ScheduledFuture<*>? = null
    private val worker = Executors.newSingleThreadScheduledExecutor { r -> Thread(r, "kravein-voice").apply { isDaemon = true } }
    private var deadline: ScheduledFuture<*>? = null
    private var linkOpen = false

    private val _status = MutableStateFlow(Status())
    val status: StateFlow<Status> = _status.asStateFlow()

    private val _micRationale = MutableStateFlow(0)

    /** Ticks when the mic is needed but not granted; the Chat shows its reason and then asks (a counter, so twice fires twice). */
    val micRationale: StateFlow<Int> = _micRationale.asStateFlow()

    @Volatile private var app: Context? = null

    /** The one player this phone may have; receives model audio. Replacing it releases the old one first. */
    private val player = SinglePlayer<VoiceAudio> { it.stop() }

    /** Empty the slot, stopping whatever was playing. Called before a service builds a new player. */
    fun releaseAudio() = player.replace(null)

    /** The service built [audio]; it becomes the only player. */
    fun attachAudio(audio: VoiceAudio) = player.replace(audio)

    /** The service that owned [audio] is going away; a successor's player is left alone. */
    fun detachAudio(audio: VoiceAudio) = player.clearIf(audio)

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

    // -- health (audio thread, service) ---------------------------------------------------------------------------

    /** One captured microphone frame. Called on the capture thread, so it only touches the watchdog. */
    fun onMic(speech: Boolean) = watchdog.onMicFrame(System.currentTimeMillis(), speech)

    /** Android's own report that it is (or no longer is) silencing our capture. */
    fun onMicSilenced(silenced: Boolean) = watchdog.onMicSilenced(System.currentTimeMillis(), silenced)

    // -- ChatClient.VoiceSink (OkHttp threads) --------------------------------------------------------------------

    override fun onFrame(frame: ServerFrame, state: ChatState) {
        // Anything the voice side says proves the session is alive; the watchdog only fires when speech goes unanswered.
        if (frame is ServerFrame.VoiceState || frame == ServerFrame.AudioClear) watchdog.onServerActivity()
        run {
            when (frame) {
                is ServerFrame.Replay -> apply(controller.onReplay(frame.voice, canStartService()))
                ServerFrame.AudioClear -> player.player?.clear()
                else -> apply(controller.onFrame(frame, state))
            }
        }
    }

    override fun onAudio(pcm: ByteArray) {
        watchdog.onServerActivity()
        player.player?.play(pcm)
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
        // The health check runs only while a session does: no timer for an idle app.
        if (controller.active && ticker == null) {
            watchdog.start(System.currentTimeMillis())
            ticker = worker.scheduleWithFixedDelay({ run { checkHealth() } }, HEALTH_PERIOD_MS, HEALTH_PERIOD_MS, TimeUnit.MILLISECONDS)
        } else if (!controller.active) {
            ticker?.cancel(false)
            ticker = null
        }
    }

    /** A session that says "Listening" while nothing listens ends here, visibly, with the reason. */
    private fun checkHealth() {
        if (!controller.active) return
        val verdict = watchdog.check(System.currentTimeMillis())
        if (verdict is VoiceWatchdog.Verdict.Failed) apply(controller.onFailure(verdict.reason.note))
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
            VoiceEffect.StartService -> { VoiceNotice.clear(ctx); VoiceService.start(ctx) }
            VoiceEffect.StopService -> VoiceService.stop(ctx)
            is VoiceEffect.EndedHere -> {
                ChatHub.client.voiceEnded(effect.note)
                // With the app in front the strip already says it; out of sight, a notification is the only trace.
                if (!ChatHub.foreground.value) VoiceNotice.postStopped(ctx, effect.note)
            }
            is VoiceEffect.Note -> ChatHub.client.voiceEnded(effect.text)
        }
    }
}
