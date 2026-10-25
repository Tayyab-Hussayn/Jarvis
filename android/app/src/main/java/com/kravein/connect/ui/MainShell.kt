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
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Phone
import androidx.compose.material.icons.filled.Settings
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import com.kravein.connect.ui.theme.GradientButton
import com.kravein.connect.ui.theme.KraveinTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.key
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.runtime.LaunchedEffect
import com.kravein.connect.core.DeviceCredential
import com.kravein.connect.core.ShellNavigation
import com.kravein.connect.web.KraveinWebController
import com.kravein.connect.web.WebUiState

private enum class ShellTab(val label: String, val icon: ImageVector) {
    KRAVEIN("Kravein", Icons.Filled.Home),
    PHONE("Phone", Icons.Filled.Phone),
    SETTINGS("Settings", Icons.Filled.Settings),
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
    // A tap on a confirmation notification lands on the Kravein tab, where the same question is on screen.
    val showKravein by ShellNavigation.showKravein.collectAsState()
    val seen = remember { showKravein }
    LaunchedEffect(showKravein) { if (showKravein != seen) tab = ShellTab.KRAVEIN }
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        bottomBar = {
            // Glass chrome with a hairline on top, like the web rail.
            NavigationBar(containerColor = KraveinTheme.colors.glassStrong, modifier = Modifier.topHairline(KraveinTheme.colors.glassBorder)) {
                ShellTab.entries.forEach { item ->
                    NavigationBarItem(
                        selected = tab == item,
                        onClick = { tab = item },
                        icon = { Icon(item.icon, contentDescription = null) },
                        label = { Text(item.label, style = MaterialTheme.typography.labelSmall) },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = MaterialTheme.colorScheme.onPrimary,
                            selectedTextColor = MaterialTheme.colorScheme.primary,
                            indicatorColor = MaterialTheme.colorScheme.primary,
                            unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                            unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        ),
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
    // The PC may be found at a new address; the page, token and loopback pipe belong to the old one.
    LaunchedEffect(credential.host, credential.port) { web.endpointChanged(credential) }
    // Back walks the page's own history first, and only then leaves the app.
    // canGoBack() is not observable, so the handler stays on and finishes the activity itself when there is no history.
    val activity = LocalContext.current as? Activity
    BackHandler(enabled = true) { if (web.canGoBack()) web.goBack() else activity?.finish() }

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

/** A 1 dp line along the top edge; the web chrome's `--glass-border` hairline. */
private fun Modifier.topHairline(color: Color): Modifier = drawBehind {
    drawLine(color, Offset(0f, 0f), Offset(size.width, 0f), strokeWidth = 1.dp.toPx())
}
