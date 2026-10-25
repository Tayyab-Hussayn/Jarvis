package com.kravein.connect.web

import android.Manifest
import android.app.AlertDialog
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.Process
import android.webkit.WebView
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import com.kravein.connect.R
import com.kravein.connect.pairing.PairingStorage
import com.kravein.connect.ui.KraveinWebScreen
import com.kravein.connect.ui.theme.KraveinConnectTheme

/**
 * The Kravein web UI in its own process (`android:process=":web"`, see the manifest). Everything the native app does
 * daily is native; this is for the pages that are not. The process is ended two minutes after the screen goes out of
 * sight because that is the only way to give back Chromium's renderer memory.
 */
class WebActivity : ComponentActivity() {
    private lateinit var web: KraveinWebController
    private val policy = WebProcessPolicy()
    private val handler = Handler(Looper.getMainLooper())
    private val exitWhenIdle = Runnable { if (policy.shouldExit(nowSeconds())) exitProcess() }

    private val pagePermissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { _ ->
        // The page asks again on its next attempt; nothing to do with the answer here.
    }

    /** The page asked for the microphone: say why before the system prompt. "Not now" leaves the page with a clean error. */
    private fun askPagePermissions(permissions: Array<String>) {
        if (Manifest.permission.RECORD_AUDIO !in permissions) {
            pagePermissions.launch(permissions)
            return
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.mic_rationale_title)
            .setMessage(R.string.mic_rationale_body)
            .setPositiveButton(R.string.mic_rationale_continue) { _, _ -> pagePermissions.launch(permissions) }
            .setNegativeButton(R.string.mic_rationale_not_now, null)
            .show()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        // Before the first WebView of this process. The main process may hold the default Chromium data directory
        // (the onboarding background is a WebView); two processes on one directory crash with "Using WebView from
        // more than one process at once". This process is only ever the web UI, so it gets its own directory.
        WebView.setDataDirectorySuffix("kravein_web")
        super.onCreate(savedInstanceState)
        // This process has its own copy of every singleton, so it reads the credential itself (encrypted storage).
        val credential = PairingStorage(this).loadCredential()
        if (credential == null) {
            // Unpaired meanwhile: nothing to show, and nothing of the old session may linger in this process.
            exitProcess()
            return
        }
        web = KraveinWebController(applicationContext, requestRuntimePermissions = ::askPagePermissions)
        setContent {
            KraveinConnectTheme {
                KraveinWebScreen(credential = credential, web = web, onPairAgain = { finish() }, onExit = { finish() })
            }
        }
    }

    override fun onStart() {
        super.onStart()
        policy.onStart()
        handler.removeCallbacks(exitWhenIdle)
        web.onStart()
    }

    override fun onStop() {
        web.onStop()
        policy.onStop(nowSeconds())
        handler.postDelayed(exitWhenIdle, policy.idleMillis)
        super.onStop()
    }

    override fun onDestroy() {
        if (isFinishing && ::web.isInitialized) web.close()
        super.onDestroy()
        // Leaving the screen on purpose ends the process at once: the renderer is the cost, not the activity.
        if (isFinishing) exitProcess()
    }

    private fun nowSeconds() = System.currentTimeMillis() / 1000

    private fun exitProcess() {
        finishAndRemoveTask()
        Process.killProcess(Process.myPid())
    }
}
