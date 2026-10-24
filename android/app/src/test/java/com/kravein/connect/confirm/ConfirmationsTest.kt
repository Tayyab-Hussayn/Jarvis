package com.kravein.connect.confirm

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertIs
import kotlin.test.assertTrue

class ConfirmationBookTest {
    private val book = ConfirmationBook()

    private fun pending(id: String, expiresAt: Long = 100) =
        PendingConfirmation(id, "Delete", "rm x", Danger.DESTRUCTIVE, expiresAt)

    @Test
    fun checkIsOkUnknownOrExpired() {
        book.add(pending("a", expiresAt = 100))
        assertIs<ReplyCheck.Ok>(book.check("a", 99))
        assertEquals(ReplyCheck.Unknown, book.check("zzz", 99))
        assertEquals(ReplyCheck.Expired, book.check("a", 100))
        // Expiry removes it: it cannot become answerable again.
        assertEquals(ReplyCheck.Unknown, book.check("a", 50))
    }

    @Test
    fun expireReturnsOnlyWhatRanOut() {
        book.add(pending("early", 10))
        book.add(pending("late", 500))
        assertEquals(listOf("early"), book.expire(100).map { it.requestId })
        assertEquals(null, book.get("early"))
        assertEquals("late", book.get("late")?.requestId)
    }

    @Test
    fun closeRemoves() {
        book.add(pending("a"))
        assertEquals("a", book.close("a")?.requestId)
        assertEquals(null, book.close("a"))
    }

    @Test
    fun aChattyPcCannotGrowItWithoutBound() {
        repeat(50) { book.add(pending("c$it")) }
        assertEquals(null, book.get("c0"))
        assertEquals("c49", book.get("c49")?.requestId)
    }
}

class ConfirmationTextTest {
    private fun pending(danger: Danger, detail: String = "rm -rf ~/old") =
        PendingConfirmation("a", "Delete files", detail, danger, 100)

    @Test
    fun criticalIsVisiblyMarked() {
        assertEquals("CRITICAL: Delete files", ConfirmationText.title(pending(Danger.CRITICAL)))
        assertEquals("Delete files", ConfirmationText.title(pending(Danger.DESTRUCTIVE)))
    }

    @Test
    fun bodyShowsTheDetailOrAFallback() {
        assertEquals("rm -rf ~/old", ConfirmationText.body(pending(Danger.DESTRUCTIVE)))
        assertTrue(ConfirmationText.body(pending(Danger.DESTRUCTIVE, detail = " ")).isNotBlank())
    }

    @Test
    fun onlyASentReplyClaimsDelivery() {
        assertEquals("Approved", ConfirmationText.result(ReplyOutcome.Sent, approved = true))
        assertEquals("Declined", ConfirmationText.result(ReplyOutcome.Sent, approved = false))
        for (outcome in listOf(ReplyOutcome.NotConnected, ReplyOutcome.Expired, ReplyOutcome.Unknown)) {
            assertTrue(ConfirmationText.result(outcome, approved = true).contains("not sent", ignoreCase = true), "$outcome")
        }
        assertTrue(ConfirmationText.result(ReplyOutcome.NotConnected, true).contains("PC unreachable"))
    }

    @Test
    fun wireValuesRoundTrip() {
        assertEquals(Danger.CRITICAL, Danger.fromWire("critical"))
        assertEquals(null, Danger.fromWire("safe"))
        assertEquals(Outcome.EXPIRED, Outcome.fromWire("expired"))
        assertFalse(Outcome.entries.any { Outcome.fromWire(it.wire) != it })
    }
}
