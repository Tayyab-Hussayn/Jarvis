package com.kravein.connect.ui

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.kravein.connect.KraveinConnectForegroundService
import com.kravein.connect.core.AgentStateStore
import com.kravein.connect.core.ConnectionState
import androidx.compose.foundation.isSystemInDarkTheme
import com.kravein.connect.ui.theme.GradientButton
import com.kravein.connect.ui.theme.KraveinConnectTheme
import com.kravein.connect.ui.theme.KraveinTheme
import com.kravein.connect.ui.theme.KraveinTokens
import com.kravein.connect.ui.theme.glassBorder
import com.kravein.connect.core.DeviceCredential
import com.kravein.connect.core.DiscoveryStatus
import com.kravein.connect.core.OfferParse
import com.kravein.connect.core.PairingOffer
import com.kravein.connect.core.PairingOfferParser
import com.kravein.connect.core.UiPrefs
import com.kravein.connect.network.DiscoveryText
import com.kravein.connect.network.PairResult
import com.kravein.connect.network.PairingClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import androidx.compose.runtime.rememberCoroutineScope
import com.kravein.connect.pairing.PairingStorage
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import kotlinx.coroutines.launch
import java.util.concurrent.Executors

import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.compose.currentBackStackEntryAsState
import com.kravein.connect.chat.ChatHub
import androidx.compose.ui.draw.clip
import kotlinx.coroutines.delay
import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.material3.Icon
import androidx.compose.material.icons.Icons



@Composable
fun HolographicBackground() {
    AndroidView(
        factory = { ctx ->
            WebView(ctx).apply {
                settings.javaScriptEnabled = true
                settings.allowFileAccess = true
                settings.allowContentAccess = true
                settings.domStorageEnabled = true
                webViewClient = WebViewClient()
                setBackgroundColor(android.graphics.Color.TRANSPARENT)
                loadUrl("file:///android_asset/web_background/index.html")
            }
        },
        modifier = Modifier.fillMaxSize()
    )
}

