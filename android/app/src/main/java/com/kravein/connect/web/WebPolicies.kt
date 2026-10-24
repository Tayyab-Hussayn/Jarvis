package com.kravein.connect.web

import com.kravein.connect.core.CertPin
import com.kravein.connect.core.DeviceCredential
import com.kravein.connect.network.UiSessionResult
import com.kravein.connect.network.UiToken
import java.net.URI
import java.net.URISyntaxException

/**
 * Holds the current UI token in memory. It is never written anywhere: a process restart simply asks again,
 * which is cheap and keeps the token out of every backup, log and crash report.
 */
class UiTokenHolder(
    private val fetch: (DeviceCredential) -> UiSessionResult,
    private val nowSeconds: () -> Long,
    private val refreshMarginSeconds: Long = 60,
) {
    private var cached: UiToken? = null
    private var cachedFor: String? = null

    /** The cached token while it is comfortably valid, otherwise a new one. [forceNew] drops the cache (a 401 from the page). */
    @Synchronized
    fun obtain(credential: DeviceCredential, forceNew: Boolean = false): UiSessionResult {
        val current = cached
        if (!forceNew && current != null && cachedFor == credential.deviceId &&
            current.expiresAt - refreshMarginSeconds > nowSeconds()
        ) {
            return UiSessionResult.Granted(current)
        }
        val result = fetch(credential)
        if (result is UiSessionResult.Granted) {
            cached = result.token
            cachedFor = credential.deviceId
        } else {
            clear()
        }
        return result
    }

    @Synchronized
    fun clear() {
        cached = null
        cachedFor = null
    }
}

/**
 * The only SSL bypass in the app. The WebView cannot use a custom trust manager, so it reports the self-signed
 * certificate as an error; that error may be overridden only for the paired origin, only when the certificate's DER
 * SHA-256 equals the pin from the QR. A missing certificate, missing pin or any other origin cancels.
 */
object SslErrorPolicy {
    fun mayProceed(navigation: NavigationPolicy?, pin: String?, errorUrl: String?, der: ByteArray?): Boolean =
        navigation != null && errorUrl != null && navigation.isPairedOrigin(errorUrl) && CertPin.matches(der, pin)

    const val MESSAGE =
        "This PC's certificate is not the one in the pairing QR code, so the page was not loaded. " +
            "Scan a new pairing QR code to pair again."
}

/**
 * Which URLs the Kravein tab may show. The page is the paired PC's own UI, so everything on that exact
 * scheme/host/port is allowed and nothing else is: other links go to the system browser, other protocols nowhere.
 */
class NavigationPolicy(host: String, private val port: Int, private val scheme: String = "http") {
    private val host = host.lowercase()

    sealed interface Decision {
        data object Allow : Decision
        data object OpenExternally : Decision
        data object Block : Decision
    }

    fun origin(): String = "$scheme://${hostForUrl()}:$port"

    fun isPairedOrigin(url: String): Boolean {
        val uri = parse(url) ?: return false
        return uri.scheme.equals(scheme, ignoreCase = true) && uri.host?.trim('[', ']')?.lowercase() == host.trim('[', ']') &&
            effectivePort(uri) == port
    }

    fun decide(url: String): Decision {
        val uri = parse(url) ?: return Decision.Block
        return when {
            isPairedOrigin(url) -> Decision.Allow
            uri.scheme.equals("http", ignoreCase = true) || uri.scheme.equals("https", ignoreCase = true) -> Decision.OpenExternally
            // file:, content:, javascript:, intent: and friends never leave the WebView, not even to another app.
            else -> Decision.Block
        }
    }

