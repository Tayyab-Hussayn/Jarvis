package com.kravein.connect.accessibility

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent
import com.kravein.connect.core.AgentStateStore

class KraveinAccessibilityService : AccessibilityService() {
    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // Intentionally left blank for now.
    }

    override fun onInterrupt() {
        // Intentionally left blank for now.
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        AgentStateStore.addLog("Accessibility service enabled.")
    }
}