@Composable
fun KraveinConnectApp(
    onRequestCameraPermission: () -> Unit,
    onRequestNotificationPermission: () -> Unit,
    onStartService: () -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val storage = remember { PairingStorage(context) }
    val pairingClient = remember { PairingClient() }
    val connectionState by AgentStateStore.connectionState.collectAsState()
    val credential by AgentStateStore.credential.collectAsState()
    val status by AgentStateStore.statusText.collectAsState()
    val discovery by AgentStateStore.discovery.collectAsState()
    val lastError by AgentStateStore.lastError.collectAsState()
    val logs by AgentStateStore.logs.collectAsState()

    var showManual by rememberSaveable { mutableStateOf(false) }
    var manualHost by rememberSaveable { mutableStateOf("") }
    var manualPort by rememberSaveable { mutableStateOf("8787") }
    var manualCode by rememberSaveable { mutableStateOf("") }
    var pairError by rememberSaveable { mutableStateOf<String?>(null) }
    var pairing by remember { mutableStateOf(false) }

    val cameraGranted = remember { ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == android.content.pm.PackageManager.PERMISSION_GRANTED }
    val notificationsGranted = remember { Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU || ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == android.content.pm.PackageManager.PERMISSION_GRANTED }

    // Camera is needed to pair. Notifications are only recommended: the screen explains why once, a refusal is
    // respected, and the app works without them.
    val uiPrefs = remember { UiPrefs(context) }
    val setupMissing = listOfNotNull(if (!cameraGranted) "Camera" else null)
    val offerNotifications = !notificationsGranted && !uiPrefs.notificationsPrompted

    val navController = rememberNavController()

    // Determine start destination
    val startDest = when {
        setupMissing.isNotEmpty() || offerNotifications -> "permissions"
        credential != null -> "home"
        else -> "welcome"
    }

    LaunchedEffect(credential) {
        if (credential != null && navController.currentDestination?.route != "home" && navController.currentDestination?.route != "connected_anim") {
            navController.navigate("connected_anim") {
                popUpTo(0)
            }
        }
    }

    // The server refused the stored credential (revoked): reconnecting cannot fix it, so go to pairing.
    LaunchedEffect(connectionState) {
        if (connectionState == ConnectionState.NEEDS_PAIRING) {
            navController.navigate("connect") { popUpTo(0) }
        }
    }

    // Redeem the offer off the main thread; the credential goes straight to encrypted storage.
    fun pair(offer: PairingOffer) {
        pairing = true
        pairError = null
        AgentStateStore.setStatus("Pairing")
        scope.launch {
            val result = withContext(Dispatchers.IO) {
                pairingClient.redeem(offer, Build.MODEL ?: "Android phone").also {
                    if (it is PairResult.Paired) storage.saveCredential(it.credential)
                }
            }
            pairing = false
            when (result) {
                is PairResult.Paired -> {
                    AgentStateStore.setError(null)
                    AgentStateStore.setConnectionState(ConnectionState.CONNECTING)
                    AgentStateStore.setCredential(result.credential)
                    onStartService()
                }
                is PairResult.Failed -> {
                    pairError = result.message
                    AgentStateStore.setStatus("Pairing failed")
                }
            }
        }
    }

    fun startWith(parse: OfferParse) {
        when (parse) {
            is OfferParse.Ok -> pair(parse.offer)
            is OfferParse.Rejected -> pairError = parse.reason
        }
    }

    val nowSeconds = { System.currentTimeMillis() / 1000 }

    val route by navController.currentBackStackEntryAsState()
    // No route yet (first frame) counts as the shell: a paired launch must not start Chromium just to draw the
    // onboarding background for one frame and then keep it resident.
    val inShell = route == null || route?.destination?.route == "home"

    Box(modifier = Modifier.fillMaxSize()) {
        // The 3D background is a second, always-animating WebView: only the onboarding screens get it, never the
        // shell, where it would cost battery and memory behind the real Kravein page.
        if (!inShell) HolographicBackground()

        // UI Overlay. The hologram is always dark, so the onboarding screens use the dark palette over it.
        KraveinConnectTheme(dark = if (inShell) isSystemInDarkTheme() else true) {
        NavHost(
            navController = navController,
            startDestination = startDest,
            modifier = Modifier.fillMaxSize(),
            enterTransition = { fadeIn(tween(500)) },
            exitTransition = { fadeOut(tween(500)) }
        ) {
            composable("permissions") {
                StartupPermissionsScreen(
                    missingItems = setupMissing,
                    cameraGranted = cameraGranted,
                    notificationsGranted = notificationsGranted,
                    onRequestCameraPermission = onRequestCameraPermission,
                    onRequestNotificationPermission = {
                        uiPrefs.notificationsPrompted = true
                        onRequestNotificationPermission()
                    },
                    onContinue = {
                        // Seeing the reason and continuing counts as the one ask; it is not repeated every launch.
                        uiPrefs.notificationsPrompted = true
                        if (credential != null) navController.navigate("home") { popUpTo(0) }
                        else navController.navigate("welcome") { popUpTo(0) }
                    }
                )
            }
            composable("welcome") {
                WelcomeScreen(
                    onNext = { navController.navigate("about") }
                )
            }
            composable("about") {
                AboutScreen(
                    onNext = { navController.navigate("connect") }
                )
            }
            composable("connect") {
                if (showManual) {
                    ManualConnectDialog(
                        host = manualHost, port = manualPort, code = manualCode,
                        onHostChange = { manualHost = it }, onPortChange = { manualPort = it }, onCodeChange = { manualCode = it },
                        onDismiss = { showManual = false },
                        onConnect = {
                            showManual = false
                            startWith(PairingOfferParser.fromParts(manualHost, manualPort, manualCode, nowSeconds()))
                            manualCode = ""
                        }
                    )
                }
                ConnectChoiceScreen(
                    pairing = pairing,
                    error = pairError ?: lastError,
                    onScanQr = {
                        if (!cameraGranted) onRequestCameraPermission()
                        pairError = null
                        navController.navigate("scanner")
                    },
                    onEnterManually = { showManual = true },
                )
            }
            composable("scanner") {
                QrScannerScreen(
                    onBack = { navController.popBackStack() },
                    onRequestPermission = onRequestCameraPermission,
                    errorText = pairError,
                    onScanned = { raw ->
                        navController.popBackStack()
                        startWith(PairingOfferParser.parse(raw, nowSeconds()))
                    }
                )
            }
            composable("connected_anim") {
                ConnectedAnimatedScreen(
                    onFinished = { navController.navigate("home") { popUpTo(0) } }
                )
            }
            composable("home") {
                val paired = credential
                if (paired != null) {
                    val openPermissions = {
                        val intent = Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                            data = Uri.fromParts("package", context.packageName, null)
                            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        }
                        context.startActivity(intent)
                    }
                    // The chat socket follows the credential (and the app being visible, see ChatHub).
                    LaunchedEffect(paired) { ChatHub.onCredential(paired) }
                    MainShell(
                        credential = paired,
                        chatTab = {
                            val chat by ChatHub.client.state.collectAsState()
                            val prefill by ChatHub.prefill.collectAsState()
                            ChatScreen(
                                state = chat,
                                prefill = prefill,
                                onPrefillUsed = ChatHub::prefillUsed,
                                onSend = { ChatHub.client.ask(it) },
                                onStop = { ChatHub.client.stopTurn() },
                                onNew = { ChatHub.client.newConversation() },
                                onConfirm = { id, approve -> ChatHub.client.answerConfirmation(id, approve) },
                                onDismissLoose = { ChatHub.client.dismissLoose(it) },
                                onRetry = { ChatHub.client.retryNow() },
                                onPairAgain = { navController.navigate("connect") { popUpTo(0) } },
                            )
                        },
                        phoneTab = {
                            ConnectedScreen(
                                credential = paired,
                                state = connectionState,
                                logs = logs,
                                status = status,
                                discovery = discovery,
                                onToggleConnection = {
                                    if (connectionState == ConnectionState.DISCONNECTED) {
                                        onStartService()
                                    } else {
                                        context.startService(Intent(context, KraveinConnectForegroundService::class.java).apply {
                                            action = KraveinConnectForegroundService.ACTION_STOP
                                        })
                                    }
                                },
                            )
                        },
                        settingsTab = {
                            SettingsScreen(
                                credential = paired,
                                onOpenPermissions = openPermissions,
                                onUnpair = {
                                    // Stop the socket first, then forget the credential, then drop everything the
                                    // web session held; the credential flow below sends the user to the welcome screen.
                                    context.startService(Intent(context, KraveinConnectForegroundService::class.java).apply {
                                        action = KraveinConnectForegroundService.ACTION_STOP
                                    })
                                    storage.clearCredential()
                                    // The :web process notices the missing credential the next time it starts and exits.
                                    ChatHub.onCredential(null)
                                    AgentStateStore.setCredential(null)
                                    AgentStateStore.setConnectionState(ConnectionState.DISCONNECTED)
                                    AgentStateStore.setError(null)
                                    navController.navigate("welcome") { popUpTo(0) }
                                },
                            )
                        },
                    )
                }
            }
        }
        }
    }
}

