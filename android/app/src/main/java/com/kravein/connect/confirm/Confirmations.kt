package com.kravein.connect.confirm

import com.kravein.connect.protocol.Frame

enum class Danger(val wire: String) {
    DESTRUCTIVE("destructive"),
    CRITICAL("critical"),
    ;

    companion object {
        fun fromWire(value: String): Danger? = entries.firstOrNull { it.wire == value }
    }
}

/** How the PC says a confirmation ended. */
enum class Outcome(val wire: String) {
    APPROVED("approved"),
    DENIED("denied"),
    EXPIRED("expired"),
    ;

    companion object {
        fun fromWire(value: String): Outcome? = entries.firstOrNull { it.wire == value }
    }
}

/** One question the PC is waiting on. The text is shown to the owner, so it is cut to what a notification can hold. */
data class PendingConfirmation(
    val requestId: String,
    val action: String,
    val detail: String,
    val danger: Danger,
    val expiresAt: Long,
) {
    fun isExpired(nowSeconds: Long) = nowSeconds >= expiresAt

    companion object {
        const val MAX_ACTION = 120
        const val MAX_DETAIL = 600

        /** Null when the payload is not a usable request (unknown danger, no id); the caller answers with an error frame. */
        fun fromFrame(frame: Frame): PendingConfirmation? {
            if (frame.requestId.isBlank()) return null
            val danger = Danger.fromWire(frame.payload.getString("danger")) ?: return null
            return PendingConfirmation(
                requestId = frame.requestId,
                action = frame.payload.getString("action").trim().take(MAX_ACTION),
                detail = frame.payload.getString("detail").trim().take(MAX_DETAIL),
                danger = danger,
                expiresAt = frame.payload.getLong("expires_at"),
            )
        }
    }
}

sealed interface ReplyCheck {
    data class Ok(val confirmation: PendingConfirmation) : ReplyCheck

    /** Not pending (never seen, already answered, already closed). Nothing is sent. */
    data object Unknown : ReplyCheck

    /** Past `expires_at`: the PC has denied it by now, so a late reply would only confuse it. Nothing is sent. */
    data object Expired : ReplyCheck
}

/**
 * The confirmations the phone has been asked about and that are still open. It outlives a single socket on purpose:
 * a dropped connection does not close a question, and the honest answer to a tap is then "not sent", not silence.
 */
class ConfirmationBook {
    private val pending = LinkedHashMap<String, PendingConfirmation>()

    @Synchronized
    fun add(confirmation: PendingConfirmation) {
        pending[confirmation.requestId] = confirmation
        // A PC that never closes anything must not grow this without bound.
        while (pending.size > MAX_PENDING) pending.remove(pending.keys.first())
    }

    @Synchronized
    fun close(requestId: String): PendingConfirmation? = pending.remove(requestId)

    @Synchronized
    fun get(requestId: String): PendingConfirmation? = pending[requestId]

    /** Check a tap against the book. An answer is consumed by [close] only after it was actually sent. */
    @Synchronized
    fun check(requestId: String, nowSeconds: Long): ReplyCheck {
        val found = pending[requestId] ?: return ReplyCheck.Unknown
        if (found.isExpired(nowSeconds)) {
            pending.remove(requestId)
            return ReplyCheck.Expired
        }
        return ReplyCheck.Ok(found)
    }

    /** Drop and return everything past its time. */
    @Synchronized
    fun expire(nowSeconds: Long): List<PendingConfirmation> {
        val gone = pending.values.filter { it.isExpired(nowSeconds) }
        gone.forEach { pending.remove(it.requestId) }
        return gone
    }

    @Synchronized
    fun clear() = pending.clear()

    private companion object {
        const val MAX_PENDING = 20
    }
}

/** The words on the notification, kept out of the Android code so they can be tested. */
object ConfirmationText {
    fun title(c: PendingConfirmation) = if (c.danger == Danger.CRITICAL) "CRITICAL: ${c.action}" else c.action

    fun body(c: PendingConfirmation) = c.detail.ifBlank { "Kravein is waiting for your answer." }

    /** What the owner reads after a tap. Only [ReplyOutcome.Sent] claims anything was delivered. */
    fun result(outcome: ReplyOutcome, approved: Boolean) = when (outcome) {
        ReplyOutcome.Sent -> if (approved) "Approved" else "Declined"
        ReplyOutcome.NotConnected -> "Not sent – PC unreachable"
        ReplyOutcome.Expired -> "Expired – not sent"
        ReplyOutcome.Unknown -> "No longer pending – not sent"
    }

    const val REJECTED = "Not accepted – already answered or expired"
}

/** Where the socket reports confirmations; the service implements it with notifications. */
interface ConfirmationListener {
    fun onRequested(confirmation: PendingConfirmation)
    fun onClosed(requestId: String, outcome: Outcome)

    /** The PC answered one of our replies with `unknown_request`: late, duplicate or already settled elsewhere. */
    fun onReplyRejected(requestId: String)

    object None : ConfirmationListener {
        override fun onRequested(confirmation: PendingConfirmation) = Unit
        override fun onClosed(requestId: String, outcome: Outcome) = Unit
        override fun onReplyRejected(requestId: String) = Unit
    }
}

/** What happened to a tap on Approve / Decline. */
sealed interface ReplyOutcome {
    data object Sent : ReplyOutcome

    /** No live authenticated socket. Nothing was sent and the owner is told so. */
    data object NotConnected : ReplyOutcome
    data object Expired : ReplyOutcome
    data object Unknown : ReplyOutcome
}
