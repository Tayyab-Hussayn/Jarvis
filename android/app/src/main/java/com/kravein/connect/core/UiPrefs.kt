package com.kravein.connect.core

import android.content.Context

/** Non-secret UI flags. Only remembers that a question was already asked; no credential or token ever goes here. */
class UiPrefs(context: Context) {
    private val prefs = context.getSharedPreferences("kravein_ui", Context.MODE_PRIVATE)

    /** The notification permission is asked once, with its reason; a refusal is respected and the app works without it. */
    var notificationsPrompted: Boolean
        get() = prefs.getBoolean(KEY_NOTIFICATIONS_PROMPTED, false)
        set(value) = prefs.edit().putBoolean(KEY_NOTIFICATIONS_PROMPTED, value).apply()

    private companion object {
        const val KEY_NOTIFICATIONS_PROMPTED = "notifications_prompted"
    }
}
