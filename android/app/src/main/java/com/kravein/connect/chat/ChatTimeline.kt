package com.kravein.connect.chat

/**
 * One node on the connecting-line timeline (the phone's version of `web/components/TurnGraph.tsx`): the user's
 * message, what the model thought, each tool call, each confirmation, the answer. The screen draws one line through
 * all of them, so a conversation reads as a single path.
 */
sealed interface TimelineRow {
    val key: String

    data class Request(override val key: String, val text: String) : TimelineRow
    data class Thinking(override val key: String, val text: String, val live: Boolean) : TimelineRow
    data class Tool(override val key: String, val tool: ToolCallItem) : TimelineRow
    data class Confirm(override val key: String, val confirm: ConfirmItem) : TimelineRow
    data class Answer(override val key: String, val text: String) : TimelineRow

    /** Turn finished abnormally or with a note: error, stopped, connection lost. */
    data class Status(override val key: String, val kind: StatusKind, val text: String) : TimelineRow

    /** The turn is running and nothing has been said yet (or is between steps). */
    data class Working(override val key: String) : TimelineRow
}

enum class StatusKind { ERROR, STOPPED, LOST }

object ChatTimeline {
    const val STOPPED_TEXT = "Stopped. Anything already running finished; nothing further was started."
    const val LOST_TEXT = "Connection lost — result unknown. The request may have completed; check before repeating it."

    fun rows(messages: List<ChatMessage>): List<TimelineRow> {
        val out = ArrayList<TimelineRow>()
        for (m in messages) when (m) {
            is ChatMessage.User -> out += TimelineRow.Request(m.id, m.text)
            is ChatMessage.Assistant -> {
                val thought = m.thinking.joinToString("\n\n").trim()
                if (thought.isNotEmpty()) out += TimelineRow.Thinking("${m.id}-think", thought, live = m.pending && m.text.isEmpty())
                m.tools.forEach { out += TimelineRow.Tool("${m.id}-tool-${it.callId}", it) }
                m.confirms.forEach { out += TimelineRow.Confirm("${m.id}-confirm-${it.confirmId}", it) }
                if (m.text.isNotBlank()) out += TimelineRow.Answer("${m.id}-answer", m.text)
                if (m.error != null) out += TimelineRow.Status("${m.id}-error", StatusKind.ERROR, m.error)
                if (m.cancelled) out += TimelineRow.Status("${m.id}-stopped", StatusKind.STOPPED, STOPPED_TEXT)
                if (m.interrupted) out += TimelineRow.Status("${m.id}-lost", StatusKind.LOST, LOST_TEXT)
                // A running turn always shows a live node at its end, so "nothing happening" is never ambiguous.
                val busy = m.pending && m.confirms.none { it.status == ConfirmStatus.PENDING } && m.tools.none { it.status == ToolStatus.RUNNING }
                if (busy && m.text.isEmpty()) out += TimelineRow.Working("${m.id}-working")
            }
        }
        return out
    }
}
