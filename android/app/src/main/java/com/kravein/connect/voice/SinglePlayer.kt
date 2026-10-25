package com.kravein.connect.voice

/**
 * Holds the one audio player a phone may have. Putting a new one in releases the old one first, so a service restart, a
 * resume or a reconnect can never leave two speakers talking at once — the failure that sounds like "2–3 voices".
 */
class SinglePlayer<T : Any>(private val release: (T) -> Unit) {
    private var current: T? = null

    /** The player now in the slot, or null. */
    @get:Synchronized
    val player: T? get() = current

    /** Make [next] the player (or empty the slot with null). Whatever was there is released, exactly once. */
    fun replace(next: T?) {
        val old = synchronized(this) {
            val previous = current
            current = next
            previous
        }
        // Outside the lock: releasing an AudioTrack can block briefly.
        if (old != null && old !== next) release(old)
    }

    /** Empty the slot only if [player] is still the one in it (a late teardown must not evict its successor). */
    fun clearIf(player: T) {
        val old = synchronized(this) {
            if (current === player) { current = null; player } else null
        }
        if (old != null) release(old)
    }
}
