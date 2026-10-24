package com.kravein.connect.network

import com.kravein.connect.core.ConnectionState
import kotlin.math.min

/** What to do after a websocket attempt fails. Pure so the revoked-credential path is unit-tested. */
sealed interface FailureAction {
    data class Retry(val delayMs: Long) : FailureAction
    data object NeedsPairing : FailureAction

    /** The server's certificate is not the pinned one: hard stop, with the reason to show. */
    data object PinMismatch : FailureAction

    /** An unpinned (pre-TLS) credential keeps hitting a TLS-only server: only a new pairing can fix it. */
    data object NeedsTlsRepair : FailureAction
}

object ConnectionPolicy {
    /** The server closes a refused handshake before accepting it, which OkHttp reports as a 401/403 response. */
    fun isCredentialRefused(httpStatus: Int?): Boolean = httpStatus == 401 || httpStatus == 403

    fun backoffMs(attempt: Int): Long = min(30_000L, 1_000L * (1L shl min(attempt.coerceAtLeast(1), 5)))

    /** Consecutive "looks like plain http to a TLS port" failures before an unpinned credential is declared stale. */
    const val TLS_HINTS_BEFORE_REPAIR = 3

    /**
     * [failure] is what the socket threw; [tlsHints] counts earlier consecutive failures that looked like plain http
     * hitting a TLS server (only counted for credentials without a pin).
     */
    fun onFailure(httpStatus: Int?, attempt: Int, failure: Throwable? = null, tlsHints: Int = 0): FailureAction = when {
        isCredentialRefused(httpStatus) -> FailureAction.NeedsPairing
        TlsFailures.isPinMismatch(failure) -> FailureAction.PinMismatch
        tlsHints >= TLS_HINTS_BEFORE_REPAIR -> FailureAction.NeedsTlsRepair
        else -> FailureAction.Retry(backoffMs(attempt))
    }

    fun stateAfter(action: FailureAction): ConnectionState = when (action) {
        is FailureAction.Retry -> ConnectionState.RECONNECTING
        FailureAction.NeedsPairing, FailureAction.PinMismatch, FailureAction.NeedsTlsRepair -> ConnectionState.NEEDS_PAIRING
    }

    const val REPAIR_MESSAGE =
        "The PC refused this phone's credential (it may have been revoked). Scan a new pairing QR code to pair again."
}
