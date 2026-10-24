package com.kravein.connect.network

import com.kravein.connect.commands.CommandRouter
import com.kravein.connect.confirm.ConfirmationBook
import com.kravein.connect.confirm.ConfirmationListener
import com.kravein.connect.confirm.ReplyOutcome
import com.kravein.connect.notice.NoticeDedupe
import com.kravein.connect.notice.NoticeListener
import com.kravein.connect.core.AgentStateStore
import com.kravein.connect.core.ConnectionState
import com.kravein.connect.core.DeviceCredential
import okhttp3.HttpUrl
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Socket glue around [PhoneSession]: opens `WS /phone/ws` with the device credential, feeds frames to the
 * session, reconnects with backoff, and stops for good when the server refuses the credential.
 * No `Origin` header is sent (OkHttp never adds one); the server refuses any request to the phone endpoints that has it.
 */
class PhoneClient(
    private val router: CommandRouter,
    private val helloInfo: (DeviceCredential) -> HelloInfo,
    private val confirmations: ConfirmationListener = ConfirmationListener.None,
    private val notices: NoticeListener = NoticeListener.None,
) {
    /** Open questions survive a reconnect; a tap while the socket is down must still be answered honestly. */
    val book = ConfirmationBook()

    /** Shared across reconnects: the PC may replay a notice on the new socket. */
    private val seenNotices = NoticeDedupe()

    // The socket stays open for hours, so it must not inherit the short call/read timeouts of the shared client.
    private val http = Http.client.newBuilder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .callTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(30, TimeUnit.SECONDS)
        .build()
    private val scheduler = Executors.newSingleThreadScheduledExecutor()

    private var socket: WebSocket? = null
    private var session: PhoneSession? = null
    private var credential: DeviceCredential? = null
    private var attempt = 0
    private var stopped = true

    @Synchronized
    fun connect(credential: DeviceCredential) {
        this.credential = credential
        stopped = false
        attempt = 0
        open(credential)
    }

    @Synchronized
    fun disconnect() {
        stopped = true
        socket?.close(1000, "Disconnected by user")
        socket = null
        session = null
        AgentStateStore.setConnectionState(ConnectionState.DISCONNECTED)
        AgentStateStore.setStatus("Disconnected")
    }

    /**
     * Answer a confirmation. Goes out only on the live, welcomed socket; anything else is reported, never queued,
     * because an approval delivered minutes later to a different situation is worse than none.
     */
    @Synchronized
    fun sendConfirmReply(requestId: String, approve: Boolean): ReplyOutcome {
        val current = session
        val live = socket
        if (stopped || current == null || live == null) return ReplyOutcome.NotConnected
        return when (val plan = current.planReply(requestId, approve)) {
            is ReplyPlan.Refused -> plan.outcome
            is ReplyPlan.Send -> if (live.send(plan.frame)) {
                current.replySent(requestId)
                ReplyOutcome.Sent
            } else {
                ReplyOutcome.NotConnected
            }
        }
    }

    private fun open(credential: DeviceCredential) {
        socket?.cancel()
        val url = HttpUrl.Builder().scheme("http").host(credential.host).port(credential.port).addPathSegments("phone/ws").build()
        val request = Request.Builder().url(url).header("Authorization", credential.authorization()).build()
        val newSession = PhoneSession(router, helloInfo(credential), book, notices = seenNotices) { UUID.randomUUID().toString().replace("-", "") }
        session = newSession
        AgentStateStore.setConnectionState(ConnectionState.CONNECTING)
        AgentStateStore.setStatus("Connecting to ${credential.host}")
        socket = http.newWebSocket(request, Listener(newSession))
    }

    private fun scheduleReconnect(delayMs: Long) {
        scheduler.schedule({
            synchronized(this) {
                val current = credential
                if (!stopped && current != null) open(current)
            }
        }, delayMs, TimeUnit.MILLISECONDS)
    }

    private inner class Listener(private val mine: PhoneSession) : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            apply(webSocket, mine.open())
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            apply(webSocket, mine.onText(text))
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = lost(webSocket, null, "closed $code")

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) =
            lost(webSocket, response?.code, "failed: ${t.javaClass.simpleName}")

        private fun apply(webSocket: WebSocket, out: SessionOutput) {
            out.send.forEach { webSocket.send(it) }
            for (event in out.events) {
                when (event) {
                    is SessionEvent.Ready -> {
                        attempt = 0
                        AgentStateStore.setConnectionState(ConnectionState.CONNECTED)
                        AgentStateStore.setStatus("Connected to ${event.serverName}")
                        AgentStateStore.setError(null)
                    }
                    is SessionEvent.ServerError -> {
                        AgentStateStore.addLog("Server error ${event.code.wire}: ${event.message}")
                        AgentStateStore.setError(event.message)
                    }
                    is SessionEvent.ConfirmRequested -> confirmations.onRequested(event.confirmation)
                    is SessionEvent.ConfirmClosed -> confirmations.onClosed(event.requestId, event.outcome)
                    is SessionEvent.NoticeReceived -> notices.onNotice(event.notice)
                    is SessionEvent.ReplyRejected -> confirmations.onReplyRejected(event.requestId)
                }
            }
            if (out.close) webSocket.close(1000, "closing")
        }

        private fun lost(webSocket: WebSocket, httpStatus: Int?, what: String) {
            synchronized(this@PhoneClient) {
                if (stopped || session !== mine || socket !== webSocket) return
                socket = null
                AgentStateStore.addLog("Socket $what")
                attempt += 1
                val action = ConnectionPolicy.onFailure(httpStatus, attempt)
                AgentStateStore.setConnectionState(ConnectionPolicy.stateAfter(action))
                when (action) {
                    is FailureAction.Retry -> {
                        AgentStateStore.setStatus("Reconnecting in ${action.delayMs / 1000}s")
                        scheduleReconnect(action.delayMs)
                    }
                    FailureAction.NeedsPairing -> {
                        // Stop reconnecting: the credential will not start working by itself. The stored
                        // credential stays until a new pairing replaces it.
                        stopped = true
                        AgentStateStore.setStatus("Pairing required")
                        AgentStateStore.setError(ConnectionPolicy.REPAIR_MESSAGE)
                    }
                }
            }
        }
    }
}
