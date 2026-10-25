package com.kravein.connect.chat

import com.kravein.connect.core.DeviceCredential
import com.kravein.connect.network.PinnedHttp
import com.kravein.connect.network.UiSessionClient
import com.kravein.connect.network.UiSessionResult
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.flow.updateAndGet
import okhttp3.HttpUrl
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit

/**
 * The chat's one websocket to `/ws`, speaking exactly what the web client speaks. Authenticated with a short-lived
 * UI token from `/phone/ui-session` (never the device secret), over the pinned TLS client. Open only between
 * [start] and [stop] (the app being visible): no polling, no background socket — the phone channel already keeps
 * the PC reachable for notifications.
 */
class ChatClient(
    private val sessions: UiSessionClient = UiSessionClient(),
    private val now: () -> Long = System::currentTimeMillis,
) {
    /** What the voice side wants to hear about; called outside this client's lock. */
    interface VoiceSink {
        /** [state] is the chat state after [frame] was applied. */
        fun onFrame(frame: ServerFrame, state: ChatState)
        /** A binary frame: PCM16 mono 24 kHz model audio. */
        fun onAudio(pcm: ByteArray)
        /** The socket opened (true) or went away (false). */
        fun onLink(open: Boolean)
    }

    @Volatile var voiceSink: VoiceSink? = null

    /** Where the resumable-session id lives; the real (encrypted) store is installed by [ChatHub.install]. */
    @Volatile var sessionIds: SessionIdStore = MemorySessionIds()

    private val _state = MutableStateFlow(ChatState())
    val state: StateFlow<ChatState> = _state.asStateFlow()

    private val worker = Executors.newSingleThreadScheduledExecutor { r -> Thread(r, "kravein-chat").apply { isDaemon = true } }
    private var credential: DeviceCredential? = null
    private var socket: WebSocket? = null
    private var retry: ScheduledFuture<*>? = null
    private var ping: ScheduledFuture<*>? = null
    private var attempts = 0
    private var running = false

    /** When anything last arrived on the socket (any frame, any audio chunk). Read by the ping task. */
    @Volatile private var lastInboundAt = 0L

    /** Connect (or keep connected). Safe to call on every foreground. */
    @Synchronized
    fun start(credential: DeviceCredential) {
        val changed = this.credential?.let { it.host != credential.host || it.port != credential.port || it.deviceId != credential.deviceId } ?: false
        this.credential = credential
        if (running && !changed) return
        running = true
        if (changed) closeSocket()
        attempts = 0
        _state.update { ChatReducer.onLink(it, Link.CONNECTING) }
        scheduleConnect(0)
    }

    /** Leave: close the socket and everything it was doing becomes "unknown", like any other disconnect. */
    fun stop() {
        synchronized(this) {
            running = false
            retry?.cancel(false)
            closeSocket()
            _state.update { ChatReducer.onDisconnected(it) }
        }
        // Outside the lock: the sink takes its own locks, and a lock-order cycle between the two would hang the app.
        voiceSink?.onLink(false)
    }

    /** Back in the foreground or the network came back: do not wait for the backoff timer. */
    @Synchronized
    fun retryNow() {
        if (!running || socket != null) return
        retry?.cancel(false)
        attempts = 0
        if (_state.value.link == Link.REPLACED) _state.update { ChatReducer.onLink(it, Link.CONNECTING) }
        scheduleConnect(0)
    }

    /** Returns false when nothing was sent (not connected); the message then shows an honest "Not connected". */
    @Synchronized
    fun ask(text: String): Boolean {
        val trimmed = text.trim().take(ChatFrames.MAX_ASK_CHARS)
        if (trimmed.isEmpty() || _state.value.busy) return false
        // Typed and spoken turns share one counter (kept in the state, and restored from a replay).
        val id = "t${_state.value.seq + 1}"
        _state.update { ChatReducer.onAsk(it, id, trimmed) }
        if (!sendFrame(ChatFrames.ask(id, trimmed))) {
            _state.update { ChatReducer.onAskNotSent(it, id) }
            return false
        }
        return true
    }

    @Synchronized
    fun answerConfirmation(confirmId: String, approve: Boolean) {
        // The card is closed only if the reply really left; otherwise it stays open and the user can see it is stuck.
        if (sendFrame(ChatFrames.confirmReply(confirmId, approve))) {
            _state.update { ChatReducer.onConfirmAnswered(it, confirmId, approve) }
        }
    }

    @Synchronized
    fun stopTurn() {
        val sent = _state.value.busy && sendFrame(ChatFrames.cancel())
        _state.update { ChatReducer.onStopRequested(it, sent) }
    }

    @Synchronized
    fun newConversation() {
        if (_state.value.busy) return
        if (!sendFrame(ChatFrames.reset())) _state.update { ChatReducer.onFrame(it, ServerFrame.ResetOk, now()) }
    }

    /** Ask the server for a live voice session with this phone as microphone and speaker. False = not connected. */
    @Synchronized
    fun startVoice(): Boolean {
        if (_state.value.voice.phase.live) return true
        if (!sendFrame(ChatFrames.voiceStartDevice())) return false
        _state.update { ChatReducer.onVoiceRequested(it) }
        return true
    }

    /**
     * End the session. The server always answers with `stopped`; if the socket is gone the session ends on this side
     * at once (the server's grace period stops it on its own).
     */
    @Synchronized
    fun stopVoice() {
        if (sendFrame(ChatFrames.voiceStop())) _state.update { ChatReducer.onVoiceStopRequested(it) }
        else _state.update { ChatReducer.onVoiceOff(it) }
    }

    /** Voice ended on this side without the server saying so (microphone refused, link down too long). */
    fun voiceEnded(note: String?) = _state.update { ChatReducer.onVoiceOff(it, note) }

    /** One microphone frame. Dropped, not queued, when the socket cannot take it. */
    fun sendAudio(frame: ByteArray, length: Int): Boolean {
        val ws = socket ?: return false
        if (ws.queueSize() > MAX_QUEUED_AUDIO_BYTES) return false
        return ws.send(frame.toByteString(0, length))
    }

    fun isConnected(): Boolean = socket != null && _state.value.link == Link.OPEN

    fun dismissLoose(confirmId: String) = _state.update { ChatReducer.dropLoose(it, confirmId) }

    private fun sendFrame(frame: String): Boolean = socket?.send(frame) == true

    private fun scheduleConnect(delayMs: Long) {
        retry?.cancel(false)
        retry = worker.schedule({ connect() }, delayMs, TimeUnit.MILLISECONDS)
    }

    private fun connect() {
        val cred: DeviceCredential
        synchronized(this) {
            if (!running || socket != null) return
            cred = credential ?: return
        }
        // Blocking network call, on the worker thread.
        when (val result = sessions.fetch(cred)) {
            is UiSessionResult.Granted -> open(cred, result.token.value)
            // The server forgot this device: asking again cannot help. The shell sends the user to pairing.
            UiSessionResult.Refused -> synchronized(this) {
                running = false
                _state.update { ChatReducer.onLink(it, Link.UNAUTHORIZED) }
            }
            is UiSessionResult.Unreachable, is UiSessionResult.Failed -> synchronized(this) { planRetry() }
        }
    }

    private fun open(cred: DeviceCredential, token: String) {
        val resume = SessionResume.resumeParam(sessionIds, cred.deviceId)
        val url = HttpUrl.Builder().scheme(cred.scheme).host(cred.host).port(cred.port)
            .addPathSegments("ws").addQueryParameter("token", token)
            .also { if (resume != null) it.addQueryParameter("resume", resume) }
            .build()
        val http = PinnedHttp.clientFor(cred.scheme, cred.certSha256).newBuilder()
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .callTimeout(0, TimeUnit.MILLISECONDS)
            .build()
        synchronized(this) {
            if (!running || socket != null) return
            // No Origin header is sent by OkHttp; the server accepts a request without one and the token gates access.
            socket = http.newWebSocket(Request.Builder().url(url).build(), Listener())
        }
    }

    private fun closeSocket() {
        ping?.cancel(false)
        socket?.cancel()
        socket = null
    }

    @Synchronized
    private fun planRetry() {
        if (!running) return
        val plan = ChatRetry.next(attempts)
        attempts = plan.attempts
        _state.update { ChatReducer.onLink(it, plan.link) }
        scheduleConnect(plan.delayMs)
    }

    private companion object {
        /** Microphone frames queued in the socket beyond this are dropped: late audio is worse than missing audio. */
        const val MAX_QUEUED_AUDIO_BYTES = 256 * 1024L

        /** The server sends model audio in small chunks; this is far above any of them and far below a memory problem. */
        const val MAX_AUDIO_CHUNK_BYTES = 256 * 1024
    }

    private inner class Listener : WebSocketListener() {
        // A callback from a socket that is no longer the current one must change nothing.
        private fun current(ws: WebSocket) = synchronized(this@ChatClient) { socket === ws }

        override fun onOpen(webSocket: WebSocket, response: Response) {
            synchronized(this@ChatClient) {
                if (socket !== webSocket) return
                attempts = 0
                _state.update { ChatReducer.onLink(it, Link.OPEN) }
                lastInboundAt = now()
                ping = worker.scheduleWithFixedDelay(
                    {
                        synchronized(this@ChatClient) {
                            val ws = socket ?: return@synchronized
                            // A socket that accepts our pings and answers nothing is not connected. Cancelling it
                            // goes through `onFailure`, which reconnects and resumes the session.
                            if (ChatRetry.isDead(lastInboundAt, now())) ws.cancel() else ws.send(ChatFrames.ping())
                        }
                    },
                    ChatRetry.PING_INTERVAL_MS, ChatRetry.PING_INTERVAL_MS, TimeUnit.MILLISECONDS,
                )
            }
            voiceSink?.onLink(true)
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (!current(webSocket)) return
            lastInboundAt = now()
            val frame = ChatFrames.parse(text)
            // A frame we cannot parse is not worth tearing the socket down for.
            if (frame == ServerFrame.Malformed) return
            if (frame is ServerFrame.Hello) credential?.let { SessionResume.onHello(sessionIds, it.deviceId, frame) }
            val after = _state.updateAndGet { ChatReducer.onFrame(it, frame, now()) }
            voiceSink?.onFrame(frame, after)
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            // Model audio. Anything that is not a sane PCM chunk is not our server and is dropped.
            if (!current(webSocket)) return
            lastInboundAt = now()
            if (bytes.size == 0 || bytes.size > MAX_AUDIO_CHUNK_BYTES) return
            voiceSink?.onAudio(bytes.toByteArray())
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(code, null)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = lost(webSocket, code)

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = lost(webSocket, 0)

        private fun lost(webSocket: WebSocket, code: Int) {
            synchronized(this@ChatClient) {
                if (socket !== webSocket) return
                ping?.cancel(false)
                socket = null
                // Nothing will ever finish what this socket was doing (see ChatReducer.onDisconnected).
                _state.update { ChatReducer.onDisconnected(it) }
                if (ChatRetry.retriesAfter(code)) {
                    // A refused token (close 1008, or a handshake 401/403) gets a fresh one on the next attempt; only
                    // the device credential being refused ends the chat, and that is decided by /phone/ui-session
                    // in connect().
                    planRetry()
                } else {
                    // Another socket took the session over; "Retry now" is the user's way to take it back.
                    _state.update { ChatReducer.onLink(it, Link.REPLACED) }
                }
            }
            voiceSink?.onLink(false)
        }
    }
}
