package com.kravein.connect.notice

import com.kravein.connect.protocol.Frame

enum class NoticeKind(val wire: String) {
    REMINDER("reminder"),
    JOB("job"),
    ;

    companion object {
        fun fromWire(value: String): NoticeKind? = entries.firstOrNull { it.wire == value }
    }
}

/** A reminder or finished job the PC wants the owner to see. No reply goes back. */
data class Notice(val requestId: String, val title: String, val body: String, val kind: NoticeKind, val at: Long) {
    companion object {
        /** Null when the frame's `kind` is outside the vocabulary; field shapes were already checked by the codec. */
        fun fromFrame(frame: Frame): Notice? {
            val kind = NoticeKind.fromWire(frame.payload.getString("kind")) ?: return null
            return Notice(
                requestId = frame.requestId,
                title = frame.payload.getString("title"),
                body = frame.payload.getString("body"),
                kind = kind,
                at = frame.payload.getLong("at"),
            )
        }
    }
}

/**
 * Remembers which notices this connection already showed, so a resent frame does not buzz twice.
 * Bounded: the oldest id is forgotten first.
 */
class NoticeDedupe(private val limit: Int = 200) {
    private val seen = LinkedHashSet<String>()

    /** True the first time an id is offered. */
    fun firstTime(requestId: String): Boolean {
        if (!seen.add(requestId)) return false
        if (seen.size > limit) seen.remove(seen.first())
        return true
    }
}

/** Receives notices from the live connection; the Android side turns them into notifications. */
fun interface NoticeListener {
    fun onNotice(notice: Notice)

    companion object {
        val None = NoticeListener { }
    }
}
