package com.kravein.connect.network

import com.kravein.connect.core.DeviceCredential
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import java.io.IOException
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertNull

/** The client against a canned server: an interceptor answers instead of the network, so no extra test dependency. */
class UiSessionClientTest {
    private val credential = DeviceCredential("dev1", "s3cret", "Pixel", "192.168.1.5", 8787)
    private var seen: Request? = null

    private fun client(code: Int = 200, body: String = "", fail: Boolean = false) = UiSessionClient { _, _ ->
        OkHttpClient.Builder().addInterceptor(Interceptor { chain ->
            seen = chain.request()
            if (fail) throw IOException("down")
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(code).message("x")
                .body(body.toResponseBody("application/json".toMediaType())).build()
        }).build()
    }

    @Test
    fun `success returns the token and sends the device header without an origin`() {
        val result = client(body = """{"token":"abc","expires_at":1900,"ttl_seconds":900}""").fetch(credential)
        assertEquals("abc", assertIs<UiSessionResult.Granted>(result).token.value)
        val request = seen!!
        assertEquals("POST", request.method)
        assertEquals("http://192.168.1.5:8787/phone/ui-session", request.url.toString())
        assertEquals("Device dev1:s3cret", request.header("Authorization"))
        assertNull(request.header("Origin"))
    }

    @Test
    fun `401 means the credential was refused`() {
        assertIs<UiSessionResult.Refused>(client(code = 401).fetch(credential))
    }

    @Test
    fun `other errors and bad bodies are failures, not refusals`() {
        assertIs<UiSessionResult.Failed>(client(code = 500).fetch(credential))
        assertIs<UiSessionResult.Failed>(client(body = "{}").fetch(credential))
    }

    @Test
    fun `network failure is unreachable`() {
        assertIs<UiSessionResult.Unreachable>(client(fail = true).fetch(credential))
    }
}
