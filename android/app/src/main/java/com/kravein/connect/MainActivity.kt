package com.kravein.connect

import android.Manifest
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
import com.kravein.connect.chat.ChatHub
import com.kravein.connect.voice.VoiceHub
import com.kravein.connect.web.ShareText

class MainActivity : ComponentActivity() {
    private lateinit var storage: PairingStorage
    private lateinit var uiPrefs: UiPrefs
    private var pendingServiceStart = false

    private val cameraPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { _ ->
        // The UI will react by showing the scanner if permission is granted.
    }

    // Voice: the Chat explained why first; the answer goes straight back to the voice side, which starts or says why not.
    private val micPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        VoiceHub.onMicResult(granted)
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

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        storage = PairingStorage(this)
        uiPrefs = UiPrefs(this)
        AgentStateStore.setCredential(storage.loadCredential())
        if (savedInstanceState == null) handleIntent(intent)
        maybeStartService()
        ensureCameraPermission()
        // Compose must receive the insets itself (keyboard included); otherwise the system resizes the window on
        // some devices and the nav bar rides up above the keys with no inset for us to react to.
        WindowCompat.setDecorFitsSystemWindows(window, false)
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
                    onRequestMicPermission = { micPermission.launch(Manifest.permission.RECORD_AUDIO) },
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
                // Lands in the native composer; it is never sent without the user pressing Send.
                ChatHub.offer(text)
                ShellNavigation.requestKraveinTab()
            }
        }
    }

    override fun onStart() {
        super.onStart()
        // The chat socket is open only while the app is visible.
        ChatHub.onVisible(true)
    }

    override fun onStop() {
        ChatHub.onVisible(false)
        super.onStop()
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
