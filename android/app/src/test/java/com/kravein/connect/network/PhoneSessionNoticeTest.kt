package com.kravein.connect.network

import com.kravein.connect.commands.CommandRouter
import com.kravein.connect.commands.DeviceAction
import com.kravein.connect.commands.DeviceBackend
import com.kravein.connect.notice.NoticeDedupe
import com.kravein.connect.notice.NoticeKind
import com.kravein.connect.protocol.CommandResult
import com.kravein.connect.protocol.Direction
import com.kravein.connect.protocol.FrameCodec
import org.json.JSONObject
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertTrue

class PhoneSessionNoticeTest {
    private val backend = object : DeviceBackend {
        override fun execute(action: DeviceAction, args: JSONObject) = CommandResult(true)
    }
    private val session = PhoneSession(CommandRouter(backend), HelloInfo("Pixel", "15", "0.1.0")) { "hello-1" }

    private fun ready() {
        session.open()
        session.onText("""{"type":"welcome","request_id":"hello-1","payload":{"protocol":1,"device_id":"ph_9","server_name":"Kravein"}}""")
    }

    private fun notify(id: String = "n1", title: String = "Stand up", body: String = "Time to stretch", kind: String = "reminder", at: Long = 1_700_000_000) =
        """{"type":"notify","request_id":"$id","payload":{"title":"$title","body":"$body","kind":"$kind","at":$at}}"""

    private fun errorCode(out: SessionOutput) =
        FrameCodec.decode(out.send.single(), Direction.PHONE_TO_SERVER).payload.getString("code")

    @Test
    fun aNoticeBecomesOneEventAndNoReply() {
        ready()
        val out = session.onText(notify(kind = "job"))
        val event = assertIs<SessionEvent.NoticeReceived>(out.events.single())
        assertEquals("n1", event.notice.requestId)
        assertEquals("Stand up", event.notice.title)
        assertEquals("Time to stretch", event.notice.body)
        assertEquals(NoticeKind.JOB, event.notice.kind)
        assertEquals(1_700_000_000, event.notice.at)
        assertTrue(out.send.isEmpty())
    }

    @Test
    fun aRepeatedRequestIdIsShownOnce() {
        ready()
        assertEquals(1, session.onText(notify("same")).events.size)
        val again = session.onText(notify("same", title = "Other"))
        assertTrue(again.events.isEmpty())
        assertTrue(again.send.isEmpty())
        assertEquals(1, session.onText(notify("different")).events.size)
    }

    @Test
    fun dedupeIsSharedAcrossSessionsThatShareIt() {
        val seen = NoticeDedupe()
        fun fresh(): PhoneSession {
            val s = PhoneSession(CommandRouter(backend), HelloInfo("Pixel", "15", "0.1.0"), notices = seen) { "hello-1" }
            s.open()
            s.onText("""{"type":"welcome","request_id":"hello-1","payload":{"protocol":1,"device_id":"ph_9","server_name":"Kravein"}}""")
            return s
        }
        assertEquals(1, fresh().onText(notify("n7")).events.size)
        assertTrue(fresh().onText(notify("n7")).events.isEmpty())
    }

    @Test
    fun dedupeForgetsTheOldestWhenFull() {
        val seen = NoticeDedupe(limit = 2)
        assertTrue(seen.firstTime("a"))
        assertTrue(seen.firstTime("b"))
        assertTrue(seen.firstTime("c"))
        assertTrue(seen.firstTime("a"))
    }

    @Test
    fun noticesBeforeWelcomeAreRefused() {
        session.open()
        assertEquals("not_ready", errorCode(session.onText(notify())))
    }

    @Test
    fun anUnknownKindIsMalformedAndNotShown() {
        ready()
        val out = session.onText(notify(kind = "alarm"))
        assertTrue(out.events.isEmpty())
        assertEquals("malformed_frame", errorCode(out))
    }

    @Test
    fun overlongTitleBodyAndWrongTypesAreRejected() {
        ready()
        assertEquals("malformed_frame", errorCode(session.onText(notify(title = "t".repeat(121)))))
        assertEquals("malformed_frame", errorCode(session.onText(notify(body = "b".repeat(1001)))))
        val wrong = """{"type":"notify","request_id":"n2","payload":{"title":"x","body":"y","kind":"job","at":"soon"}}"""
        assertEquals("malformed_frame", errorCode(session.onText(wrong)))
        val extra = """{"type":"notify","request_id":"n3","payload":{"title":"x","body":"y","kind":"job","at":1,"sound":"loud"}}"""
        assertEquals("malformed_frame", errorCode(session.onText(extra)))
        val maxed = notify("n4", title = "t".repeat(120), body = "b".repeat(1000))
        assertEquals(1, session.onText(maxed).events.size)
    }
}