// --- NEW ONBOARDING SCREENS ---

/** The web's tracked, upper-case section label (`--tracking-label`). */
@Composable
fun SectionLabel(text: String) {
    Text(text.uppercase(), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

/** A glass panel for the in-shell tabs, same fill and hairline as the onboarding cards. */
@Composable
private fun Panel(content: @Composable () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = KraveinTheme.colors.glassStrong, contentColor = MaterialTheme.colorScheme.onSurface),
        shape = RoundedCornerShape(KraveinTokens.RADIUS_LG.dp),
        border = glassBorder(),
    ) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) { content() } }
}

@Composable
fun GlassCard(content: @Composable () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 24.dp),
        colors = CardDefaults.cardColors(
            containerColor = KraveinTheme.colors.glassStrong,
        ),
        shape = RoundedCornerShape(KraveinTokens.RADIUS_XL.dp),
        border = glassBorder()
    ) {
        Column(modifier = Modifier.padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            content()
        }
    }
}

@Composable
fun WelcomeScreen(onNext: () -> Unit) {
    Column(modifier = Modifier.fillMaxSize(), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
        Text("KRAVEIN", style = MaterialTheme.typography.displayLarge, fontWeight = FontWeight.Black, color = MaterialTheme.colorScheme.primary)
        Text("CONNECT", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Light, color = MaterialTheme.colorScheme.onSurface)
        Spacer(modifier = Modifier.height(60.dp))
        GlassCard {
            Text("Welcome to the neural bridge.", style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurface)
            Spacer(modifier = Modifier.height(24.dp))
            GradientButton(onClick = onNext, modifier = Modifier.fillMaxWidth()) {
                Text("Get Started")
            }
        }
    }
}

