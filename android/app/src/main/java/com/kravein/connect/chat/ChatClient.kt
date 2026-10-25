package com.kravein.connect.chat

import com.kravein.connect.core.DeviceCredential
import com.kravein.connect.network.PinnedHttp
import com.kravein.connect.network.UiSessionClient
import com.kravein.connect.network.UiSessionResult
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import okhttp3.HttpUrl
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
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
    private val _state = MutableStateFlow(ChatState())
    val state: StateFlow<ChatState> = _state.asStateFlow()

    private val worker = Executors.newSingleThreadScheduledExecutor { r -> Thread(r, "kravein-chat").apply { isDaemon = true } }
    private var credential: DeviceCredential? = null
    private var socket: WebSocket? = null
    private var retry: ScheduledFuture<*>? = null
    private var ping: ScheduledFuture<*>? = null
    private var attempts = 0
    private var seq = 0
    private var running = false

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
    @Synchronized
    fun stop() {
        running = false
        retry?.cancel(false)
        closeSocket()
        _state.update { ChatReducer.onDisconnected(it) }
    }

    /** Back in the foreground or the network came back: do not wait for the backoff timer. */
    @Synchronized
    fun retryNow() {
        if (!running || socket != null) return
        retry?.cancel(false)
        attempts = 0
        scheduleConnect(0)
    }

    /** Returns false when nothing was sent (not connected); the message then shows an honest "Not connected". */
    @Synchronized
    fun ask(text: String): Boolean {
        val trimmed = text.trim().take(ChatFrames.MAX_ASK_CHARS)
        if (trimmed.isEmpty() || _state.value.busy) return false
        seq += 1
        val id = "t$seq"
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
        val url = HttpUrl.Builder().scheme(cred.scheme).host(cred.host).port(cred.port)
            .addPathSegments("ws").addQueryParameter("token", token).build()
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

    private inner class Listener : WebSocketListener() {
        // A callback from a socket that is no longer the current one must change nothing.
        private fun current(ws: WebSocket) = synchronized(this@ChatClient) { socket === ws }

        override fun onOpen(webSocket: WebSocket, response: Response) {
            synchronized(this@ChatClient) {
                if (socket !== webSocket) return
                attempts = 0
                _state.update { ChatReducer.onLink(it, Link.OPEN) }
                ping = worker.scheduleWithFixedDelay(
                    { synchronized(this@ChatClient) { socket?.send(ChatFrames.ping()) } },
                    ChatRetry.PING_INTERVAL_MS, ChatRetry.PING_INTERVAL_MS, TimeUnit.MILLISECONDS,
                )
            }
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (!current(webSocket)) return
            val frame = ChatFrames.parse(text)
            // A frame we cannot parse is not worth tearing the socket down for.
            if (frame != ServerFrame.Malformed) _state.update { ChatReducer.onFrame(it, frame, now()) }
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(code, null)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = lost(webSocket)

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = lost(webSocket)

        private fun lost(webSocket: WebSocket) {
            synchronized(this@ChatClient) {
                if (socket !== webSocket) return
                ping?.cancel(false)
                socket = null
                // Nothing will ever finish what this socket was doing (see ChatReducer.onDisconnected).
                _state.update { ChatReducer.onDisconnected(it) }
                // A refused token (close 1008, or a handshake 401/403) gets a fresh one on the next attempt; only the
                // device credential being refused ends the chat, and that is decided by /phone/ui-session in connect().
                planRetry()
            }
        }
    }
}
