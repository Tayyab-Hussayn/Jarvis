package com.kravein.connect.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.viewinterop.AndroidView
import com.kravein.connect.core.DeviceCredential
import com.kravein.connect.ui.theme.GradientButton
import com.kravein.connect.web.KraveinWebController
import com.kravein.connect.web.WebUiState

/**
 * The full Kravein web UI ("More"). It runs in the `:web` process (see `WebActivity`), so the WebView and Chromium's
 * renderer cost nothing while the native Chat is what the user is using.
 */
@Composable
fun KraveinWebScreen(credential: DeviceCredential, web: KraveinWebController, onPairAgain: () -> Unit, onExit: () -> Unit) {
    val state by web.state.collectAsState()
    // The PC may be found at a new address; the page, token and loopback pipe belong to the old one.
    LaunchedEffect(credential.host, credential.port) { web.endpointChanged(credential) }
    // Back walks the page's own history first, and only then leaves the web screen.
    // canGoBack() is not observable, so the handler stays on and exits itself when there is no history.
    BackHandler(enabled = true) { if (web.canGoBack()) web.goBack() else onExit() }

    when (val current = state) {
        WebUiState.NeedsPairing -> Centered {
            Text("This phone is no longer paired", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Text("Kravein on your PC refused this device. Pair it again to continue.", textAlign = TextAlign.Center)
            GradientButton(onClick = onPairAgain, modifier = Modifier.fillMaxWidth()) { Text("Pair again") }
        }
        is WebUiState.Offline -> Centered {
            Text("Kravein is unreachable", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Text(current.message, textAlign = TextAlign.Center)
            GradientButton(onClick = { web.reload() }, modifier = Modifier.fillMaxWidth()) { Text("Retry") }
        }
        else -> {
            Column(Modifier.fillMaxSize()) {
                if (current == WebUiState.Loading) {
                    LinearProgressIndicator(Modifier.fillMaxWidth(), color = MaterialTheme.colorScheme.primary, trackColor = MaterialTheme.colorScheme.surfaceVariant)
                }
                key(credential.host, credential.port) {
                    AndroidView(
                        modifier = Modifier.fillMaxWidth().weight(1f),
                        factory = { web.viewFor(credential) },
                    )
                }
                OutlinedButton(onClick = { web.reload() }, modifier = Modifier.fillMaxWidth()) { Text("Reload") }
            }
        }
    }
}
