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
import com.kravein.connect.core.DiscoveryStatus
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
    private val discovery: PcDiscovery = PcDiscovery.None,
    private val hosts: HostListener = HostListener.None,
) {
    /** Open questions survive a reconnect; a tap while the socket is down must still be answered honestly. */
    val book = ConfirmationBook()

    /** Shared across reconnects: the PC may replay a notice on the new socket. */
    private val seenNotices = NoticeDedupe()

    // The socket stays open for hours, so it must not inherit the short call/read timeouts of the shared client.
    // Derived from the pinned client for this server (or the plain one for http), so the pin applies to the socket too.
    private fun httpFor(credential: DeviceCredential) = PinnedHttp.clientFor(credential.scheme, credential.certSha256).newBuilder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .callTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(30, TimeUnit.SECONDS)
        .build()

    // Consecutive failures that look like plain http hitting a TLS server; only counted without a pin.
    private var tlsHints = 0
    private val scheduler = Executors.newSingleThreadScheduledExecutor()

    private var socket: WebSocket? = null
    private var session: PhoneSession? = null
    private var credential: DeviceCredential? = null
    private var attempt = 0
    private var stopped = true

    // The stored address plus any rediscovered one being tried; see HostRecovery.
    private var recovery: HostRecovery? = null
    private var ready = false
    private var searching = false
    private var seen: List<DiscoveredService> = emptyList()

    @Synchronized
    fun connect(credential: DeviceCredential) {
        this.credential = credential
        stopped = false
        attempt = 0
        tlsHints = 0
        recovery = HostRecovery(credential)
        stopSearch()
        open(credential)
    }

    @Synchronized
    fun disconnect() {
        stopped = true
        stopSearch()
        socket?.close(1000, "Disconnected by user")
        socket = null
        session = null
        ready = false
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

    /**
     * The network changed (Wi-Fi switch, VPN, airplane mode off): an earlier "not found" may now be wrong, so retry
     * the stored address right away and search again. Does nothing while connected — a dead socket fails by itself
     * and then searches — and never polls.
     */
    @Synchronized
    fun networkChanged() {
        val current = recovery ?: return
        if (stopped || ready) return
        current.networkChanged()
        attempt = 0
        stopSearch()
        startSearch()
        open(current.active())
    }

    private fun startSearch() {
        val current = recovery ?: return
        // Without a pin there is nothing to match a broadcast against (see HostRecovery.candidates).
        if (searching || current.stored.certSha256 == null) return
        searching = true
        seen = emptyList()
        AgentStateStore.setDiscovery(DiscoveryStatus.SEARCHING)
        scheduler.schedule({
            synchronized(this) {
                if (searching && recovery?.trial == null) AgentStateStore.setDiscovery(DiscoveryStatus.NOT_FOUND)
            }
        }, NOT_FOUND_AFTER_MS, TimeUnit.MILLISECONDS)
        discovery.start { found -> onServices(found) }
    }

    /** Connected (or given up): stop listening to the network, it costs battery. */
    private fun stopSearch() {
        if (searching) discovery.stop()
        searching = false
        seen = emptyList()
        AgentStateStore.setDiscovery(DiscoveryStatus.IDLE)
    }

    @Synchronized
    private fun onServices(found: List<DiscoveredService>) {
        if (stopped || !searching) return
        seen = found
        tryNextCandidate()
    }

    /** Opens the next rediscovered address, if any. Returns whether one was started. */
    private fun tryNextCandidate(): Boolean {
        val current = recovery ?: return false
        val candidate = current.nextCandidate(seen) ?: return false
        AgentStateStore.addLog("Trying ${candidate.host}:${candidate.port} found on this network")
        AgentStateStore.setDiscovery(DiscoveryStatus.SEARCHING)
        attempt = 0
        open(candidate)
        return true
    }

    private fun open(credential: DeviceCredential) {
        ready = false
        socket?.cancel()
        val url = HttpUrl.Builder().scheme(credential.scheme).host(credential.host).port(credential.port).addPathSegments("phone/ws").build()
        val request = Request.Builder().url(url).header("Authorization", credential.authorization()).build()
        val newSession = PhoneSession(router, helloInfo(credential), book, notices = seenNotices) { UUID.randomUUID().toString().replace("-", "") }
        session = newSession
        AgentStateStore.setConnectionState(ConnectionState.CONNECTING)
        AgentStateStore.setStatus("Connecting to ${credential.host}")
        socket = httpFor(credential).newWebSocket(request, Listener(newSession))
    }

    private fun scheduleReconnect(delayMs: Long) {
        scheduler.schedule({
            synchronized(this) {
                // A trial or a fresh attempt may already be running (found service, network change).
                val current = recovery
                if (!stopped && current != null && socket == null) open(current.active())
            }
        }, delayMs, TimeUnit.MILLISECONDS)
    }

    /** The welcomed socket [mine] is the live one: save a rediscovered address and stop searching. */
    private fun connected(mine: PhoneSession) {
        if (session !== mine) return
        ready = true
        val moved = recovery?.trialSucceeded()
        if (moved != null) {
            credential = moved
            AgentStateStore.addLog("Found the PC again at ${moved.host}:${moved.port}")
            hosts.onHostChanged(moved)
        }
        stopSearch()
    }

    private companion object {
        const val NOT_FOUND_AFTER_MS = 10_000L
    }

    private inner class Listener(private val mine: PhoneSession) : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            apply(webSocket, mine.open())
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            apply(webSocket, mine.onText(text))
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = lost(webSocket, null, "closed $code", null)

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) =
            lost(webSocket, response?.code, "failed: ${t.javaClass.simpleName}", t)

        private fun apply(webSocket: WebSocket, out: SessionOutput) {
            out.send.forEach { webSocket.send(it) }
            for (event in out.events) {
                when (event) {
                    is SessionEvent.Ready -> {
                        synchronized(this@PhoneClient) { connected(mine) }
                        attempt = 0
                        tlsHints = 0
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

        private fun lost(webSocket: WebSocket, httpStatus: Int?, what: String, failure: Throwable?) {
            synchronized(this@PhoneClient) {
                if (stopped || session !== mine || socket !== webSocket) return
                socket = null
                ready = false
                AgentStateStore.addLog("Socket $what")
                if (recovery?.trial != null) {
                    // A rediscovered address that does not work (wrong certificate, refusal, silence) never ends the
                    // pairing: the stored address stays as it was and the search goes on.
                    recovery?.trialFailed()
                    if (tryNextCandidate()) return
                    AgentStateStore.setStatus("Reconnecting")
                    scheduleReconnect(ConnectionPolicy.backoffMs(1))
                    return
                }
                attempt += 1
                val unpinned = credential?.certSha256 == null
                tlsHints = if (unpinned && TlsFailures.looksLikePlainHttpToTlsServer(failure)) tlsHints + 1 else 0
                val action = ConnectionPolicy.onFailure(httpStatus, attempt, failure, tlsHints)
                AgentStateStore.setConnectionState(ConnectionPolicy.stateAfter(action))
                when (action) {
                    is FailureAction.Retry -> {
                        AgentStateStore.setStatus("Reconnecting in ${action.delayMs / 1000}s")
                        startSearch()
                        // A service may already be known (search left running between attempts).
                        if (!tryNextCandidate()) scheduleReconnect(action.delayMs)
                    }
                    FailureAction.NeedsPairing -> {
                        // Stop reconnecting: the credential will not start working by itself. The stored
                        // credential stays until a new pairing replaces it.
                        stopped = true
                        stopSearch()
                        AgentStateStore.setStatus("Pairing required")
                        AgentStateStore.setError(ConnectionPolicy.REPAIR_MESSAGE)
                    }
                    FailureAction.PinMismatch -> {
                        // A different certificate is never retried: it is either a reinstall or someone else.
                        stopped = true
                        stopSearch()
                        AgentStateStore.setStatus("Certificate does not match")
                        AgentStateStore.setError(TlsFailures.MISMATCH_MESSAGE)
                    }
                    FailureAction.NeedsTlsRepair -> {
                        stopped = true
                        stopSearch()
                        AgentStateStore.setStatus("Pairing required")
                        AgentStateStore.setError(TlsFailures.RE_PAIR_FOR_TLS_MESSAGE)
                    }
                }
            }
        }
    }
}
