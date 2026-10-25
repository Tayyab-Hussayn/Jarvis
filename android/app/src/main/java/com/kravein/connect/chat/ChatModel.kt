package com.kravein.connect.chat

enum class ToolStatus { RUNNING, DONE, FAILED, UNKNOWN }

data class ToolCallItem(
    val callId: String,
    val name: String,
    val display: String,
    val args: String,
    val status: ToolStatus = ToolStatus.RUNNING,
    val result: String? = null,
)

enum class ConfirmStatus { PENDING, APPROVED, DENIED, EXPIRED }

data class ConfirmItem(
    val confirmId: String,
    val action: String,
    val detail: String,
    val danger: Danger,
    /** Epoch ms when the server stops waiting and denies. */
    val expiresAt: Long,
    val status: ConfirmStatus = ConfirmStatus.PENDING,
    /** Who answered, when the server said ("phone", "web"). */
    val by: String = "",
)

sealed interface ChatMessage {
    val id: String

    data class User(override val id: String, val text: String) : ChatMessage

    /** One assistant turn: its thinking, tool calls, confirmations and answer all hang off the same timeline. */
    data class Assistant(
        override val id: String,
        val text: String = "",
        val thinking: List<String> = emptyList(),
        val tools: List<ToolCallItem> = emptyList(),
        val confirms: List<ConfirmItem> = emptyList(),
        val error: String? = null,
        /** Pending until `turn_end`. */
        val pending: Boolean = true,
        /** The user stopped it. Not an error: it did what was asked. */
        val cancelled: Boolean = false,
        /** The connection dropped before the turn finished. */
        val interrupted: Boolean = false,
    ) : ChatMessage
}

enum class Link { CONNECTING, OPEN, RECONNECTING, OFFLINE, UNAUTHORIZED }

data class ChatState(
    val messages: List<ChatMessage> = emptyList(),
    val link: Link = Link.CONNECTING,
    val assistantName: String = "Kravein",
    val busy: Boolean = false,
    /** Set on the Stop tap, cleared by the server's `cancelled`/`turn_end`. */
    val cancelling: Boolean = false,
    /** Confirmations whose turn has no message here (voice, a panel run); still shown, never dropped. */
    val looseConfirms: List<ConfirmItem> = emptyList(),
) {
    val pendingConfirms: List<ConfirmItem>
        get() = (messages.filterIsInstance<ChatMessage.Assistant>().flatMap { it.confirms } + looseConfirms)
            .filter { it.status == ConfirmStatus.PENDING }
}

const val LOST_RESULT =
    "Connection lost — result unknown. It may have run; check the device or the activity log."
