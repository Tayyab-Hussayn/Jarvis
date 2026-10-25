package com.kravein.connect.voice

import com.kravein.connect.chat.ChatFrames
import com.kravein.connect.chat.ChatReducer
import com.kravein.connect.chat.ChatState
import com.kravein.connect.chat.ReplayVoice
import com.kravein.connect.chat.ServerFrame
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class VoiceControllerTest {
    private val device = ReplayVoice(active = true, mode = "live", audio = "device")

    /** Apply [json] to a state the way the client does, and let the controller see it. */
    private fun frame(c: VoiceController, json: String, from: ChatState = ChatState()): List<VoiceEffect> {
        val f = ChatFrames.parse(json)
        return c.onFrame(f, ChatReducer.onFrame(from, f, 0))
    }

    private fun started(c: VoiceController = VoiceController()): VoiceController {
        c.onUserStart(micGranted = true, linkOpen = true)
        return c
    }

    @Test fun startAsksForTheMicFirstAndNeverStartsAnythingWithoutIt() {
        val c = VoiceController()
        assertEquals(listOf(VoiceEffect.RequestMic), c.onUserStart(micGranted = false, linkOpen = true))
        assertFalse(c.active)
    }

    @Test fun startNeedsAConnectionOtherwiseNoServiceAndNoOpenMicrophone() {
        val c = VoiceController()
        assertEquals(listOf(VoiceEffect.Note("Not connected to Kravein.")), c.onUserStart(micGranted = true, linkOpen = false))
        assertFalse(c.active)
    }

    @Test fun startAsksTheServerAndRaisesTheServiceWithItsNotification() {
        val c = VoiceController()
        assertEquals(listOf(VoiceEffect.StartSession, VoiceEffect.StartService), c.onUserStart(true, true))
        assertTrue(c.active)
        // A second tap while running changes nothing.
        assertEquals(emptyList(), c.onUserStart(true, true))
    }

    @Test fun userStopEndsServerAndServiceTogether() {
        val c = started()
        assertEquals(listOf(VoiceEffect.SendStop, VoiceEffect.StopService), c.onUserStop())
        assertFalse(c.active)
    }

    @Test fun stopWithNothingActiveStillAsksTheServerSoAPcSessionCanBeEnded() {
        assertEquals(listOf(VoiceEffect.SendStop), VoiceController().onUserStop())
    }

    @Test fun serverStoppedReleasesTheMicrophone() {
        val c = started()
        assertEquals(listOf(VoiceEffect.StopService), frame(c, """{"t":"voice_state","phase":"stopped","text":""}"""))
        assertFalse(c.active)
        // A repeated `stopped` has nothing left to do.
        assertEquals(emptyList(), frame(c, """{"t":"voice_state","phase":"stopped","text":""}"""))
    }

    @Test fun aFatalVoiceErrorReleasesTheMicrophoneAndAsksTheServerToEnd() {
        val c = started()
        assertEquals(
            listOf(VoiceEffect.SendStop, VoiceEffect.StopService),
            frame(c, """{"t":"voice_state","phase":"error","text":"This device's mic and speaker need Live voice."}"""),
        )
        assertFalse(c.active)
    }

    @Test fun anErrorInsideATurnDoesNotEndTheSession() {
        val c = started()
        val inTurn = ChatReducer.onFrame(ChatState(), ChatFrames.parse("""{"t":"voice_state","phase":"heard","text":"x"}"""), 0)
        assertEquals(emptyList(), frame(c, """{"t":"voice_state","phase":"error","text":"tool timed out"}""", from = inTurn))
        assertTrue(c.active)
    }

    @Test fun aServerThatForgotTheSessionEndsOurMicrophone() {
        val c = started()
        val effects = frame(c, """{"t":"hello","model":"m","session_id":"new","resumed":false}""")
        assertEquals(VoiceEffect.StopService, effects.first())
        assertTrue(effects.any { it is VoiceEffect.EndedHere })
        assertFalse(c.active)
    }

    @Test fun aResumedHelloChangesNothing() {
        val c = started()
        assertEquals(emptyList(), frame(c, """{"t":"hello","model":"m","session_id":"a","resumed":true}"""))
        assertTrue(c.active)
    }

    // ---- resume after the app was closed ----

    @Test fun aSurvivingDeviceSessionReopensTheMicWhenTheAppIsInFrontWithPermission() {
        val c = VoiceController()
        assertEquals(listOf(VoiceEffect.StartService), c.onReplay(device, canStartService = true))
        assertTrue(c.active)
    }

    @Test fun aSurvivingDeviceSessionIsEndedWhenTheMicCannotBeReopened() {
        val c = VoiceController()
        val effects = c.onReplay(device, canStartService = false)
        assertEquals(VoiceEffect.SendStop, effects.first())
        assertTrue(effects.any { it is VoiceEffect.Note })
        assertFalse(c.active)
    }

    @Test fun replayWhileTheServiceAlreadyRunsDoesNotStartASecond() {
        val c = started()
        assertEquals(emptyList(), c.onReplay(device, canStartService = true))
        assertTrue(c.active)
    }

    @Test fun replayWithoutVoiceStopsAServiceThatThoughtItWasLiveAndSaysWhy() {
        val c = started()
        val effects = c.onReplay(ReplayVoice.NONE, canStartService = true)
        assertEquals(VoiceEffect.StopService, effects.first())
        assertTrue(effects.last() is VoiceEffect.EndedHere)
        assertFalse(c.active)
    }

    // ---- the silent stall: "Listening" while nothing listens ----

    @Test fun aWatchdogFailureEndsTheSessionWithTheReasonAndReleasesTheMicrophone() {
        val c = started()
        val note = VoiceWatchdog.Reason.NOT_RESPONDING.note
        assertEquals(listOf(VoiceEffect.SendStop, VoiceEffect.StopService, VoiceEffect.EndedHere(note)), c.onFailure(note))
        assertFalse(c.active)
    }

    @Test fun aFailureForASessionThatIsAlreadyOverDoesNothingTwice() {
        val c = started()
        c.onFailure("x")
        assertEquals(emptyList(), c.onFailure("x"))
    }

    @Test fun aWatchdogFailureCanBeFollowedByARestart() {
        val c = started()
        c.onFailure(VoiceWatchdog.Reason.CAPTURE_STALLED.note)
        assertEquals(listOf(VoiceEffect.StartSession, VoiceEffect.StartService), c.onUserStart(true, true))
        assertTrue(c.active)
    }

    // Exactly one live session per phone: a resume re-attaches, it never asks the server for a second one.
    @Test fun noReplayAndNoReconnectEverStartsASecondSession() {
        for (active in listOf(true, false)) for (voice in listOf(device, ReplayVoice.NONE, ReplayVoice(true, "live", "laptop"))) for (canStart in listOf(true, false)) {
            val c = VoiceController()
            if (active) c.onUserStart(true, true)
            c.onLink(open = false, now = 0)
            c.onLink(open = true, now = 1)
            val effects = c.onReplay(voice, canStart)
            assertFalse(VoiceEffect.StartSession in effects, "active=$active voice=$voice canStart=$canStart")
        }
    }

    @Test fun aSecondStartTapWhileLiveOrReconnectingSendsNothing() {
        val c = started()
        c.onLink(open = false, now = 0)
        assertEquals(emptyList(), c.onUserStart(true, linkOpen = false))
        c.onLink(open = true, now = 5)
        assertEquals(emptyList(), c.onUserStart(true, linkOpen = true))
    }

    @Test fun aLaptopVoiceSessionIsNeverOurMicrophone() {
        val c = VoiceController()
        assertEquals(emptyList(), c.onReplay(ReplayVoice(true, "live", "laptop"), canStartService = true))
        assertFalse(c.active)
    }

    // ---- the link ----

    @Test fun aLostLinkGivesTheSessionAMinuteThenReleasesTheMicrophone() {
        val c = started()
        val deadline = c.onLink(open = false, now = 1_000)
        assertEquals(1_000 + VoiceController.RECONNECT_LIMIT_MS, deadline)
        assertEquals(emptyList(), c.onDeadline(now = 1_000 + VoiceController.RECONNECT_LIMIT_MS - 1))
        assertTrue(c.active)
        val effects = c.onDeadline(now = 1_000 + VoiceController.RECONNECT_LIMIT_MS)
        assertEquals(VoiceEffect.StopService, effects.first())
        assertTrue(effects.any { it is VoiceEffect.EndedHere })
        assertFalse(c.active)
    }

    @Test fun theDeadlineIsKeptAcrossRepeatedLossesNotRestarted() {
        val c = started()
        c.onLink(false, 1_000)
        assertEquals(1_000 + VoiceController.RECONNECT_LIMIT_MS, c.onLink(false, 30_000))
    }

    @Test fun aLinkThatComesBackCancelsTheDeadline() {
        val c = started()
        c.onLink(false, 0)
        assertNull(c.onLink(true, 10_000))
        assertEquals(emptyList(), c.onDeadline(now = 1_000_000))
        assertTrue(c.active)
    }

    @Test fun aLossWithNoVoiceSetsNoDeadline() {
        assertNull(VoiceController().onLink(false, 0))
    }

    // ---- mute and audio focus ----

    @Test fun muteOnlyExistsWhileVoiceRuns() {
        val c = VoiceController()
        assertFalse(c.toggleMute())
        started(c)
        assertTrue(c.toggleMute())
        assertFalse(c.toggleMute())
    }

    @Test fun muteDoesNotSurviveTheSession() {
        val c = started()
        c.toggleMute()
        c.onUserStop()
        assertFalse(c.muted)
        assertFalse(started(c).muted)
    }

    @Test fun frameForSendsSilenceOfTheSameLengthWhenMuted() {
        val pcm = byteArrayOf(1, 2, 3, 4, 5, 6)
        assertTrue(VoiceView.frameFor(pcm.copyOf(), 4, muted = false).contentEquals(pcm))
        val silent = VoiceView.frameFor(pcm.copyOf(), 4, muted = true)
        assertEquals(listOf<Byte>(0, 0, 0, 0, 5, 6), silent.toList())
    }

    @Test fun serverFrameTypeUsedByTheControllerIsAVoiceState() {
        // Guards the contract the tests above rely on: the controller reads `phase`, not free text.
        assertTrue(ChatFrames.parse("""{"t":"voice_state","phase":"stopped","text":""}""") is ServerFrame.VoiceState)
    }
}
