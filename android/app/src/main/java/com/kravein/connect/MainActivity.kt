package com.kravein.connect

import android.Manifest
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.os.Build
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import com.kravein.connect.confirm.ConfirmationNotifier
import com.kravein.connect.core.AgentStateStore
import com.kravein.connect.core.ShellNavigation
import com.kravein.connect.core.UiPrefs
import com.kravein.connect.pairing.PairingStorage
import com.kravein.connect.ui.KraveinConnectApp
import com.kravein.connect.ui.theme.KraveinConnectTheme
import com.kravein.connect.web.KraveinWebController
import com.kravein.connect.web.ShareText

class MainActivity : ComponentActivity() {
    private lateinit var storage: PairingStorage
    private lateinit var uiPrefs: UiPrefs
    private var pendingServiceStart = false

    private val cameraPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { _ ->
        // The UI will react by showing the scanner if permission is granted.
    }

    private val notificationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        // Denied is fine: the connection and the web UI work without notifications; only the Approve/Decline
        // notifications are lost, and the Kravein tab still shows every confirmation.
        if (!granted) AgentStateStore.addLog("Notifications are off: confirmations will only show inside the app.")
        if (pendingServiceStart) {
            pendingServiceStart = false
            startPhoneService()
        }
    }

    private val pagePermissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { _ ->
        // The page asks again on its next attempt; nothing to do with the answer here.
    }

    private lateinit var web: KraveinWebController

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
        super.onCreate(savedInstanceState)
        web = KraveinWebController(applicationContext, requestRuntimePermissions = ::askPagePermissions)
        storage = PairingStorage(this)
        uiPrefs = UiPrefs(this)
        AgentStateStore.setCredential(storage.loadCredential())
        if (savedInstanceState == null) handleIntent(intent)
        maybeStartService()
        ensureCameraPermission()
        // Light theme needs dark status/navigation icons; the window colours already come from the theme.
        val night = (resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES
        WindowCompat.getInsetsController(window, window.decorView).apply {
            isAppearanceLightStatusBars = !night
            isAppearanceLightNavigationBars = !night
        }
        setContent {
            KraveinConnectTheme {
                KraveinConnectApp(
                    onRequestCameraPermission = {
                        cameraPermission.launch(Manifest.permission.CAMERA)
                    },
                    onRequestNotificationPermission = {
                        notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
                    },
                    onStartService = { maybeStartService() },
                    web = web,
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent) {
        when (intent.action) {
            ConfirmationNotifier.ACTION_OPEN_KRAVEIN -> ShellNavigation.requestKraveinTab()
            Intent.ACTION_SEND -> {
                // Another app's text: it goes to the composer only, and is dropped from the intent so a re-delivery
                // (rotation-free recreation, task restore) cannot fill the composer a second time.
                val text = ShareText.clean(intent.getCharSequenceExtra(Intent.EXTRA_TEXT))
                intent.removeExtra(Intent.EXTRA_TEXT)
                if (text == null) return
                if (AgentStateStore.credential.value == null) {
                    // The pairing screen is already what the user sees; say why their share went nowhere.
                    Toast.makeText(this, "Pair this phone with Kravein first, then share again.", Toast.LENGTH_LONG).show()
                    return
                }
                web.shareText(text)
                ShellNavigation.requestKraveinTab()
            }
        }
    }

    override fun onStart() {
        super.onStart()
        web.onStart()
    }

    override fun onStop() {
        web.onStop()
        super.onStop()
    }

    override fun onDestroy() {
        // Config changes are handled in the manifest, so this is a real exit and the WebView must not leak.
        if (isFinishing) web.close()
        super.onDestroy()
    }

    private fun maybeStartService() {
        if (AgentStateStore.credential.value != null) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED &&
                !uiPrefs.notificationsPrompted
            ) {
                uiPrefs.notificationsPrompted = true
                pendingServiceStart = true
                notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
                return
            }
            startPhoneService()
        }
    }

    private fun ensureCameraPermission() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            cameraPermission.launch(Manifest.permission.CAMERA)
        }
    }

    private fun startPhoneService() {
        if (AgentStateStore.credential.value != null) {
            val intent = Intent(this, KraveinConnectForegroundService::class.java)
            ContextCompat.startForegroundService(this, intent)
        }
    }
}
