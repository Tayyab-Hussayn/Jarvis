package com.kravein.connect.chat

/** Reconnect timing for the chat socket; the same schedule as `planRetry` in `web/lib/connection.ts`. */
object ChatRetry {
    const val BASE_MS = 500L
    const val MAX_MS = 15_000L
    const val PING_INTERVAL_MS = 25_000L

    /** 1008 is the server's policy-violation close: the UI token was refused. */
    const val CLOSE_POLICY_VIOLATION = 1008

    data class Plan(val link: Link, val delayMs: Long, val attempts: Int)

    /**
     * Keeps retrying at the capped delay even once "offline" is shown, so a phone that comes back from the background
     * recovers by itself. A refused token is not decided here: the client asks for a new one, and only the device
     * credential being refused (401 on `/phone/ui-session`) stops the chat.
     */
    fun next(attempts: Int): Plan {
        val n = attempts + 1
        val delay = (BASE_MS shl (n - 1).coerceAtMost(10)).coerceAtMost(MAX_MS)
        return Plan(if (n > 3) Link.OFFLINE else Link.RECONNECTING, delay, n)
    }
}
