package com.kravein.connect.network

import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit

/**
 * The one OkHttp client of the app. Pairing, the UI-session call and the phone socket derive from it with
 * `newBuilder()`, which shares its connection pool and dispatcher instead of starting a second set of threads.
 */
object Http {
    val client: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(10, TimeUnit.SECONDS)
            .callTimeout(15, TimeUnit.SECONDS)
            .build()
    }
}
