package com.kravein.connect.web

/**
 * What the app does around a live voice session, decided from one fact the platform tells us: whether this app's
 * microphone is being recorded right now (AudioManager.AudioRecordingCallback). The page owns the session; the app
 * never needs to be told its start or end, and a page that forgot to stop is still seen.
 */
class VoiceSessionPolicy {
    enum class Action { None, TakeAudioFocus, ReleaseAudioFocus }

    var micActive = false
        private set

    /** Recording state changed. Focus is taken on the first active report and released when the mic goes quiet. */
    fun onRecording(active: Boolean): Action {
        if (active == micActive) return Action.None
        micActive = active
        return if (active) Action.TakeAudioFocus else Action.ReleaseAudioFocus
    }

    /**
     * The app left the screen. The simpler safe choice (over a notification-backed background session): a session
     * never outlives the screen, so a mic that is still open here is closed by the app. True = stop it now.
     */
    fun mustStopOnBackground(): Boolean = micActive

    /** The page was torn down (WebView destroyed): nothing records any more and focus must go. */
    fun onPageGone(): Action = onRecording(false)
}
