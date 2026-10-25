package com.kravein.connect.ui

import android.content.Intent
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
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.MoreVert
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
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
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
import androidx.compose.runtime.LaunchedEffect
import com.kravein.connect.core.DeviceCredential
import com.kravein.connect.core.ShellNavigation
import com.kravein.connect.web.WebActivity

private enum class ShellTab(val label: String, val icon: ImageVector) {
    CHAT("Chat", Icons.Filled.Home),
    PHONE("Phone", Icons.Filled.Phone),
    MORE("More", Icons.Filled.MoreVert),
    SETTINGS("Settings", Icons.Filled.Settings),
}

/**
 * The paired app: the native Chat (default), the phone-side connection status, "More" (the full web UI, in its own
 * process) and settings. Only the selected tab is composed.
 */
@Composable
fun MainShell(
    credential: DeviceCredential,
    chatTab: @Composable () -> Unit,
    phoneTab: @Composable () -> Unit,
    settingsTab: @Composable () -> Unit,
) {
    var tab by rememberSaveable { mutableStateOf(ShellTab.CHAT) }
    // A tap on a confirmation notification lands on the Chat tab, where the same question is on screen.
    val showChat by ShellNavigation.showKravein.collectAsState()
    val seen = remember { showChat }
    LaunchedEffect(showChat) { if (showChat != seen) tab = ShellTab.CHAT }
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
                ShellTab.CHAT -> chatTab()
                ShellTab.PHONE -> phoneTab()
                ShellTab.MORE -> MoreTab(credential)
                ShellTab.SETTINGS -> settingsTab()
            }
        }
    }
}

/** Everything the native screens do not cover yet lives in the web UI, opened on demand in a separate process. */
@Composable
private fun MoreTab(credential: DeviceCredential) {
    val context = LocalContext.current
    Centered {
        Text("The full Kravein", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        Text(
            "Voice, apps, routines and the rest of the web UI on ${credential.host}. It opens in its own window and " +
                "frees its memory two minutes after you leave it.",
            textAlign = TextAlign.Center,
        )
        GradientButton(
            onClick = { context.startActivity(Intent(context, WebActivity::class.java)) },
            modifier = Modifier.fillMaxWidth(),
        ) { Text("Open Kravein web") }
    }
}

@Composable
internal fun Centered(content: @Composable () -> Unit) {
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
