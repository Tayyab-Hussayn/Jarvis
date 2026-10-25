package com.kravein.connect.chat

/** Reconnect timing for the chat socket; the same schedule as `planRetry` in `web/lib/connection.ts`. */
object ChatRetry {
    const val BASE_MS = 500L
    const val MAX_MS = 15_000L
    const val PING_INTERVAL_MS = 15_000L

    /**
     * Inbound silence after which the link is dead. The server answers every ping, so a healthy socket is never quiet
     * for two intervals; a Wi-Fi radio that slept in the background leaves a half-open socket that still accepts
     * writes, and without this the chat (and a voice session on it) would sit "connected" and deaf for good.
     */
    const val DEAD_AFTER_MS = 40_000L

    fun isDead(lastInboundAt: Long, now: Long): Boolean = now - lastInboundAt > DEAD_AFTER_MS

    /** 1008 is the server's policy-violation close: the UI token was refused. */
    const val CLOSE_POLICY_VIOLATION = 1008

    /** Another socket resumed this session (`server.CLOSE_REPLACED`); retrying would only take it back and forth. */
    const val CLOSE_REPLACED = 4001

    fun retriesAfter(closeCode: Int): Boolean = closeCode != CLOSE_REPLACED

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
