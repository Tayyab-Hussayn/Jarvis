package com.kravein.connect

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.kravein.connect.commands.DeviceCommandHandler
import com.kravein.connect.confirm.ConfirmationListener
import com.kravein.connect.confirm.ConfirmationNotifier
import com.kravein.connect.notice.NoticeNotifier
import com.kravein.connect.confirm.ConfirmationText
import com.kravein.connect.confirm.Outcome
import com.kravein.connect.confirm.PendingConfirmation
import com.kravein.connect.ui.theme.KraveinTokens
import com.kravein.connect.core.AgentStateStore
import com.kravein.connect.core.ConnectionState
import com.kravein.connect.commands.CommandRouter
import com.kravein.connect.network.PhoneClient
import com.kravein.connect.pairing.PairingStorage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.collect

class KraveinConnectForegroundService : Service() {
    private val scope = CoroutineScope(Job() + Dispatchers.IO)
    private lateinit var storage: PairingStorage
    private lateinit var client: PhoneClient
    private lateinit var notifier: ConfirmationNotifier
    private lateinit var noticeNotifier: NoticeNotifier
    private var connectStarted = false

    override fun onCreate() {
        super.onCreate()
        storage = PairingStorage(this)
        val handler = DeviceCommandHandler(this)
        notifier = ConfirmationNotifier(this)
        notifier.ensureChannel()
        noticeNotifier = NoticeNotifier(this)
        noticeNotifier.ensureChannel()
        client = PhoneClient(CommandRouter(handler), { handler.helloInfo(it.deviceName) }, object : ConfirmationListener {
            override fun onRequested(confirmation: PendingConfirmation) =
                notifier.show(confirmation, System.currentTimeMillis() / 1000)

            override fun onClosed(requestId: String, outcome: Outcome) = notifier.cancel(requestId)

            override fun onReplyRejected(requestId: String) = notifier.showResult(requestId, ConfirmationText.REJECTED)
        }, noticeNotifier)
        createNotificationChannel()
        try {
            val notification = buildNotification("Kravein Connect", "Starting connection")
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
            } else {
                startForeground(NOTIFICATION_ID, notification)
            }
        } catch (security: SecurityException) {
            stopSelf()
            return
        } catch (t: Throwable) {
            stopSelf()
            return
        }
        scope.launch {
            AgentStateStore.connectionState.collect {
                updateNotification()
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                client.disconnect()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_CONFIRM_REPLY -> {
                answerConfirmation(intent)
                // A service that was just (re)created has no socket yet; reconnecting is right then, but on a live
                // one connect() would tear down the working connection.
                if (!connectStarted) connectIfPossible()
                return START_STICKY
            }
        }
        connectIfPossible()
        return START_STICKY
    }

    private fun answerConfirmation(intent: Intent) {
        val requestId = intent.getStringExtra(EXTRA_REQUEST_ID)?.takeIf { it.isNotBlank() && it.length <= 64 } ?: return
        // No default: a missing extra must never read as approval.
        if (!intent.hasExtra(EXTRA_APPROVE)) return
        val approve = intent.getBooleanExtra(EXTRA_APPROVE, false)
        val outcome = client.sendConfirmReply(requestId, approve)
        notifier.showResult(requestId, ConfirmationText.result(outcome, approve))
    }

    private fun connectIfPossible() {
        val credential = storage.loadCredential()
        if (credential == null) {
            stopSelf()
            return
        }
        AgentStateStore.setCredential(credential)
        connectStarted = true
        client.connect(credential)
    }

    private fun updateNotification() {
        val state = AgentStateStore.connectionState.value
        val text = when (state) {
            ConnectionState.CONNECTED -> "Connected to Kravein"
            ConnectionState.CONNECTING -> "Connecting to Kravein"
            ConnectionState.RECONNECTING -> "Reconnecting"
            ConnectionState.DISCONNECTED -> "Disconnected"
            ConnectionState.NEEDS_PAIRING -> "Pairing required"
        }
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.notify(NOTIFICATION_ID, buildNotification("Kravein Connect", text))
    }

    private fun buildNotification(title: String, text: String): Notification {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_kravein)
            .setColor(KraveinTokens.NOTIFICATION_ACCENT)
            .setContentTitle(title)
            .setContentText(text)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val channel = NotificationChannel(CHANNEL_ID, "Kravein Connect", NotificationManager.IMPORTANCE_LOW)
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(channel)
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        client.disconnect()
        super.onDestroy()
    }

    companion object {
        const val ACTION_STOP = "com.kravein.connect.action.STOP"
        const val ACTION_CONFIRM_REPLY = "com.kravein.connect.action.CONFIRM_REPLY"
        const val EXTRA_REQUEST_ID = "request_id"
        const val EXTRA_APPROVE = "approve"
        private const val CHANNEL_ID = "kravein_connect"
        private const val NOTIFICATION_ID = 4201
    }
}
