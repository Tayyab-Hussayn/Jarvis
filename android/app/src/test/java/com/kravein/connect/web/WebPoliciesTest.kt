package com.kravein.connect.web

import com.kravein.connect.core.DeviceCredential
import com.kravein.connect.network.UiSessionResult
import com.kravein.connect.network.UiToken
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

class UiTokenHolderTest {
    private val credential = DeviceCredential("dev1", "secret", "Pixel", "192.168.1.5", 8787)
    private var now = 1_000L
    private var calls = 0
    private var next: UiSessionResult = granted("t1", 1_900)

    private fun granted(value: String, expiresAt: Long) = UiSessionResult.Granted(UiToken(value, expiresAt, 900))
    private val holder = UiTokenHolder(fetch = { calls++; next }, nowSeconds = { now })

    @Test
    fun `fetches once and reuses a comfortably valid token`() {
        assertEquals("t1", ((holder.obtain(credential)) as UiSessionResult.Granted).token.value)
        holder.obtain(credential)
        assertEquals(1, calls)
    }

    @Test
    fun `refreshes inside the margin before expiry`() {
        holder.obtain(credential)
        next = granted("t2", 2_800)
        now = 1_850 // 50 s before expires_at=1900, margin is 60 s
        assertEquals("t2", (holder.obtain(credential) as UiSessionResult.Granted).token.value)
        assertEquals(2, calls)
    }

    @Test
    fun `forceNew drops the cache after a 401 from the page`() {
        holder.obtain(credential)
        next = granted("t2", 1_900)
        holder.obtain(credential, forceNew = true)
        assertEquals(2, calls)
    }

    @Test
    fun `a refused credential is returned and nothing stays cached`() {
        holder.obtain(credential)
        next = UiSessionResult.Refused
        assertEquals(UiSessionResult.Refused, holder.obtain(credential, forceNew = true))
        next = granted("t3", 1_900)
        holder.obtain(credential)
        assertEquals(3, calls)
    }

    @Test
    fun `a different device never gets another device's token`() {
        holder.obtain(credential)
        holder.obtain(credential.copy(deviceId = "dev2"))
        assertEquals(2, calls)
    }

    @Test
    fun `token is redacted in toString`() {
        assertFalse(UiToken("super-secret", 5, 5).toString().contains("super-secret"))
    }
}

class NavigationPolicyTest {
    private val policy = NavigationPolicy("192.168.1.5", 8787)

    @Test
    fun `paired origin is allowed`() {
        assertEquals(NavigationPolicy.Decision.Allow, policy.decide("http://192.168.1.5:8787/"))
        assertEquals(NavigationPolicy.Decision.Allow, policy.decide("http://192.168.1.5:8787/api/x?y=1"))
    }

    @Test
    fun `other host, port or scheme goes to the system browser`() {
        for (url in listOf(
            "http://192.168.1.6:8787/", "http://192.168.1.5:9999/", "https://192.168.1.5:8787/",
            "https://example.com/", "http://192.168.1.5.evil.com:8787/", "http://192.168.1.5@evil.com:8787/",
        )) {
            assertEquals(NavigationPolicy.Decision.OpenExternally, policy.decide(url), url)
        }
    }

    @Test
    fun `local and script schemes are blocked outright`() {
        for (url in listOf("file:///etc/passwd", "content://x/y", "javascript:alert(1)", "intent://x#Intent;end", "not a url", "")) {
            assertEquals(NavigationPolicy.Decision.Block, policy.decide(url), url)
        }
    }

    @Test
    fun `host comparison ignores case`() {
        assertTrue(NavigationPolicy("PC.local", 8787).isPairedOrigin("http://pc.LOCAL:8787/"))
    }

    @Test
    fun `ipv6 host is bracketed in the origin and matched`() {
        val v6 = NavigationPolicy("fd00::5", 8787)
        assertEquals("http://[fd00::5]:8787", v6.origin())
        assertTrue(v6.isPairedOrigin("http://[fd00::5]:8787/"))
    }

