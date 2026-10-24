package com.kravein.connect.ui

import android.app.Activity
import androidx.activity.compose.BackHandler
import androidx.compose.ui.platform.LocalContext
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.kravein.connect.core.DeviceCredential
import com.kravein.connect.web.KraveinWebController
import com.kravein.connect.web.WebUiState

private enum class ShellTab(val label: String) {
    KRAVEIN("Kravein"),
    PHONE("Phone"),
    SETTINGS("Settings"),
}

/**
 * The paired app: the Kravein web UI, the phone-side connection status, and settings. Only the selected tab is
 * composed, so the WebView is not even built until its tab is first shown.
 */
@Composable
fun MainShell(
    credential: DeviceCredential,
    web: KraveinWebController,
    phoneTab: @Composable () -> Unit,
    settingsTab: @Composable () -> Unit,
    onPairAgain: () -> Unit,
) {
    var tab by rememberSaveable { mutableStateOf(ShellTab.KRAVEIN) }
    Scaffold(
        bottomBar = {
            NavigationBar {
                ShellTab.entries.forEach { item ->
                    NavigationBarItem(
                        selected = tab == item,
                        onClick = { tab = item },
                        icon = { Text(item.label.take(1), fontWeight = FontWeight.Bold) },
                        label = { Text(item.label) },
                    )
                }
            }
        },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            when (tab) {
                ShellTab.KRAVEIN -> KraveinWebTab(credential, web, onPairAgain)
                ShellTab.PHONE -> phoneTab()
                ShellTab.SETTINGS -> settingsTab()
            }
        }
    }
}

@Composable
private fun KraveinWebTab(credential: DeviceCredential, web: KraveinWebController, onPairAgain: () -> Unit) {
    val state by web.state.collectAsState()
    // Back walks the page's own history first, and only then leaves the app.
    // canGoBack() is not observable, so the handler stays on and finishes the activity itself when there is no history.
    val activity = LocalContext.current as? Activity
    BackHandler(enabled = true) { if (web.canGoBack()) web.goBack() else activity?.finish() }

    when (val current = state) {
        WebUiState.NeedsPairing -> Centered {
            Text("This phone is no longer paired", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Text("Kravein on your PC refused this device. Pair it again to continue.", textAlign = TextAlign.Center)
            Button(onClick = onPairAgain, modifier = Modifier.fillMaxWidth()) { Text("Pair again") }
        }
        is WebUiState.Offline -> Centered {
            Text("Kravein is unreachable", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Text(current.message, textAlign = TextAlign.Center)
            Button(onClick = { web.reload() }, modifier = Modifier.fillMaxWidth()) { Text("Retry") }
        }
        else -> {
            Column(Modifier.fillMaxSize()) {
                if (current == WebUiState.Loading) {
                    CircularProgressIndicator(Modifier.align(Alignment.CenterHorizontally).padding(8.dp))
                }
                AndroidView(
                    modifier = Modifier.fillMaxWidth().weight(1f),
                    factory = { web.viewFor(credential) },
                )
                OutlinedButton(onClick = { web.reload() }, modifier = Modifier.fillMaxWidth()) { Text("Reload") }
            }
        }
    }
}

@Composable
private fun Centered(content: @Composable () -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(24.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterVertically),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        content()
        Spacer(Modifier.height(8.dp))
    }
}
