package com.kravein.connect.web

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class ShareTextTest {
    private val policy = NavigationPolicy("192.168.1.5", 8787)

    @Test
    fun `blank or missing text is not shared`() {
        assertNull(ShareText.clean(null))
        assertNull(ShareText.clean("   \n "))
    }

    @Test
    fun `text is trimmed and capped at 4000 chars`() {
        assertEquals("hello", ShareText.clean("  hello \n"))
        assertEquals(4000, ShareText.clean("a".repeat(5000))!!.length)
        assertEquals("a".repeat(4000), ShareText.clean("a".repeat(4000)))
    }

    @Test
    fun `the cap never splits a surrogate pair`() {
        val text = "a".repeat(3999) + "😀" + "tail"
        val cut = ShareText.clean(text)!!
        assertEquals(3999, cut.length)
        assertFalse(Character.isHighSurrogate(cut.last()))
    }

    @Test
    fun `url carries token and encoded ask together`() {
        val url = policy.authenticatedUrl(null, "tok", ShareText.clean("see https://x.io/a?b=1&c=2 ok"))
        assertEquals("http://192.168.1.5:8787/?ask=see%20https%3A%2F%2Fx.io%2Fa%3Fb%3D1%26c%3D2%20ok&token=tok", url)
    }

    @Test
    fun `non-ascii text round-trips as utf-8 percent encoding`() {
        assertTrue(policy.authenticatedUrl(null, "t", "کیا حال ہے").contains("ask=%DA%A9"))
        assertEquals("100%25%20%2B%201", ShareText.encode("100% + 1"))
    }

    @Test
    fun `no ask means the url is unchanged`() {
        assertEquals("http://192.168.1.5:8787/?token=tok", policy.authenticatedUrl(null, "tok"))
    }

    @Test
    fun `an old ask in the remembered page is never replayed`() {
        val last = "http://192.168.1.5:8787/apps?tab=1&ask=secret&token=old"
        assertEquals("http://192.168.1.5:8787/apps?tab=1&token=new", policy.authenticatedUrl(last, "new"))
        assertEquals("http://192.168.1.5:8787/apps?tab=1", policy.withoutToken(last))
    }
}
