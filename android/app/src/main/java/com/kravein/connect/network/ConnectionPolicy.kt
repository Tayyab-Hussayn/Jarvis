package com.kravein.connect.network

import com.kravein.connect.core.ConnectionState
import kotlin.math.min

/** What to do after a websocket attempt fails. Pure so the revoked-credential path is unit-tested. */
sealed interface FailureAction {
    data class Retry(val delayMs: Long) : FailureAction
    data object NeedsPairing : FailureAction
}

object ConnectionPolicy {
    /** The server closes a refused handshake before accepting it, which OkHttp reports as a 401/403 response. */
    fun isCredentialRefused(httpStatus: Int?): Boolean = httpStatus == 401 || httpStatus == 403

    fun backoffMs(attempt: Int): Long = min(30_000L, 1_000L * (1L shl min(attempt.coerceAtLeast(1), 5)))

    fun onFailure(httpStatus: Int?, attempt: Int): FailureAction =
        if (isCredentialRefused(httpStatus)) FailureAction.NeedsPairing else FailureAction.Retry(backoffMs(attempt))

    fun stateAfter(action: FailureAction): ConnectionState = when (action) {
        is FailureAction.Retry -> ConnectionState.RECONNECTING
        FailureAction.NeedsPairing -> ConnectionState.NEEDS_PAIRING
    }

    const val REPAIR_MESSAGE =
        "The PC refused this phone's credential (it may have been revoked). Scan a new pairing QR code to pair again."
}
