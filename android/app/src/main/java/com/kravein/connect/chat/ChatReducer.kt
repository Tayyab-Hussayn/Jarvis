package com.kravein.connect.chat

/**
 * The conversation state machine: `(state, event) -> state`, no Android, no clock but the one passed in.
 * It follows `web/lib/useKravein.ts` frame for frame, so the phone and the browser show the same story.
 */
object ChatReducer {
    /** A server frame arrived. [now] is epoch ms, used only to turn a confirmation's timeout into a deadline. */
    fun onFrame(state: ChatState, frame: ServerFrame, now: Long): ChatState = when (frame) {
        is ServerFrame.Hello -> {
            // A session the server did not resume has no voice either (a restart or an expired session ends it).
            var next = state.copy(assistantName = frame.assistantName)
            if (frame.resumed != true) next = next.copy(voice = VoiceUi())
            // Keep what the user can see, but say the model does not have it.
            if (frame.resumed == false && next.messages.isNotEmpty()) {
                next = next.copy(messages = next.messages + ChatMessage.Assistant(id = "note-${next.messages.size}", text = SESSION_LOST_NOTE, pending = false))
            }
            next
        }

        // Rebuild from the server's record. This also replaces any card the dead socket left "running" or "denied"
        // with what really happened.
        is ServerFrame.Replay -> replay(state, frame, now)

        // Only inside a replay; live, the chat added the message when it sent.
        is ServerFrame.UserTurn -> state.copy(messages = state.messages + ChatMessage.User("${frame.id}-user", frame.text))

        is ServerFrame.VoiceState -> voiceFrame(state, frame)

        ServerFrame.AudioClear -> state

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
        state.copy(messages = state.messages + ChatMessage.User("$id-user", text), seq = maxOf(state.seq, seqOf(id)))

    /** The user asked for voice. Nothing is live until the server says `started`. */
    fun onVoiceRequested(state: ChatState): ChatState =
        if (state.voice.phase.live) state
        else state.copy(voice = state.voice.copy(phase = VoicePhase.STARTING, error = "", stopping = false, audio = "device"))

    fun onVoiceStopRequested(state: ChatState): ChatState =
        if (state.voice.phase.live) state.copy(voice = state.voice.copy(stopping = true)) else state

    /** Voice ended on this side (refused microphone, the link stayed down too long, the user stopped while offline). */
    fun onVoiceOff(state: ChatState, note: String? = null): ChatState {
        val closed = closeVoiceTurn(state)
        return closed.copy(voice = closed.voice.copy(phase = VoicePhase.OFF, stopping = false).withNote(note))
    }

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
        // The spoken turn below is settled; the next frame must not patch it. The voice phase itself stays: the
        // server keeps a device session for a grace period and the resumed replay says whether it survived.
        voice = state.voice.copy(turnId = null, stopping = false),
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

    private fun replay(state: ChatState, frame: ServerFrame.Replay, now: Long): ChatState {
        val blank = state.copy(
            messages = emptyList(),
            looseConfirms = emptyList(),
            voice = state.voice.copy(notes = emptyList(), turnId = null, toolCount = 0, error = ""),
        )
        val rebuilt = frame.frames.fold(blank) { acc, f -> onFrame(acc, f, now) }
        val v = rebuilt.voice
        val voice = if (frame.voice.active) {
            v.copy(
                phase = if (v.phase == VoicePhase.OFF || v.phase == VoicePhase.ERROR) VoicePhase.IDLE else v.phase,
                mode = frame.voice.mode.takeIf { it == "live" || it == "local" } ?: v.mode,
                audio = frame.voice.audio,
            )
        } else {
            v.copy(phase = VoicePhase.OFF, stopping = false, audio = "", turnId = null)
        }
        return rebuilt.copy(
            // Without this a reloaded chat would number its next turn t1 again and patch the old message.
            seq = maxOf(rebuilt.seq, frame.frames.maxOfOrNull { idOf(it)?.let(::seqOf) ?: 0 } ?: 0),
            busy = frame.busy,
            cancelling = false,
            voice = voice,
        )
    }

    /** `t7`, `p3`, `s2-user` -> the number; anything else 0. */
    internal fun seqOf(id: String): Int {
        if (id.length < 2 || id[0] !in "tps") return 0
        return id.drop(1).takeWhile { it.isDigit() }.toIntOrNull() ?: 0
    }

    private fun idOf(frame: ServerFrame): String? = when (frame) {
        is ServerFrame.UserTurn -> frame.id
        is ServerFrame.TurnStart -> frame.id
        is ServerFrame.Thinking -> frame.id
        is ServerFrame.ToolCall -> frame.id
        is ServerFrame.ToolResult -> frame.id
        is ServerFrame.Answer -> frame.id
        is ServerFrame.TurnEnd -> frame.id
        else -> null
    }

    // -- voice: a spoken turn is opened lazily by whichever frame first carries content and closed by `listening`
    // (both pipelines emit it at a turn boundary, at opposite ends of it), exactly like web/lib/useKravein.ts.

    private fun voiceFrame(state: ChatState, f: ServerFrame.VoiceState): ChatState {
        val v = state.voice
        return when (f.phase) {
            "started" -> state.copy(
                voice = v.copy(
                    phase = VoicePhase.IDLE, error = "", stopping = false,
                    mode = f.text.takeIf { it == "live" || it == "local" } ?: v.mode,
                    audio = f.audio ?: v.audio,
                    notes = listOf("Voice session started (${f.text})."),
                ),
            )
            "stopped" -> onVoiceOff(state, "Voice session ended.")
            "listening" -> closeVoiceTurn(state).withPhase(VoicePhase.LISTENING)
            "heard_partial" -> setRequest(state, f.text)
            "heard" -> setRequest(state.withPhase(VoicePhase.THINKING), f.text)
            "thinking" -> state.withPhase(VoicePhase.THINKING)
            "answer_partial", "answer" -> setAnswer(state, f.text)
            // Phase only: local emits `answer` with the full reply and then a shortened `speaking` for the
            // synthesiser, so rendering it would show the reply twice.
            "speaking" -> state.withPhase(VoicePhase.SPEAKING)
            "tool" -> addTool(state, f.text)
            "tool_result" -> finishTool(state, f.text)
            "info" -> state.copy(voice = v.withNote(f.text))
            "error" -> {
                val withError = state.copy(voice = v.copy(error = f.text, stopping = false))
                val turn = v.turnId
                // A failed tool is an error in a turn, not a dead session; only an error with no turn to blame is fatal.
                if (turn != null) withError.patch(turn) { it.copy(error = f.text) }
                else withError.copy(voice = withError.voice.copy(phase = VoicePhase.ERROR).withNote(f.text))
            }
            else -> state
        }
    }

    /** A late phase frame after `stopped` must not bring a finished session back to life. */
    private fun ChatState.withPhase(phase: VoicePhase): ChatState =
        if (voice.phase == VoicePhase.OFF) this else copy(voice = voice.copy(phase = phase))

    private fun VoiceUi.withNote(text: String?): VoiceUi =
        if (text.isNullOrBlank()) this else copy(notes = (notes + text).takeLast(MAX_NOTES))

    private fun openVoiceTurn(state: ChatState): Pair<ChatState, String> {
        state.voice.turnId?.let { return state to it }
        val seq = state.seq + 1
        val id = "s$seq"
        return state.copy(
            seq = seq,
            messages = state.messages + ChatMessage.User("$id-user", "") + ChatMessage.Assistant(id = id),
            // A failure belonged to the turn that caused it; left standing it would describe something finished.
            voice = state.voice.copy(turnId = id, toolCount = 0, error = ""),
        ) to id
    }

    private fun closeVoiceTurn(state: ChatState): ChatState {
        val id = state.voice.turnId ?: return state
        return state.copy(voice = state.voice.copy(turnId = null)).patch(id) { it.copy(pending = false) }
    }

    // The user's words arrive as a growing string and are rewritten in place: the message is created on the first
    // partial because transcription only finalises at the turn boundary, after the answer it prompted.
    private fun setRequest(state: ChatState, text: String): ChatState {
        if (text.isBlank()) return state
        val (opened, id) = openVoiceTurn(state)
        return opened.copy(messages = opened.messages.map { if (it is ChatMessage.User && it.id == "$id-user") it.copy(text = text) else it })
    }

    private fun setAnswer(state: ChatState, text: String): ChatState {
        if (text.isBlank()) return state
        val (opened, id) = openVoiceTurn(state)
        return opened.patch(id) { it.copy(text = text) }
    }

    // Voice reports a call already rendered as `name(k='v')`; it is split, not parsed into fake structure.
    private fun addTool(state: ChatState, text: String): ChatState {
        if (text.isBlank()) return state
        val (opened, id) = openVoiceTurn(state)
        val count = opened.voice.toolCount + 1
        val paren = text.indexOf('(')
        val name = (if (paren > 0) text.substring(0, paren) else text).trim()
        val args = if (paren > 0) text.substring(paren + 1).trimEnd().removeSuffix(")").trim() else ""
        return opened.copy(voice = opened.voice.copy(toolCount = count))
            .patch(id) { it.copy(tools = it.tools + ToolCallItem("$id-c$count", name, text, args)) }
    }

    private fun finishTool(state: ChatState, result: String): ChatState {
        val id = state.voice.turnId ?: return state
        return state.patch(id) { m ->
            val index = m.tools.indexOfLast { it.status == ToolStatus.RUNNING }
            if (index < 0) m else m.copy(tools = m.tools.mapIndexed { i, t -> if (i == index) t.copy(status = ToolStatus.DONE, result = result) else t })
        }
    }

    private const val MAX_NOTES = 6

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
