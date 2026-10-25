package com.kravein.connect.web

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class WebProcessPolicyTest {
    @Test fun exitsOnlyAfterTheFullIdleLimitWhileStillHidden() {
        val p = WebProcessPolicy(idleLimitSeconds = 120)
        assertFalse(p.shouldExit(1_000)) // never stopped
        p.onStop(1_000)
        assertFalse(p.shouldExit(1_119))
        assertTrue(p.shouldExit(1_120))
    }

    @Test fun comingBackCancelsTheExit() {
        val p = WebProcessPolicy(120)
        p.onStop(0)
        p.onStart()
        assertFalse(p.shouldExit(10_000))
    }

    @Test fun defaultIsTwoMinutes() {
        assertEquals(120_000L, WebProcessPolicy().idleMillis)
    }
}
