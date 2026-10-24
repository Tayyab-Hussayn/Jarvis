package com.kravein.connect.web

import com.kravein.connect.web.VoiceSessionPolicy.Action
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceSessionPolicyTest {
    @Test
    fun focusFollowsTheMic() {
        val p = VoiceSessionPolicy()
        assertEquals(Action.None, p.onRecording(false))
        assertEquals(Action.TakeAudioFocus, p.onRecording(true))
        assertEquals(Action.None, p.onRecording(true))
        assertEquals(Action.ReleaseAudioFocus, p.onRecording(false))
    }

    @Test
    fun openMicIsStoppedOnBackground() {
        val p = VoiceSessionPolicy()
        assertFalse(p.mustStopOnBackground())
        p.onRecording(true)
        assertTrue(p.mustStopOnBackground())
    }

    @Test
    fun destroyedPageReleasesFocusOnce() {
        val p = VoiceSessionPolicy()
        p.onRecording(true)
        assertEquals(Action.ReleaseAudioFocus, p.onPageGone())
        assertEquals(Action.None, p.onPageGone())
        assertFalse(p.micActive)
    }
}