    /**
     * `/?token=…` for a fresh load; [lastUrl] is the page to come back to (its own token and `ask`, if any, are
     * dropped so a rebuilt page never refills the composer). [ask] is shared text for the composer, already capped.
     */
    fun authenticatedUrl(lastUrl: String?, token: String, ask: String? = null): String {
        val path = lastUrl?.takeIf { isPairedOrigin(it) }?.let(::parse)
        val rawPath = path?.rawPath?.takeIf { it.startsWith("/") } ?: "/"
        val kept = path?.rawQuery.orEmpty().split('&').filter { it.isNotEmpty() && !isSecretParam(it) }
        val extra = listOfNotNull(ask?.let { "ask=" + ShareText.encode(it) }, "token=" + java.net.URLEncoder.encode(token, "UTF-8"))
        val query = (kept + extra).joinToString("&")
        val fragment = path?.rawFragment?.let { "#$it" }.orEmpty()
        return "${origin()}$rawPath?$query$fragment"
    }

    /** The same page without any token, for remembering where the user was. */
    fun withoutToken(url: String): String? {
        val uri = parse(url)?.takeIf { isPairedOrigin(url) } ?: return null
        val kept = uri.rawQuery.orEmpty().split('&').filter { it.isNotEmpty() && !isSecretParam(it) }
        val query = if (kept.isEmpty()) "" else "?" + kept.joinToString("&")
        val fragment = uri.rawFragment?.let { "#$it" }.orEmpty()
        return "${origin()}${uri.rawPath.ifEmpty { "/" }}$query$fragment"
    }

    // `token` is a credential and `ask` is the user's shared text: neither may be remembered in lastUrl.
    private fun isSecretParam(pair: String) = pair.startsWith("token=") || pair.startsWith("ask=")

    private fun hostForUrl() = if (host.contains(':') && !host.startsWith("[")) "[$host]" else host

    private fun effectivePort(uri: URI) = if (uri.port == -1) (if (scheme == "https") 443 else 80) else uri.port

    private fun parse(url: String): URI? = try {
        URI(url).takeIf { it.scheme != null }
    } catch (_: URISyntaxException) {
        null
    }
}

/** What a page may ask of the phone's microphone and camera. Pure so the rule can be tested without a WebView. */
object PagePermissionPolicy {
    const val AUDIO = "android.webkit.resource.AUDIO_CAPTURE"
    const val VIDEO = "android.webkit.resource.VIDEO_CAPTURE"

    /**
     * The resources to grant: only for the paired origin, only the two capture kinds, and only the ones whose
     * Android runtime permission the user already granted. Anything else (MIDI, protected media) is dropped.
     */
    fun grant(
        requestOrigin: String,
        pairedOrigin: String,
        requested: List<String>,
        audioPermitted: Boolean,
        cameraPermitted: Boolean,
    ): List<String> {
        if (requestOrigin.trimEnd('/') != pairedOrigin) return emptyList()
        return requested.filter { (it == AUDIO && audioPermitted) || (it == VIDEO && cameraPermitted) }.distinct()
    }
}

/**
 * When the WebView exists. It is created the first time the Kravein tab is shown and destroyed once the app
 * has been in the background for [idleLimitSeconds]; a WebView is the heaviest thing this app holds.
 */
class WebViewLifecyclePolicy(private val idleLimitSeconds: Long = 300) {
    private var backgroundedAt: Long? = null
    private var created = false

    val isCreated: Boolean get() = created

    /** The tab was shown. True when the caller must build a WebView now. */
    fun onTabShown(): Boolean {
        if (created) return false
        created = true
        return true
    }

    fun onBackground(nowSeconds: Long) {
        backgroundedAt = nowSeconds
    }

    /** The app is visible again. True when the WebView outlived the idle limit and must be destroyed and rebuilt. */
    fun onForeground(nowSeconds: Long): Boolean {
        val since = backgroundedAt
        backgroundedAt = null
        return created && since != null && nowSeconds - since >= idleLimitSeconds
    }

    /** The idle timer fired while still in the background. True when the WebView should be destroyed now. */
    fun shouldDestroyNow(nowSeconds: Long): Boolean {
        val since = backgroundedAt ?: return false
        return created && nowSeconds - since >= idleLimitSeconds
    }

    fun onDestroyed() {
        created = false
    }
}