@Composable
fun AboutScreen(onNext: () -> Unit) {
    Column(modifier = Modifier.fillMaxSize(), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
        GlassCard {
            Text("Hardware Bridge", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.primary)
            Spacer(modifier = Modifier.height(16.dp))
            Text("This agent acts as a secure physical bridge between your Android device and your PC's Kravein AI.", color = MaterialTheme.colorScheme.onSurface, textAlign = androidx.compose.ui.text.style.TextAlign.Center)
            Spacer(modifier = Modifier.height(24.dp))
            GradientButton(onClick = onNext, modifier = Modifier.fillMaxWidth()) {
                Text("Next")
            }
        }
    }
}

@Composable
fun ConnectChoiceScreen(pairing: Boolean, error: String?, onScanQr: () -> Unit, onEnterManually: () -> Unit) {
    Column(modifier = Modifier.fillMaxSize(), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
        GlassCard {
            Text("Pair Device", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.primary)
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                "Open Kravein on your PC, choose Phone, and scan the QR code it shows.",
                color = MaterialTheme.colorScheme.onSurface,
                textAlign = androidx.compose.ui.text.style.TextAlign.Center,
            )
            error?.let {
                Spacer(modifier = Modifier.height(12.dp))
                Text(it, color = MaterialTheme.colorScheme.error, textAlign = androidx.compose.ui.text.style.TextAlign.Center)
            }
            Spacer(modifier = Modifier.height(24.dp))
            if (pairing) {
                androidx.compose.material3.CircularProgressIndicator(color = MaterialTheme.colorScheme.primary)
            } else {
                GradientButton(onClick = onScanQr, modifier = Modifier.fillMaxWidth()) { Text("Scan QR Code") }
                Spacer(modifier = Modifier.height(12.dp))
                TextButton(onClick = onEnterManually) { Text("Enter Code Manually", color = MaterialTheme.colorScheme.onSurface) }
            }
        }
    }
}

@Composable
fun ConnectedAnimatedScreen(onFinished: () -> Unit) {
    LaunchedEffect(Unit) {
        delay(2000)
        onFinished()
    }
    Column(modifier = Modifier.fillMaxSize(), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
        GlassCard {
            Text("Connection Established", style = MaterialTheme.typography.headlineSmall, color = MaterialTheme.colorScheme.primary)
            Spacer(modifier = Modifier.height(16.dp))
            androidx.compose.material3.CircularProgressIndicator(color = MaterialTheme.colorScheme.primary)
        }
    }
}


@Composable
private fun StartupPermissionsScreen(
    missingItems: List<String>,
    cameraGranted: Boolean,
    notificationsGranted: Boolean,
    onRequestCameraPermission: () -> Unit,
    onRequestNotificationPermission: () -> Unit,
    onContinue: () -> Unit,
) {
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp).background(androidx.compose.ui.graphics.Color.Transparent).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.Center,
    ) {
        Text("SETUP REQUIRED", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Black)
        Spacer(Modifier.height(8.dp))
        Text("Enable the permissions Kravein Connect needs to control your phone reliably.", color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(20.dp))
        Card(colors = CardDefaults.cardColors(containerColor = KraveinTheme.colors.glassStrong, contentColor = MaterialTheme.colorScheme.onSurface), shape = RoundedCornerShape(KraveinTokens.RADIUS_LG.dp), border = glassBorder()) {
            Column(Modifier.padding(18.dp)) {
                SectionLabel("Missing permissions")
                Spacer(Modifier.height(10.dp))
                PermissionRow("Camera", cameraGranted)
                PermissionRow("Notifications", notificationsGranted, neededLabel = "Recommended")
                if (!notificationsGranted) {
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "Notifications let you approve or decline Kravein's requests from the lock screen. You can skip this; the app still works.",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
        Spacer(Modifier.height(20.dp))
        if (!cameraGranted) {
            GradientButton(onClick = onRequestCameraPermission, modifier = Modifier.fillMaxWidth()) { Text("Allow Camera") }
            Spacer(Modifier.height(10.dp))
        }
        if (!notificationsGranted) {
            GradientButton(onClick = onRequestNotificationPermission, modifier = Modifier.fillMaxWidth()) { Text("Allow Notifications") }
            Spacer(Modifier.height(10.dp))
        }
        GradientButton(
            onClick = onContinue,
            enabled = missingItems.isEmpty(),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(if (missingItems.isEmpty()) "Continue" else "Finish Setup First")
        }
    }
}

@Composable
private fun PermissionRow(label: String, granted: Boolean, neededLabel: String = "Needed") {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label)
        Text(if (granted) "Granted" else neededLabel, color = if (granted) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error)
    }
}

