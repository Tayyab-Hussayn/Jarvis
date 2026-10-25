package com.kravein.connect.voice

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import com.kravein.connect.MainActivity
import com.kravein.connect.R
import com.kravein.connect.confirm.ConfirmationNotifier
import com.kravein.connect.ui.theme.KraveinTokens

/**
 * "Voice stopped — tap to restart": what the user sees when a session ends without them asking (the microphone died,
 * the link was gone too long, the server forgot it). The ongoing "listening" notification disappears with the
 * service, so without this a session that ended in the pocket would leave no trace at all.
 */
object VoiceNotice {
    private const val CHANNEL_ID = "kravein_voice_status"
    private const val ID = 4108

    fun postStopped(context: Context, reason: String) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Kravein voice status", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "Tells you when a voice session ended on its own."
                setShowBadge(false)
            },
        )
        val open = PendingIntent.getActivity(
            context, 0,
            Intent(context, MainActivity::class.java).setAction(ConfirmationNotifier.ACTION_OPEN_KRAVEIN)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        manager.notify(
            ID,
            NotificationCompat.Builder(context, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_stat_kravein)
                .setColor(KraveinTokens.NOTIFICATION_ACCENT)
                .setContentTitle("Voice stopped — tap to restart")
                .setContentText(reason.removePrefix("Voice stopped: ").removePrefix("Voice ended: "))
                .setStyle(NotificationCompat.BigTextStyle().bigText(reason))
                .setContentIntent(open)
                .setAutoCancel(true)
                .build(),
        )
    }

    /** A new session (or the app coming to the front) makes the old notice stale. */
    fun clear(context: Context) {
        context.getSystemService(NotificationManager::class.java).cancel(ID)
    }
}
