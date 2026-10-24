package com.kravein.connect.tile

import com.kravein.connect.core.ConnectionState

/** What the Quick Settings tile shows. Pure so the mapping from connection state is testable without a device. */
data class TileModel(val active: Boolean, val subtitle: String) {
    companion object {
        /** [paired] false means no credential is stored; the connection state is then meaningless. */
        fun from(paired: Boolean, state: ConnectionState): TileModel = when {
            !paired -> TileModel(false, "Not paired")
            else -> when (state) {
                ConnectionState.CONNECTED -> TileModel(true, "Connected")
                ConnectionState.CONNECTING, ConnectionState.RECONNECTING -> TileModel(false, "Connecting")
                ConnectionState.NEEDS_PAIRING -> TileModel(false, "Pair again")
                ConnectionState.DISCONNECTED -> TileModel(false, "Not connected")
            }
        }
    }
}
