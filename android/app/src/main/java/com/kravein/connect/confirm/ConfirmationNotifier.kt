package com.kravein.connect.confirm

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.kravein.connect.KraveinConnectForegroundService
import com.kravein.connect.MainActivity
import com.kravein.connect.R

/**
 * Shows a pending confirmation as a high-priority notification with Approve and Decline. The buttons start the
 * foreground service, which owns the socket; a missing notification permission only means nothing is shown.
 */
class ConfirmationNotifier(private val context: Context) {
    private val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val channel = NotificationChannel(CHANNEL_ID, "Kravein confirmations", NotificationManager.IMPORTANCE_HIGH)
        channel.description = "Requests from Kravein that need your approval"
        manager.createNotificationChannel(channel)
    }

    fun show(confirmation: PendingConfirmation, nowSeconds: Long) {
        if (!canNotify()) return
        val critical = confirmation.danger == Danger.CRITICAL
        val builder = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_kravein_launcher)
            .setContentTitle(ConfirmationText.title(confirmation))
            .setContentText(ConfirmationText.body(confirmation))
            .setStyle(NotificationCompat.BigTextStyle().bigText(ConfirmationText.body(confirmation)))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(if (critical) NotificationCompat.CATEGORY_ALARM else NotificationCompat.CATEGORY_MESSAGE)
            .setOnlyAlertOnce(true)
            .setAutoCancel(false)
            .setContentIntent(openKraveinIntent(confirmation.requestId))
            .addAction(0, "Decline", replyIntent(confirmation.requestId, approve = false))
            .addAction(0, "Approve", replyIntent(confirmation.requestId, approve = true))
        if (critical) builder.setColor(0xFFFF6B6B.toInt()).setSubText("Critical")
        // The notification disappears by itself when the question runs out, even if the service was killed meanwhile.
        val remainingMs = (confirmation.expiresAt - nowSeconds) * 1000
        if (remainingMs > 0) builder.setTimeoutAfter(remainingMs)
        manager.notify(idFor(confirmation.requestId), builder.build())
    }

    /** Replace the buttons with a one-line result that clears itself; used after a tap. */
    fun showResult(requestId: String, text: String) {
        if (!canNotify()) return
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_kravein_launcher)
            .setContentTitle(text)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setAutoCancel(true)
            .setTimeoutAfter(RESULT_MS)
            .setContentIntent(openKraveinIntent(requestId))
            .build()
        manager.notify(idFor(requestId), notification)
    }

    fun cancel(requestId: String) = manager.cancel(idFor(requestId))

    private fun canNotify() = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    private fun openKraveinIntent(requestId: String): PendingIntent {
        val intent = Intent(context, MainActivity::class.java)
            .setAction(ACTION_OPEN_KRAVEIN)
            .setData(Uri.fromParts("kravein-confirm", requestId, null))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        return PendingIntent.getActivity(context, 0, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }

    // Explicit, immutable, and distinct per question and answer, so one tap can never be delivered as another.
    private fun replyIntent(requestId: String, approve: Boolean): PendingIntent {
        val intent = Intent(context, KraveinConnectForegroundService::class.java)
            .setAction(KraveinConnectForegroundService.ACTION_CONFIRM_REPLY)
            .setData(Uri.fromParts("kravein-confirm", "$requestId/${if (approve) "approve" else "decline"}", null))
            .putExtra(KraveinConnectForegroundService.EXTRA_REQUEST_ID, requestId)
            .putExtra(KraveinConnectForegroundService.EXTRA_APPROVE, approve)
        return PendingIntent.getForegroundService(context, 0, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }

    companion object {
        const val CHANNEL_ID = "kravein_confirmations"
        const val ACTION_OPEN_KRAVEIN = "com.kravein.connect.action.OPEN_KRAVEIN"
        private const val RESULT_MS = 8_000L

        /** Stable per question; offset so it can never collide with the service's own notification id. */
        fun idFor(requestId: String) = 10_000 + (requestId.hashCode() and 0x3FFFFFFF) % 1_000_000
    }
}
