package com.kravein.connect.web

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.view.ViewGroup
import android.webkit.PermissionRequest
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.core.content.ContextCompat
import com.kravein.connect.core.DeviceCredential
import com.kravein.connect.ui.theme.KraveinTokens
import com.kravein.connect.network.UiSessionClient
import com.kravein.connect.network.UiSessionResult
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.ByteArrayInputStream

sealed interface WebUiState {
    data object Idle : WebUiState
    data object Loading : WebUiState
    data object Ready : WebUiState
    data class Offline(val message: String) : WebUiState

    /** The server no longer accepts this device. Only pairing again helps. */
    data object NeedsPairing : WebUiState
}

/**
 * Owns the one WebView of the app: builds it when the Kravein tab is first shown, keeps it hardened, feeds it a fresh
 * UI token, and throws it away after the app has been in the background a while. All thread hops live here so the
 * decisions stay in the pure classes of WebPolicies.kt.
 */
class KraveinWebController(
    private val appContext: Context,
    private val tokens: UiTokenHolder = UiTokenHolder(
        fetch = UiSessionClient()::fetch,
        nowSeconds = { System.currentTimeMillis() / 1000 },
    ),
    private val nowSeconds: () -> Long = { System.currentTimeMillis() / 1000 },
    /** Asks the activity for Android runtime permissions the page needs (microphone / camera). */
    private val requestRuntimePermissions: (Array<String>) -> Unit = {},
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val handler = Handler(Looper.getMainLooper())
    private val lifecycle = WebViewLifecyclePolicy()
    private val destroyWhenIdle = Runnable { if (lifecycle.shouldDestroyNow(nowSeconds())) destroy() }

    private val _state = MutableStateFlow<WebUiState>(WebUiState.Idle)
    val state: StateFlow<WebUiState> = _state.asStateFlow()

    private var webView: WebView? = null
    private var credential: DeviceCredential? = null
    private var navigation: NavigationPolicy? = null
    private var lastUrl: String? = null
    private var loadJob: Job? = null
    private var retriedAfter401 = false

    /** The view for the tab; built on first use. It may still have a parent from an earlier composition. */
    fun viewFor(credential: DeviceCredential): WebView {
        bind(credential)
        webView?.let { view ->
            (view.parent as? ViewGroup)?.removeView(view)
            return view
        }
        lifecycle.onTabShown()
        val view = build()
        webView = view
        load(forceNewToken = false)
        return view
    }

    /** The user pressed Reload / Retry. A new token every time: it is cheap and rules out an expired one. */
    fun reload() {
        retriedAfter401 = false
        load(forceNewToken = true)
    }

    fun canGoBack(): Boolean = webView?.canGoBack() == true

    fun goBack() {
        webView?.goBack()
    }

    fun onStop() {
        webView?.onPause()
        lifecycle.onBackground(nowSeconds())
        handler.postDelayed(destroyWhenIdle, IDLE_MS)
    }

    fun onStart() {
        handler.removeCallbacks(destroyWhenIdle)
        if (lifecycle.onForeground(nowSeconds())) {
            destroy()
            return
        }
        webView?.let {
            it.onResume()
            // The token the page holds may have expired while the app slept; a fresh one only costs a reload when
            // the cached one is within its refresh margin.
            if (_state.value is WebUiState.Ready) refreshIfTokenStale()
        }
    }

    /** Unpaired or credential changed: nothing of the old session may remain. */
    fun reset() {
        destroy()
        tokens.clear()
        lastUrl = null
        credential = null
        navigation = null
    }

    fun close() {
        destroy()
        scope.cancel()
    }

    private fun bind(newCredential: DeviceCredential) {
        val current = credential
        if (current == null || current.deviceId != newCredential.deviceId || current.host != newCredential.host ||
            current.port != newCredential.port
        ) {
            if (webView != null) destroy()
            tokens.clear()
            lastUrl = null
        }
        credential = newCredential
        navigation = NavigationPolicy(newCredential.host, newCredential.port)
    }

    private fun refreshIfTokenStale() {
        val current = credential ?: return
        scope.launch {
            // obtain() returns the cached token untouched while it is valid, so compare by asking for a reload only
            // when a different token came back.
            val before = (withContext(Dispatchers.IO) { tokens.obtain(current) } as? UiSessionResult.Granted)?.token
            if (before != null && before !== lastLoadedToken) load(forceNewToken = false)
        }
    }

    private var lastLoadedToken: Any? = null

    private fun load(forceNewToken: Boolean) {
        val current = credential ?: return
        val policy = navigation ?: return
        loadJob?.cancel()
        _state.value = WebUiState.Loading
        loadJob = scope.launch {
            val result = withContext(Dispatchers.IO) { tokens.obtain(current, forceNewToken) }
            when (result) {
                is UiSessionResult.Granted -> {
                    lastLoadedToken = result.token
                    webView?.loadUrl(policy.authenticatedUrl(lastUrl, result.token.value))
                }
                UiSessionResult.Refused -> _state.value = WebUiState.NeedsPairing
                is UiSessionResult.Unreachable -> _state.value = WebUiState.Offline(result.message)
                is UiSessionResult.Failed -> _state.value = WebUiState.Offline(result.message)
            }
        }
    }

    private fun destroy() {
        handler.removeCallbacks(destroyWhenIdle)
        loadJob?.cancel()
        webView?.let { view ->
            (view.parent as? ViewGroup)?.removeView(view)
            view.stopLoading()
            view.webViewClient = WebViewClient()
            view.destroy()
        }
        webView = null
        lastLoadedToken = null
        lifecycle.onDestroyed()
        _state.value = WebUiState.Idle
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun build(): WebView {
        val view = WebView(appContext)
        // The web page's own --bg, so the frames before it paints match it (the web UI follows the system theme).
        val night = (appContext.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES
        view.setBackgroundColor(KraveinTokens.background(night))
        view.settings.apply {
            javaScriptEnabled = true // the Kravein UI is a JavaScript app
            domStorageEnabled = true // it keeps its token in sessionStorage
            allowFileAccess = false
            allowContentAccess = false
            @Suppress("DEPRECATION")
            allowFileAccessFromFileURLs = false
            @Suppress("DEPRECATION")
            allowUniversalAccessFromFileURLs = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            setSupportMultipleWindows(false)
            setGeolocationEnabled(false)
            mediaPlaybackRequiresUserGesture = false // voice replies play without a tap
            cacheMode = WebSettings.LOAD_DEFAULT
        }
        // No addJavascriptInterface anywhere: the page gets no handle on the app.
        view.webViewClient = Client()
        view.webChromeClient = Chrome()
        return view
    }

    private inner class Client : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val policy = navigation ?: return true
            when (policy.decide(request.url.toString())) {
                NavigationPolicy.Decision.Allow -> return false
                NavigationPolicy.Decision.OpenExternally -> openInBrowser(request.url)
                NavigationPolicy.Decision.Block -> Unit
            }
            return true
        }

        // Sub-resources and fetches too: the page may not pull anything from outside the paired origin.
        override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
            val policy = navigation ?: return refused()
            val url = request.url.toString()
            val scheme = request.url.scheme?.lowercase()
            if (scheme == "data" || scheme == "blob" || scheme == "about" || policy.isPairedOrigin(url)) return null
            return refused()
        }

        override fun onPageFinished(view: WebView, url: String?) {
            // Remember where the user was, token-free, so a rebuilt WebView comes back to the same page.
            url?.let { navigation?.withoutToken(it) }?.let { lastUrl = it }
            if (_state.value is WebUiState.Loading) _state.value = WebUiState.Ready
        }

        override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, errorResponse: WebResourceResponse) {
            if (!request.isForMainFrame) return
            if (errorResponse.statusCode == 401) {
                if (retriedAfter401) {
                    _state.value = WebUiState.NeedsPairing
                } else {
                    retriedAfter401 = true
                    load(forceNewToken = true)
                }
            } else {
                _state.value = WebUiState.Offline("The PC answered with an error (${errorResponse.statusCode}).")
            }
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (request.isForMainFrame) {
                _state.value = WebUiState.Offline("Cannot reach Kravein on your PC. Is it running and on the same network?")
            }
        }

        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            // The renderer died (usually memory pressure). Drop the dead view; the tab builds a new one.
            val current = credential
            destroy()
            if (current != null) _state.value = WebUiState.Offline("The page stopped. Tap Retry to reload it.")
            return true
        }

        private fun refused() = WebResourceResponse("text/plain", "utf-8", 403, "Blocked", emptyMap(), ByteArrayInputStream(ByteArray(0)))

        private fun openInBrowser(uri: Uri) {
            try {
                appContext.startActivity(Intent(Intent.ACTION_VIEW, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            } catch (_: Exception) {
                // No browser installed: the link simply does nothing.
            }
        }
    }

    private inner class Chrome : WebChromeClient() {
        override fun onPermissionRequest(request: PermissionRequest) {
            val paired = navigation?.origin()
            val audio = held(Manifest.permission.RECORD_AUDIO)
            val camera = held(Manifest.permission.CAMERA)
            val granted = if (paired == null) emptyList() else PagePermissionPolicy.grant(
                request.origin.toString(), paired, request.resources.toList(), audio, camera,
            )
            if (granted.isNotEmpty()) {
                request.grant(granted.toTypedArray())
                return
            }
            request.deny()
            // The page is ours but the user never allowed the microphone/camera to the app: ask now, the page can ask again.
            if (paired != null && request.origin.toString().trimEnd('/') == paired) {
                val missing = buildList {
                    if (PagePermissionPolicy.AUDIO in request.resources && !audio) add(Manifest.permission.RECORD_AUDIO)
                    if (PagePermissionPolicy.VIDEO in request.resources && !camera) add(Manifest.permission.CAMERA)
                }
                if (missing.isNotEmpty()) requestRuntimePermissions(missing.toTypedArray())
            }
        }

        // Geolocation is off in the settings; nothing else needs a chrome client override.
    }

    private fun held(permission: String) =
        ContextCompat.checkSelfPermission(appContext, permission) == PackageManager.PERMISSION_GRANTED

    private companion object {
        const val IDLE_MS = 5 * 60 * 1000L
    }
}