    @Test
    fun `authenticated url carries the token and keeps the page`() {
        assertEquals("http://192.168.1.5:8787/?token=abc", policy.authenticatedUrl(null, "abc"))
        assertEquals(
            "http://192.168.1.5:8787/apps?tab=1&token=abc",
            policy.authenticatedUrl("http://192.168.1.5:8787/apps?tab=1&token=old", "abc"),
        )
    }

    @Test
    fun `authenticated url never follows a foreign last url and encodes the token`() {
        assertEquals("http://192.168.1.5:8787/?token=a%26b", policy.authenticatedUrl("https://evil.com/x", "a&b"))
    }

    @Test
    fun `withoutToken strips the token and refuses foreign urls`() {
        assertEquals("http://192.168.1.5:8787/apps?tab=1", policy.withoutToken("http://192.168.1.5:8787/apps?tab=1&token=old"))
        assertEquals("http://192.168.1.5:8787/", policy.withoutToken("http://192.168.1.5:8787/?token=old"))
        assertEquals(null, policy.withoutToken("https://evil.com/?token=old"))
        assertNotNull(policy.withoutToken("http://192.168.1.5:8787"))
    }
}

class PagePermissionPolicyTest {
    private val origin = "http://192.168.1.5:8787"
    private val both = listOf(PagePermissionPolicy.AUDIO, PagePermissionPolicy.VIDEO)

    @Test
    fun `grants capture resources only when the runtime permission is held`() {
        assertEquals(listOf(PagePermissionPolicy.AUDIO), PagePermissionPolicy.grant(origin, origin, both, true, false))
        assertEquals(both, PagePermissionPolicy.grant(origin, origin, both, true, true))
        assertEquals(emptyList(), PagePermissionPolicy.grant(origin, origin, both, false, false))
    }

    @Test
    fun `refuses any other origin`() {
        assertEquals(emptyList(), PagePermissionPolicy.grant("https://evil.com", origin, both, true, true))
    }

    @Test
    fun `accepts the trailing slash WebView adds and drops unknown resources`() {
        val asked = listOf(PagePermissionPolicy.AUDIO, "android.webkit.resource.MIDI_SYSEX")
        assertEquals(listOf(PagePermissionPolicy.AUDIO), PagePermissionPolicy.grant("$origin/", origin, asked, true, true))
    }

    @Test
    fun `the loopback pipe origin is the paired origin, another loopback port is not`() {
        val pipe = NavigationPolicy("127.0.0.1", 41234, "http").origin()
        val audio = listOf(PagePermissionPolicy.AUDIO)
        assertEquals(audio, PagePermissionPolicy.grant("http://127.0.0.1:41234/", pipe, audio, true, false))
        assertEquals(emptyList(), PagePermissionPolicy.grant("http://127.0.0.1:41235", pipe, audio, true, false))
        assertEquals(emptyList(), PagePermissionPolicy.grant("http://127.0.0.1:41234", pipe, audio, false, false))
    }
}

class WebViewLifecyclePolicyTest {
    private val policy = WebViewLifecyclePolicy(idleLimitSeconds = 300)

    @Test
    fun `created lazily on the first tab show only`() {
        assertFalse(policy.isCreated)
        assertTrue(policy.onTabShown())
        assertFalse(policy.onTabShown())
    }

    @Test
    fun `a short background keeps the webview`() {
        policy.onTabShown()
        policy.onBackground(1_000)
        assertFalse(policy.onForeground(1_299))
    }

    @Test
    fun `a long background destroys it on return`() {
        policy.onTabShown()
        policy.onBackground(1_000)
        assertTrue(policy.onForeground(1_300))
    }

    @Test
    fun `the idle timer destroys it while still in the background`() {
        policy.onTabShown()
        policy.onBackground(1_000)
        assertFalse(policy.shouldDestroyNow(1_100))
        assertTrue(policy.shouldDestroyNow(1_300))
    }

    @Test
    fun `nothing to destroy when never created or when in the foreground`() {
        policy.onBackground(0)
        assertFalse(policy.shouldDestroyNow(10_000))
        policy.onTabShown()
        policy.onForeground(10_000)
        assertFalse(policy.shouldDestroyNow(20_000))
    }

    @Test
    fun `can be created again after being destroyed`() {
        policy.onTabShown()
        policy.onDestroyed()
        assertTrue(policy.onTabShown())
    }
}
