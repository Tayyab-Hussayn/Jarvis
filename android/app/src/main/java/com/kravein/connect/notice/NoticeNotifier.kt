package com.kravein.connect.notice

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
import com.kravein.connect.MainActivity
import com.kravein.connect.R
import com.kravein.connect.confirm.ConfirmationNotifier
import com.kravein.connect.ui.theme.KraveinTokens

/**
 * Shows reminders and finished jobs on their own default-importance channel, apart from the high-priority
 * confirmations, so muting one never mutes the other. A tap opens the Kravein tab.
 */
class NoticeNotifier(private val context: Context) : NoticeListener {
    private val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val channel = NotificationChannel(CHANNEL_ID, "Kravein reminders", NotificationManager.IMPORTANCE_DEFAULT)
        channel.description = "Reminders and finished jobs from Kravein"
        manager.createNotificationChannel(channel)
    }

    override fun onNotice(notice: Notice) {
        if (!canNotify()) return
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_kravein)
            .setColor(KraveinTokens.NOTIFICATION_ACCENT)
            .setContentTitle(notice.title)
            .setContentText(notice.body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(notice.body))
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setCategory(if (notice.kind == NoticeKind.REMINDER) NotificationCompat.CATEGORY_REMINDER else NotificationCompat.CATEGORY_STATUS)
            .setWhen(notice.at * 1000)
            .setShowWhen(true)
            .setAutoCancel(true)
            .setContentIntent(openKraveinIntent(notice.requestId))
            .build()
        manager.notify(idFor(notice.requestId), notification)
    }

    private fun canNotify() = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    // Distinct data per notice so each keeps its own PendingIntent; the action is the one MainActivity already handles.
    private fun openKraveinIntent(requestId: String): PendingIntent {
        val intent = Intent(context, MainActivity::class.java)
            .setAction(ConfirmationNotifier.ACTION_OPEN_KRAVEIN)
            .setData(Uri.fromParts("kravein-notice", requestId, null))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        return PendingIntent.getActivity(context, 0, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }

    companion object {
        const val CHANNEL_ID = "kravein_reminders"

        /** Offset into its own id range so it cannot collide with confirmations or the service notification. */
        fun idFor(requestId: String) = 2_000_000 + (requestId.hashCode() and 0x3FFFFFFF) % 1_000_000
    }
}
