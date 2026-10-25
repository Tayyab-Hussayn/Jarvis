package com.kravein.connect.chat

/**
 * The conversation state machine: `(state, event) -> state`, no Android, no clock but the one passed in.
 * It follows `web/lib/useKravein.ts` frame for frame, so the phone and the browser show the same story.
 */
object ChatReducer {
    /** A server frame arrived. [now] is epoch ms, used only to turn a confirmation's timeout into a deadline. */
    fun onFrame(state: ChatState, frame: ServerFrame, now: Long): ChatState = when (frame) {
        is ServerFrame.Hello -> state.copy(assistantName = frame.assistantName)

        is ServerFrame.TurnStart ->
            state.copy(busy = true, messages = state.messages + ChatMessage.Assistant(id = frame.id))

        is ServerFrame.Thinking ->
            if (frame.text.isBlank()) state else state.patch(frame.id) { it.copy(thinking = it.thinking + frame.text) }

        is ServerFrame.ToolCall -> state.patch(frame.id) {
            it.copy(tools = it.tools + ToolCallItem(frame.callId, frame.name, frame.display, frame.args))
        }

        is ServerFrame.ToolResult -> state.patch(frame.id) { m ->
            m.copy(tools = m.tools.map { t ->
                if (t.callId == frame.callId) t.copy(status = ToolStatus.DONE, result = frame.result) else t
            })
        }

        is ServerFrame.ConfirmRequest -> {
            val card = ConfirmItem(
                confirmId = frame.confirmId,
                action = frame.action,
                detail = frame.detail,
                danger = frame.danger,
                expiresAt = now + frame.timeoutSeconds * 1000L,
            )
            // A confirmation blocks a server worker until answered, so it is never dropped: a turn this chat has no
            // message for (voice, a panel run) lands in the loose list, which the screen shows as an overlay.
            if (state.hasTurn(frame.id)) state.patch(frame.id) { it.copy(confirms = it.confirms + card) }
            else state.copy(looseConfirms = state.looseConfirms + card)
        }

        is ServerFrame.ConfirmExpired -> state.mapConfirms { c ->
            if (c.confirmId == frame.confirmId && c.status == ConfirmStatus.PENDING) c.copy(status = ConfirmStatus.EXPIRED) else c
        }

        // Resolved anywhere (phone notification, web, timeout): close the card here too. Only a pending card changes,
        // so the tap that resolved it locally is never overwritten and a repeated frame is harmless.
        is ServerFrame.ConfirmResolved -> state.mapConfirms { c ->
            if (c.confirmId == frame.confirmId && c.status == ConfirmStatus.PENDING) {
                c.copy(status = frame.outcome.toStatus(), by = frame.by)
            } else c
        }

        is ServerFrame.Answer -> state.patch(frame.id) { it.copy(text = frame.text) }

        is ServerFrame.TurnEnd -> state.copy(busy = false, cancelling = false).patch(frame.id) {
            it.copy(
                pending = false,
                // A stop did what was asked; it must not render as a failure.
                error = if (frame.cancelled) null else frame.error,
                cancelled = frame.cancelled,
            )
        }

        // The server always answers a cancel, even with nothing running, so "Stopping…" can never wedge.
        is ServerFrame.Cancelled -> if (frame.id == null) state.copy(cancelling = false) else state

        is ServerFrame.Error ->
            // Like the web client, an error on a live turn only annotates it; `turn_end` is what finishes it.
            if (frame.id != null && state.hasTurn(frame.id)) {
                state.patch(frame.id) { it.copy(error = frame.message) }
            } else {
                state.copy(messages = state.messages + ChatMessage.Assistant(id = "err-${state.messages.size}", error = frame.message, pending = false))
            }

        ServerFrame.ResetOk -> state.copy(messages = emptyList(), looseConfirms = emptyList(), busy = false, cancelling = false)

        ServerFrame.Pong, ServerFrame.Ignored, ServerFrame.Malformed -> state
    }

    /** The user sent [text] as turn [id]. */
    fun onAsk(state: ChatState, id: String, text: String): ChatState =
        state.copy(messages = state.messages + ChatMessage.User("$id-user", text))

    /** An `ask` could not be sent: say so instead of leaving the user's message unanswered. */
    fun onAskNotSent(state: ChatState, id: String): ChatState = state.copy(
        messages = state.messages + ChatMessage.Assistant(id = "$id-err", error = "Not connected.", pending = false),
    )

    /** The user tapped Approve/Decline. Optimistic: the server's `confirm_resolved` confirms or corrects it. */
    fun onConfirmAnswered(state: ChatState, confirmId: String, approve: Boolean): ChatState = state.mapConfirms { c ->
        if (c.confirmId == confirmId && c.status == ConfirmStatus.PENDING) {
            c.copy(status = if (approve) ConfirmStatus.APPROVED else ConfirmStatus.DENIED, by = "phone")
        } else c
    }

    fun onStopRequested(state: ChatState, sent: Boolean): ChatState =
        if (state.busy && sent) state.copy(cancelling = true) else state

    /** Overlay cards that finished are removed by the screen after a short delay. */
    fun dropLoose(state: ChatState, confirmId: String): ChatState =
        state.copy(looseConfirms = state.looseConfirms.filterNot { it.confirmId == confirmId })

    fun onLink(state: ChatState, link: Link): ChatState = state.copy(link = link)

    /**
     * The socket died. Nothing will ever finish what it was doing, and a new socket is a new session with no replay:
     * a "running" card would stay that way forever. It becomes UNKNOWN — not failed, because the call may have run.
     * A pending confirmation is DENIED (the server denies on a dropped socket, so that is what really happened).
     */
    fun onDisconnected(state: ChatState): ChatState = state.copy(
        busy = false,
        cancelling = false,
        messages = state.messages.map { m ->
            if (m !is ChatMessage.Assistant) return@map m
            val open = m.tools.any { it.status == ToolStatus.RUNNING } || m.confirms.any { it.status == ConfirmStatus.PENDING }
            if (!m.pending && !open) return@map m
            m.copy(
                pending = false,
                interrupted = if (m.pending) true else m.interrupted,
                tools = m.tools.map { if (it.status == ToolStatus.RUNNING) it.copy(status = ToolStatus.UNKNOWN, result = LOST_RESULT) else it },
                confirms = m.confirms.map { if (it.status == ConfirmStatus.PENDING) it.copy(status = ConfirmStatus.DENIED) else it },
            )
        },
        looseConfirms = state.looseConfirms.map { if (it.status == ConfirmStatus.PENDING) it.copy(status = ConfirmStatus.DENIED) else it },
    )

    private fun ConfirmOutcome.toStatus() = when (this) {
        ConfirmOutcome.APPROVED -> ConfirmStatus.APPROVED
        ConfirmOutcome.DENIED -> ConfirmStatus.DENIED
        ConfirmOutcome.EXPIRED -> ConfirmStatus.EXPIRED
    }

    private fun ChatState.hasTurn(id: String) = messages.any { it is ChatMessage.Assistant && it.id == id }

    private fun ChatState.patch(id: String, fn: (ChatMessage.Assistant) -> ChatMessage.Assistant): ChatState =
        copy(messages = messages.map { if (it is ChatMessage.Assistant && it.id == id) fn(it) else it })

    private fun ChatState.mapConfirms(fn: (ConfirmItem) -> ConfirmItem): ChatState = copy(
        messages = messages.map { if (it is ChatMessage.Assistant) it.copy(confirms = it.confirms.map(fn)) else it },
        looseConfirms = looseConfirms.map(fn),
    )
}
