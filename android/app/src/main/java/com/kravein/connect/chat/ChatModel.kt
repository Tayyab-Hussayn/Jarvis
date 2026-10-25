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

/** [REPLACED]: another socket took this session over; we stop retrying instead of fighting for it. */
enum class Link { CONNECTING, OPEN, RECONNECTING, OFFLINE, UNAUTHORIZED, REPLACED }

enum class VoicePhase {
    OFF, STARTING, IDLE, LISTENING, THINKING, SPEAKING, ERROR;

    /** A session that is genuinely running (the same definition as the web's `isVoiceLive`). */
    val live: Boolean get() = this != OFF && this != ERROR
}

/** The voice session as the chat shows it. [turnId] is the spoken turn being built (`s…`), if one is open. */
data class VoiceUi(
    val phase: VoicePhase = VoicePhase.OFF,
    /** "live" or "local", as the server reported it (never what we asked for). */
    val mode: String = "",
    /** Whether the microphone/speaker are this phone's ("device") or the PC's ("laptop"). */
    val audio: String = "",
    val error: String = "",
    /** Session chatter ("started", "ended"): a status line, not conversation. */
    val notes: List<String> = emptyList(),
    val turnId: String? = null,
    val toolCount: Int = 0,
    /** Set on the stop tap. You cannot be stopping something already stopped, see [live]. */
    val stopping: Boolean = false,
) {
    val isStopping: Boolean get() = stopping && phase.live
}

data class ChatState(
    val messages: List<ChatMessage> = emptyList(),
    val link: Link = Link.CONNECTING,
    val assistantName: String = "Kravein",
    val busy: Boolean = false,
    /** Set on the Stop tap, cleared by the server's `cancelled`/`turn_end`. */
    val cancelling: Boolean = false,
    /** Confirmations whose turn has no message here (voice, a panel run); still shown, never dropped. */
    val looseConfirms: List<ConfirmItem> = emptyList(),
    /** Highest turn number used (`t7`, `s3`): typed and spoken turns share it so an id is never reused. */
    val seq: Int = 0,
    val voice: VoiceUi = VoiceUi(),
) {
    val pendingConfirms: List<ConfirmItem>
        get() = (messages.filterIsInstance<ChatMessage.Assistant>().flatMap { it.confirms } + looseConfirms)
            .filter { it.status == ConfirmStatus.PENDING }
}

/** What a server restart or an expired session leaves behind: the server no longer has the conversation above. */
const val SESSION_LOST_NOTE =
    "This conversation was not found on the server (it expired or the server restarted), so Kravein does not remember what is above."

const val LOST_RESULT =
    "Connection lost — result unknown. It may have run; check the device or the activity log."
