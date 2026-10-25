package com.kravein.connect.network

import com.kravein.connect.core.DeviceCredential
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue
import kotlin.test.Test

class HostRecoveryTest {
    private val pin = "ab12cd34ef56ab78" + "0".repeat(48)
    private val stored = DeviceCredential("id", "secret", "Phone", "192.168.1.5", 8787, scheme = "https", certSha256 = pin)

    private fun svc(host: String, fp: String? = pin.take(16), port: Int = 8787, v: String? = "1") =
        DiscoveredService(host, port, fp, v)

    @Test
    fun onlyMatchingFingerprintIsACandidate() {
        val found = listOf(svc("10.0.0.2", fp = "ffffffffffffffff"), svc("10.0.0.3"), svc("10.0.0.4", fp = null))
        assertEquals(listOf("10.0.0.3"), HostRecovery.candidates(stored, found).map { it.host })
    }

    @Test
    fun fingerprintCompareIgnoresCase() {
        assertEquals(1, HostRecovery.candidates(stored, listOf(svc("10.0.0.3", fp = pin.take(16).uppercase()))).size)
    }

    @Test
    fun unknownProtocolVersionOrBadAddressIsDropped() {
        val found = listOf(svc("10.0.0.3", v = "2"), svc("10.0.0.4", v = null), svc("evil.com/x@y"), svc("10.0.0.5", port = 0))
        assertTrue(HostRecovery.candidates(stored, found).isEmpty())
    }

    @Test
    fun unpinnedCredentialIsNeverRedirected() {
        val plain = stored.copy(scheme = "http", certSha256 = null)
        assertTrue(HostRecovery.candidates(plain, listOf(svc("10.0.0.3"))).isEmpty())
    }

    @Test
    fun candidateKeepsTheCredentialAndChangesOnlyTheAddress() {
        val c = HostRecovery.candidates(stored, listOf(svc("10.0.0.3", port = 9000))).single()
        assertEquals(stored.copy(host = "10.0.0.3", port = 9000), c)
    }

    @Test
    fun theStoredAddressItselfIsNotACandidate() {
        assertTrue(HostRecovery.candidates(stored, listOf(svc("192.168.1.5"))).isEmpty())
    }

    @Test
    fun successUpdatesTheStoredHost() {
        val r = HostRecovery(stored)
        val trial = r.nextCandidate(listOf(svc("10.0.0.3")))!!
        assertEquals("10.0.0.3", r.active().host)
        assertEquals("192.168.1.5", r.stored.host)
        assertEquals(trial, r.trialSucceeded())
        assertEquals("10.0.0.3", r.stored.host)
        assertEquals("10.0.0.3", r.active().host)
        assertNull(r.trial)
    }

    @Test
    fun failedTrialFallsBackToTheStoredHostAndIsNotRetried() {
        val r = HostRecovery(stored)
        val found = listOf(svc("10.0.0.3"))
        r.nextCandidate(found)
        r.trialFailed()
        assertEquals("192.168.1.5", r.active().host)
        assertEquals("192.168.1.5", r.stored.host)
        assertNull(r.nextCandidate(found))
    }

    @Test
    fun nextCandidateAfterAFailureIsTheOtherService() {
        val r = HostRecovery(stored)
        val found = listOf(svc("10.0.0.3"), svc("10.0.0.4"))
        assertEquals("10.0.0.3", r.nextCandidate(found)!!.host)
        r.trialFailed()
        assertEquals("10.0.0.4", r.nextCandidate(found)!!.host)
    }

    @Test
    fun networkChangeForgetsRejections() {
        val r = HostRecovery(stored)
        val found = listOf(svc("10.0.0.3"))
        r.nextCandidate(found)
        r.trialFailed()
        r.networkChanged()
        assertEquals("10.0.0.3", r.nextCandidate(found)!!.host)
    }

    @Test
    fun noSecondTrialWhileOneIsRunning() {
        val r = HostRecovery(stored)
        r.nextCandidate(listOf(svc("10.0.0.3")))
        assertNull(r.nextCandidate(listOf(svc("10.0.0.4"))))
        assertNull(HostRecovery(stored).trialSucceeded())
    }
}
