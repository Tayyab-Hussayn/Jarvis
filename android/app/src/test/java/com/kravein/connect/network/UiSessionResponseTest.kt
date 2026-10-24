package com.kravein.connect.network

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull

class UiSessionResponseTest {
    @Test
    fun `parses the documented reply`() {
        val token = assertNotNull(UiSessionResponse.parse("""{"token":"abc","expires_at":1900,"ttl_seconds":900}"""))
        assertEquals("abc", token.value)
        assertEquals(1900L, token.expiresAt)
        assertEquals(900L, token.ttlSeconds)
    }

    @Test
    fun `rejects malformed, missing, mistyped and oversized fields`() {
        for (body in listOf(
            "", "not json", "[]", "{}",
            """{"token":"","expires_at":1,"ttl_seconds":1}""",
            """{"token":5,"expires_at":1,"ttl_seconds":1}""",
            """{"token":"a","expires_at":"soon","ttl_seconds":1}""",
            """{"token":"a","expires_at":1}""",
            """{"token":"a","expires_at":-1,"ttl_seconds":1}""",
            """{"token":"${"x".repeat(513)}","expires_at":1,"ttl_seconds":1}""",
        )) {
            assertNull(UiSessionResponse.parse(body), body)
        }
    }
}
