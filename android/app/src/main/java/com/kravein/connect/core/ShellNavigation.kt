package com.kravein.connect.core

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** One-way requests from outside the UI (a notification tap) to the shell. A counter, so asking twice still fires. */
object ShellNavigation {
    private val _showKravein = MutableStateFlow(0)
    val showKravein: StateFlow<Int> = _showKravein.asStateFlow()

    fun requestKraveinTab() {
        _showKravein.value += 1
    }
}