@Composable
private fun ConnectedScreen(
    credential: DeviceCredential,
    state: ConnectionState,
    logs: List<String>,
    status: String,
    discovery: DiscoveryStatus,
    onToggleConnection: () -> Unit,
) {
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp).background(androidx.compose.ui.graphics.Color.Transparent).verticalScroll(rememberScrollState()),
    ) {
        SectionLabel("Kravein Connect")
        Spacer(Modifier.height(8.dp))
        val tone = when (state) {
            ConnectionState.CONNECTED -> MaterialTheme.colorScheme.primary
            ConnectionState.NEEDS_PAIRING -> MaterialTheme.colorScheme.error
            else -> MaterialTheme.colorScheme.onSurfaceVariant
        }
        Text(
            if (state == ConnectionState.CONNECTED) "● Connected" else "○ ${state.name.lowercase().replace('_', ' ')}",
            style = MaterialTheme.typography.headlineMedium,
            color = tone,
        )
        Spacer(Modifier.height(20.dp))
        Panel {
            Text("${credential.host}:${credential.port}", fontWeight = FontWeight.Bold)
            Text(credential.deviceName, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(status, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (discovery != DiscoveryStatus.IDLE) {
                Text(DiscoveryText.SEARCHING, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (discovery == DiscoveryStatus.NOT_FOUND) {
                Text(DiscoveryText.NOT_FOUND_HINT, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        Spacer(Modifier.height(16.dp))
        GradientButton(onClick = onToggleConnection, modifier = Modifier.fillMaxWidth()) {
            Text(if (state == ConnectionState.DISCONNECTED) "Connect" else "Disconnect")
        }
        Spacer(Modifier.height(20.dp))
        SectionLabel("Recent Logs")
        Spacer(Modifier.height(8.dp))
        logs.takeLast(8).forEach { line ->
            Text(line, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun ManualConnectDialog(
    host: String,
    port: String,
    code: String,
    onHostChange: (String) -> Unit,
    onPortChange: (String) -> Unit,
    onCodeChange: (String) -> Unit,
    onDismiss: () -> Unit,
    onConnect: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Enter pairing details") },
        text = {
            Column {
                OutlinedTextField(value = host, onValueChange = onHostChange, label = { Text("Host") }, singleLine = true)
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(value = port, onValueChange = onPortChange, label = { Text("Port") }, singleLine = true)
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(value = code, onValueChange = onCodeChange, label = { Text("Pairing code") }, singleLine = true)
            }
        },
        confirmButton = { TextButton(onClick = onConnect) { Text("Pair") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun QrScannerScreen(
    onBack: () -> Unit,
    onRequestPermission: () -> Unit,
    errorText: String?,
    onScanned: (String) -> Unit,
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    var previewReady by remember { mutableStateOf(false) }
    var scanHandled by remember { mutableStateOf(false) }
    val cameraExecutor = remember { Executors.newSingleThreadExecutor() }

    DisposableEffect(Unit) {
        onDispose {
            cameraExecutor.shutdown()
        }
    }

    Column(Modifier.fillMaxSize().padding(24.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            TextButton(onClick = onBack) { Text("Back") }
            Text("Scan QR", fontWeight = FontWeight.Bold)
            Spacer(Modifier.size(48.dp))
        }
        Spacer(Modifier.height(16.dp))
        Card(modifier = Modifier.fillMaxWidth().height(420.dp), shape = RoundedCornerShape(KraveinTokens.RADIUS_XL.dp), border = glassBorder()) {
            AndroidView(
                modifier = Modifier.fillMaxSize(),
                factory = { viewContext ->
                    PreviewView(viewContext).also { previewView ->
                        val cameraProviderFuture = ProcessCameraProvider.getInstance(viewContext)
                        cameraProviderFuture.addListener({
                            val cameraProvider = cameraProviderFuture.get()
                            val preview = Preview.Builder().build()
                            val imageAnalysis = ImageAnalysis.Builder()
                                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                                .build()
                            val scanner = BarcodeScanning.getClient(
                                BarcodeScannerOptions.Builder()
                                    .setBarcodeFormats(Barcode.FORMAT_QR_CODE)
                                    .build(),
                            )
                            imageAnalysis.setAnalyzer(cameraExecutor) { imageProxy ->
                                val mediaImage = imageProxy.image
                                if (mediaImage != null) {
                                    val image = InputImage.fromMediaImage(mediaImage, imageProxy.imageInfo.rotationDegrees)
                                    scanner.process(image)
                                        .addOnSuccessListener { codes ->
                                            if (!scanHandled) {
                                                codes.firstOrNull()?.rawValue?.let { raw ->
                                                    scanHandled = true
                                                    onScanned(raw)
                                                }
                                            }
                                        }
                                        .addOnCompleteListener {
                                            imageProxy.close()
                                        }
                                } else {
                                    imageProxy.close()
                                }
                            }
                            cameraProvider.unbindAll()
                            cameraProvider.bindToLifecycle(
                                lifecycleOwner,
                                CameraSelector.DEFAULT_BACK_CAMERA,
                                preview,
                                imageAnalysis,
                            )
                            preview.setSurfaceProvider(previewView.surfaceProvider)
                            previewReady = true
                        }, ContextCompat.getMainExecutor(viewContext))
                    }
                }
            )
        }
        if (!previewReady) {
            Spacer(Modifier.height(12.dp))
            Text("Camera is preparing.", color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (!errorText.isNullOrBlank()) {
            Spacer(Modifier.height(8.dp))
            Text(errorText, color = MaterialTheme.colorScheme.error)
        }
    }
}

@Composable
private fun SettingsScreen(
    credential: DeviceCredential,
    onOpenPermissions: () -> Unit,
    onUnpair: () -> Unit,
) {
    var confirmUnpair by rememberSaveable { mutableStateOf(false) }
    if (confirmUnpair) {
        AlertDialog(
            onDismissRequest = { confirmUnpair = false },
            title = { Text("Unpair this phone?") },
            text = { Text("Kravein will stop controlling this phone and the app returns to pairing. Remove the device on your PC too if you will not use it again.") },
            confirmButton = { TextButton(onClick = { confirmUnpair = false; onUnpair() }) { Text("Unpair") } },
            dismissButton = { TextButton(onClick = { confirmUnpair = false }) { Text("Cancel") } },
        )
    }
    Column(Modifier.fillMaxSize().padding(24.dp).verticalScroll(rememberScrollState())) {
        Text("Settings", style = MaterialTheme.typography.headlineSmall)
        Spacer(Modifier.height(16.dp))
        SectionLabel("Server")
        Spacer(Modifier.height(8.dp))
        Panel {
            Text("${credential.host}:${credential.port}", fontWeight = FontWeight.Bold)
            Text("Paired as ${credential.deviceName}", color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Spacer(Modifier.height(20.dp))
        OutlinedButton(onClick = onOpenPermissions, modifier = Modifier.fillMaxWidth()) { Text("Permissions") }
        Spacer(Modifier.height(12.dp))
        OutlinedButton(
            onClick = { confirmUnpair = true },
            modifier = Modifier.fillMaxWidth(),
            border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.error),
            colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
        ) { Text("Unpair") }
        Spacer(Modifier.height(24.dp))
        SectionLabel("About")
        Spacer(Modifier.height(8.dp))
        Text("Kravein Connect ${com.kravein.connect.BuildConfig.VERSION_NAME}", color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(
            "Shows your PC's Kravein and lets Kravein control this phone over your local network.",
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
