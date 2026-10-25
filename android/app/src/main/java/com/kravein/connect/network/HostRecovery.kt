package com.kravein.connect.network

import com.kravein.connect.core.DeviceCredential
import com.kravein.connect.core.PairingOfferParser

/** One `_kravein._tcp` answer, already reduced to plain strings so the filter is testable without NsdManager. */
data class DiscoveredService(val host: String, val port: Int, val fp: String?, val version: String?)

object DiscoveryText {
    const val SEARCHING = "Searching for your PC on this network…"
    const val NOT_FOUND_HINT =
        "Your phone and PC need the same Wi-Fi. Mobile data cannot reach a laptop at home or on another network."
}

/**
 * Finds the PC again after its address changed, without re-pairing. mDNS only *suggests* an address: a service is a
 * candidate when its advertised fingerprint prefix matches the stored pin, but whoever answers is still held to the
 * full pin by the TLS handshake. A candidate that fails (wrong certificate, refusal, unreachable) is dropped for this
 * search and the stored address stays what it was.
 */
class HostRecovery(stored: DeviceCredential) {
    /** The address that is saved; changes only through [trialSucceeded]. */
    var stored: DeviceCredential = stored
        private set

    /** A rediscovered address being tried; never saved before it connected and passed the pin. */
    var trial: DeviceCredential? = null
        private set

    private val rejected = mutableSetOf<String>()

    /** What the socket should open next. */
    fun active(): DeviceCredential = trial ?: stored

    /** The next address worth trying from [services], or null. Starts a trial when one is returned. */
    fun nextCandidate(services: List<DiscoveredService>): DeviceCredential? {
        if (trial != null) return null
        val candidate = candidates(stored, services).firstOrNull { key(it) !in rejected } ?: return null
        trial = candidate
        return candidate
    }

    /** The trial connected and passed the full pin: it becomes the saved address. */
    fun trialSucceeded(): DeviceCredential? {
        val won = trial ?: return null
        trial = null
        stored = won
        rejected.clear()
        return won
    }

    /** Fell back to the stored address: this candidate is not tried again until the network changes. */
    fun trialFailed() {
        val lost = trial ?: return
        trial = null
        rejected += key(lost)
    }

    /** A new network can make an earlier rejection wrong (the PC may now be reachable there). */
    fun networkChanged() {
        trial = null
        rejected.clear()
    }

    companion object {
        /** The advertised `fp` is the first 16 hex of the certificate SHA-256. */
        const val FP_LENGTH = 16
        private const val SERVICE_VERSION = "1"

        private fun key(c: DeviceCredential) = "${c.host}:${c.port}"

        /**
         * Services that may be the paired PC, as credentials with the new address. Needs a pin: an unpinned (plain
         * http) pairing has no identity to match, so it is never redirected by a broadcast anyone can send.
         */
        fun candidates(stored: DeviceCredential, services: List<DiscoveredService>): List<DeviceCredential> {
            val pin = stored.certSha256 ?: return emptyList()
            val wanted = pin.take(FP_LENGTH).lowercase()
            return services.asSequence()
                .filter { it.version == SERVICE_VERSION }
                .filter { it.fp?.lowercase() == wanted }
                .filter { it.port in 1..65535 && PairingOfferParser.isUsableHost(it.host) }
                .filter { it.host != stored.host || it.port != stored.port }
                .distinctBy { "${it.host}:${it.port}" }
                .map { stored.copy(host = it.host, port = it.port) }
                .toList()
        }
    }
}

/** Source of [DiscoveredService]s; Android's NsdManager in the app, a fake in tests. */
interface PcDiscovery {
    /** Idempotent. [onFound] gets every service seen so far, on any thread. */
    fun start(onFound: (List<DiscoveredService>) -> Unit)

    /** Idempotent. Releases the multicast lock and listeners (battery). */
    fun stop()

    object None : PcDiscovery {
        override fun start(onFound: (List<DiscoveredService>) -> Unit) = Unit
        override fun stop() = Unit
    }
}

/** Saves a rediscovered address. */
fun interface HostListener {
    fun onHostChanged(credential: DeviceCredential)

    companion object {
        val None = HostListener { }
    }
}
