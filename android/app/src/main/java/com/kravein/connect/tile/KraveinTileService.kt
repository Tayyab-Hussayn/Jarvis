package com.kravein.connect.tile

import android.app.PendingIntent
import android.content.Intent
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import com.kravein.connect.MainActivity
import com.kravein.connect.confirm.ConfirmationNotifier
import com.kravein.connect.core.AgentStateStore
import com.kravein.connect.pairing.PairingStorage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

/**
 * The "Kravein" Quick Settings tile. It only mirrors [AgentStateStore] while the shade is open (between
 * onStartListening and onStopListening), so nothing polls and nothing runs when the tile is not visible.
 */
class KraveinTileService : TileService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var watching: Job? = null

    override fun onStartListening() {
        super.onStartListening()
        // The process may have been started by the shade alone, before any activity loaded the credential.
        if (AgentStateStore.credential.value == null) AgentStateStore.setCredential(PairingStorage(this).loadCredential())
        watching?.cancel()
        watching = scope.launch {
            combine(AgentStateStore.credential, AgentStateStore.connectionState) { credential, state ->
                TileModel.from(credential != null, state)
            }.collect(::render)
        }
    }

    override fun onStopListening() {
        watching?.cancel()
        watching = null
        super.onStopListening()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    override fun onClick() {
        val intent = Intent(this, MainActivity::class.java)
            .setAction(ConfirmationNotifier.ACTION_OPEN_KRAVEIN)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            // Android 14 replaced startActivityAndCollapse(Intent) with a PendingIntent overload.
            val pending = PendingIntent.getActivity(this, 0, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            startActivityAndCollapse(pending)
        } else {
            @Suppress("DEPRECATION", "StartActivityAndCollapseDeprecated")
            startActivityAndCollapse(intent)
        }
    }

    private fun render(model: TileModel) {
        val tile = qsTile ?: return
        tile.state = if (model.active) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
        tile.label = "Kravein"
        // Tile subtitles exist from Android 10; below that the state colour is the only signal.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) tile.subtitle = model.subtitle
        tile.updateTile()
    }
}
