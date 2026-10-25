package com.kravein.connect.voice

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.IBinder
import android.provider.Settings
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.kravein.connect.MainActivity
import com.kravein.connect.R
import com.kravein.connect.bubble.BubbleOverlay
import com.kravein.connect.bubble.BubblePolicy
import com.kravein.connect.chat.ChatHub
import com.kravein.connect.chat.VoicePhase
import com.kravein.connect.confirm.ConfirmationNotifier
import com.kravein.connect.core.UiPrefs
import com.kravein.connect.ui.theme.KraveinTokens
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.conflate
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.launch

/**
 * Owns the microphone and speaker while a device voice session is live, and shows why: an ongoing notification with
 * Mute and Stop. That notification is the privacy guarantee — the service, the microphone and the notification live
 * and die together — so every exit path goes through [shutdown].
 *
 * Foreground type `microphone` only. `mediaPlayback` is not needed: the microphone service already keeps the process
 * and its audio alive, and Kravein's speech is an assistant stream, not media.
 */
class VoiceService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var audio: VoiceAudio? = null
    private var focus: AudioFocusRequest? = null
    private var bubble: BubbleOverlay? = null
    private var begun = false
    private var recordingCallback: AudioManager.AudioRecordingCallback? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_MUTE -> VoiceHub.toggleMute()
            ACTION_STOP -> {
                VoiceHub.stop()
                // The hub stops this service itself; ending here too covers a service the hub no longer knows about.
                if (!VoiceHub.status.value.active) stopSelf()
            }
            else -> begin()
        }
        return START_NOT_STICKY
    }

    private fun begin() {
        if (begun) return
        begun = true
        createChannel()
        try {
            ServiceCompat.startForeground(
                this, NOTIFICATION_ID, notification("Starting…", muted = false),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE,
            )
        } catch (e: Exception) {
            // Android refused a microphone service (started from the background, or without the permission).
            fail("Voice could not start: Android did not allow a microphone service.")
            return
        }
        // Whatever played before is stopped before anything new is built: one speaker per phone, always.
        VoiceHub.releaseAudio()
        val voice = VoiceAudio(
            send = { frame, length -> ChatHub.client.sendAudio(frame, length) },
            live = { ChatHub.client.state.value.voice.phase != VoicePhase.OFF },
            muted = { VoiceHub.status.value.muted },
            onMic = VoiceHub::onMic,
        )
        try {
            voice.start()
        } catch (e: Exception) {
            voice.stop()
            fail("Voice could not open the microphone.")
            return
        }
        audio = voice
        VoiceHub.attachAudio(voice)
        requestFocus()
        watchSilencing(voice)
        render()
    }

    /**
     * Android can keep delivering frames while replacing them with zeros (another app took the microphone, a call, an
     * OEM background rule). The frames look fine, so only the platform's own report reveals it; the watchdog ends the
     * session if it lasts. API 29+, where `isClientSilenced` exists.
     */
    private fun watchSilencing(voice: VoiceAudio) {
        if (android.os.Build.VERSION.SDK_INT < android.os.Build.VERSION_CODES.Q) return
        val manager = getSystemService(AudioManager::class.java)
        val callback = object : AudioManager.AudioRecordingCallback() {
            override fun onRecordingConfigChanged(configs: MutableList<android.media.AudioRecordingConfiguration>) {
                val mine = configs.firstOrNull { it.clientAudioSessionId == voice.sessionId } ?: return
                VoiceHub.onMicSilenced(mine.isClientSilenced)
            }
        }
        manager.registerAudioRecordingCallback(callback, null)
        recordingCallback = callback
    }

    private fun fail(note: String) {
        ChatHub.client.voiceEnded(note)
        VoiceHub.stop()
        shutdown()
        stopSelf()
    }

    /** Re-render only when something the notification or the bubble shows actually changed: no timers, no polling. */
    private fun render() {
        val prefs = UiPrefs(this)
        scope.launch {
            combine(VoiceHub.status, ChatHub.client.state, ChatHub.foreground) { status, chat, front ->
                Snapshot(
                    active = status.active,
                    muted = status.muted,
                    reconnecting = status.reconnecting,
                    phase = chat.voice.phase,
                    stopping = chat.voice.isStopping,
                    front = front,
                    // The last line only matters to the bubble, and the bubble only exists with the app out of front.
                    line = if (front) "" else VoiceView.lastLine(chat.messages),
                )
            }.distinctUntilChanged().conflate().collect { s ->
                val label = VoiceView.label(s.phase, s.muted, s.reconnecting, s.stopping)
                getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification(label, s.muted))
                val show = BubblePolicy.shouldShow(prefs.bubbleEnabled, Settings.canDrawOverlays(this@VoiceService), s.active, s.front)
                if (show) {
                    val overlay = bubble ?: BubbleOverlay(
                        this@VoiceService,
                        onToggleMute = { VoiceHub.toggleMute() },
                        onStop = { VoiceHub.stop() },
                        onOpen = { openApp() },
                    ).also { bubble = it }
                    overlay.update(s.phase, s.muted, s.reconnecting, s.line, label)
                } else {
                    bubble?.remove()
                    bubble = null
                }
            }
        }
    }

    private data class Snapshot(
        val active: Boolean, val muted: Boolean, val reconnecting: Boolean,
        val phase: VoicePhase, val stopping: Boolean, val front: Boolean, val line: String,
    )

    private fun notification(label: String, muted: Boolean): Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_kravein)
            .setColor(KraveinTokens.NOTIFICATION_ACCENT)
            .setContentTitle("Kravein is listening")
            .setContentText(label)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .setContentIntent(openIntent())
            .addAction(0, if (muted) "Unmute" else "Mute", actionIntent(ACTION_MUTE, 1))
            .addAction(0, "Stop", actionIntent(ACTION_STOP, 2))
            .build()

    private fun actionIntent(action: String, code: Int): PendingIntent =
        PendingIntent.getService(
            this, code, Intent(this, VoiceService::class.java).setAction(action),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

    private fun openIntent(): PendingIntent =
        PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java).setAction(ConfirmationNotifier.ACTION_OPEN_KRAVEIN).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

    private fun openApp() {
        try {
            openIntent().send()
        } catch (_: PendingIntent.CanceledException) {
            // Nothing to open.
        }
    }

    private fun createChannel() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Kravein voice", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Shown while Kravein is listening through this phone's microphone."
                setShowBadge(false)
            },
        )
    }

    private fun requestFocus() {
        val audioManager = getSystemService(AudioManager::class.java)
        // MAY_DUCK: other apps (a video the assistant just opened) lower their volume instead of stopping, and losing
        // focus to them never ends the session — opening an app is exactly what voice is for.
        val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
            .setAudioAttributes(
                AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build(),
            )
            .setOnAudioFocusChangeListener { }
            .build()
        audioManager.requestAudioFocus(request)
        focus = request
    }

    /** The one exit: microphone, speaker, focus, bubble and notification go together. Idempotent. */
    private fun shutdown() {
        scope.cancel()
        bubble?.remove()
        bubble = null
        audio?.let(VoiceHub::detachAudio)
        recordingCallback?.let { getSystemService(AudioManager::class.java).unregisterAudioRecordingCallback(it) }
        recordingCallback = null
        audio?.stop()
        audio = null
        focus?.let { getSystemService(AudioManager::class.java).abandonAudioFocusRequest(it) }
        focus = null
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
    }

    override fun onDestroy() {
        shutdown()
        super.onDestroy()
    }

    companion object {
        const val ACTION_MUTE = "com.kravein.connect.voice.MUTE"
        const val ACTION_STOP = "com.kravein.connect.voice.STOP"
        private const val ACTION_START = "com.kravein.connect.voice.START"
        private const val CHANNEL_ID = "kravein_voice"
        private const val NOTIFICATION_ID = 4107

        fun start(context: Context) {
            ContextCompat.startForegroundService(context, Intent(context, VoiceService::class.java).setAction(ACTION_START))
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, VoiceService::class.java))
        }
    }
}
