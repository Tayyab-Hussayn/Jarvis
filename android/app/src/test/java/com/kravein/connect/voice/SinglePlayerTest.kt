package com.kravein.connect.voice

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertSame

/** "Exactly one player per phone at any time" (#115): a second speaker is what sounds like several voices at once. */
class SinglePlayerTest {
    private class Fake(val name: String)

    private val released = ArrayList<String>()
    private val slot = SinglePlayer<Fake> { released += it.name }

    @Test fun anEmptySlotReleasesNothing() {
        slot.replace(Fake("a"))
        assertEquals(emptyList(), released)
    }

    @Test fun aNewPlayerReleasesTheOldOneFirst() {
        val a = Fake("a")
        val b = Fake("b")
        slot.replace(a)
        slot.replace(b)
        assertEquals(listOf("a"), released)
        assertSame(b, slot.player)
    }

    @Test fun manyReplacementsLeaveExactlyOneAliveAndEveryOtherReleasedOnce() {
        val all = (1..10).map { Fake("p$it") }
        all.forEach { slot.replace(it) }
        assertEquals(all.dropLast(1).map { it.name }, released)
        assertSame(all.last(), slot.player)
    }

    @Test fun putTheSamePlayerAgainReleasesNothing() {
        val a = Fake("a")
        slot.replace(a)
        slot.replace(a)
        assertEquals(emptyList(), released)
    }

    @Test fun emptyingTheSlotReleasesThePlayer() {
        slot.replace(Fake("a"))
        slot.replace(null)
        assertEquals(listOf("a"), released)
        assertNull(slot.player)
    }

    @Test fun aLateTeardownCannotEvictItsSuccessor() {
        val old = Fake("old")
        val fresh = Fake("fresh")
        slot.replace(old)
        slot.replace(fresh) // the service restarted: `old` is released here
        slot.clearIf(old) // ...and the old service's onDestroy arrives afterwards
        assertSame(fresh, slot.player)
        assertEquals(listOf("old"), released)
    }

    @Test fun clearIfOnTheCurrentPlayerReleasesIt() {
        val a = Fake("a")
        slot.replace(a)
        slot.clearIf(a)
        assertNull(slot.player)
        assertEquals(listOf("a"), released)
    }
}
