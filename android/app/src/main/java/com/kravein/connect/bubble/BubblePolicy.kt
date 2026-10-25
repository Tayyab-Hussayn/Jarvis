package com.kravein.connect.bubble

/** Whether the floating bubble exists right now. Every input must hold, so a missing one can never leave an orb behind. */
object BubblePolicy {
    /**
     * [enabled] is the Settings toggle (off by default), [overlayGranted] the "display over other apps" permission
     * (revocable at any time, so it is re-read, not remembered), [voiceActive] a live session on this phone, and
     * [appInFront] whether Kravein's own screen is visible (the bubble would only cover it).
     */
    fun shouldShow(enabled: Boolean, overlayGranted: Boolean, voiceActive: Boolean, appInFront: Boolean): Boolean =
        enabled && overlayGranted && voiceActive && !appInFront
}

/** Where the bubble sits. Positions are the view's top-left in pixels. */
object BubbleGeometry {
    /** The x the bubble settles at after a drag: the nearer screen edge, with [margin] to spare. */
    fun snapX(x: Int, bubbleWidth: Int, screenWidth: Int, margin: Int = 0): Int {
        val center = x + bubbleWidth / 2
        val left = margin
        val right = (screenWidth - bubbleWidth - margin).coerceAtLeast(left)
        return if (center < screenWidth / 2) left else right
    }

    /** Keep it fully on screen vertically, below the status bar and above the navigation bar. */
    fun clampY(y: Int, bubbleHeight: Int, screenHeight: Int, top: Int = 0, bottom: Int = 0): Int {
        val max = (screenHeight - bottom - bubbleHeight).coerceAtLeast(top)
        return y.coerceIn(top, max)
    }

    /** A drag shorter than this is a tap. */
    fun isTap(dx: Float, dy: Float, slop: Float): Boolean = dx * dx + dy * dy <= slop * slop
}
