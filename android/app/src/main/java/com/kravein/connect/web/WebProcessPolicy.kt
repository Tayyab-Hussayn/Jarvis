package com.kravein.connect.web

/**
 * When the `:web` process (the only one that holds a WebView) should be ended. Chromium keeps a renderer of ~130 MB
 * alive after a WebView is destroyed, and only ending the process returns it, so the web activity asks for exactly
 * that once it has been out of sight for [idleLimitSeconds].
 */
class WebProcessPolicy(private val idleLimitSeconds: Long = 120) {
    private var stoppedAt: Long? = null

    fun onStop(nowSeconds: Long) {
        stoppedAt = nowSeconds
    }

    fun onStart() {
        stoppedAt = null
    }

    /** True when the activity has been stopped for the whole idle limit (the timer fired and it is still hidden). */
    fun shouldExit(nowSeconds: Long): Boolean {
        val since = stoppedAt ?: return false
        return nowSeconds - since >= idleLimitSeconds
    }

    val idleMillis: Long get() = idleLimitSeconds * 1000
}
