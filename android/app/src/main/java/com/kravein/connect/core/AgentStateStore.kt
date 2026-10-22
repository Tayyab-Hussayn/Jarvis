package com.kravein.connect.core

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** UI-visible state shared by the activity and the foreground service. Never put a secret or a pairing code here. */
object AgentStateStore {
    private val _connectionState = MutableStateFlow(ConnectionState.DISCONNECTED)
    val connectionState: StateFlow<ConnectionState> = _connectionState.asStateFlow()

    private val _credential = MutableStateFlow<DeviceCredential?>(null)
    val credential: StateFlow<DeviceCredential?> = _credential.asStateFlow()

    private val _statusText = MutableStateFlow("Ready")
    val statusText: StateFlow<String> = _statusText.asStateFlow()

    private val _lastError = MutableStateFlow<String?>(null)
    val lastError: StateFlow<String?> = _lastError.asStateFlow()

    private val _logs = MutableStateFlow<List<String>>(emptyList())
    val logs: StateFlow<List<String>> = _logs.asStateFlow()

    fun setConnectionState(state: ConnectionState) {
        _connectionState.value = state
    }

    fun setCredential(credential: DeviceCredential?) {
        _credential.value = credential
    }

    fun setStatus(text: String) {
        _statusText.value = text
    }

    fun setError(text: String?) {
        _lastError.value = text
    }

    fun addLog(message: String) {
        _logs.value = (_logs.value + message).takeLast(50)
    }
}
