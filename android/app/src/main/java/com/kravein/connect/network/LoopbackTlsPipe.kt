package com.kravein.connect.network

import java.io.Closeable
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.Collections
import javax.net.ssl.SSLSocketFactory

/**
 * The WebView cannot be given a trust manager, and Chromium rejects the PC's self-signed certificate on sub-resources
 * and websockets (net_error -202) whatever `onReceivedSslError` says. So the WebView talks plain HTTP to this pipe on
 * 127.0.0.1, and the pipe talks TLS to the PC through the pin-only socket factory. Bytes are copied untouched, which
 * keeps the websocket upgrade working. A certificate other than the pin fails the handshake, so the WebView's
 * connection is closed with nothing delivered.
 *
 * It listens on loopback only and lives exactly as long as the WebView. Other apps on the phone can reach the port,
 * but a page still needs the UI token to get anything from the server.
 */
class LoopbackTlsPipe(
    private val host: String,
    private val port: Int,
    private val sockets: SSLSocketFactory,
) : Closeable {
    private val server = ServerSocket(0, 16, InetAddress.getByName("127.0.0.1"))
    private val open: MutableSet<Socket> = Collections.synchronizedSet(HashSet())

    @Volatile private var closed = false

    val localPort: Int get() = server.localPort

    fun start(): LoopbackTlsPipe {
        Thread({ acceptLoop() }, "kravein-pipe-accept").apply { isDaemon = true }.start()
        return this
    }

    private fun acceptLoop() {
        while (!closed) {
            val client = try {
                server.accept()
            } catch (_: IOException) {
                return
            }
            Thread({ serve(client) }, "kravein-pipe-conn").apply { isDaemon = true }.start()
        }
    }

    private fun serve(client: Socket) {
        var upstream: Socket? = null
        try {
            track(client)
            val remote = sockets.createSocket(host, port)
            upstream = remote
            track(remote)
            (remote as javax.net.ssl.SSLSocket).startHandshake()
            val uplink = Thread({ copy(client.getInputStream(), remote.getOutputStream(), client, remote) }, "kravein-pipe-up")
            uplink.isDaemon = true
            uplink.start()
            copy(remote.getInputStream(), client.getOutputStream(), client, remote)
        } catch (_: IOException) {
            // Pin mismatch, PC down or the WebView went away: either way the connection simply ends.
        } finally {
            drop(client)
            upstream?.let { drop(it) }
        }
    }

    /** Copies until either side ends, then closes both so the other direction's blocked read unblocks. */
    private fun copy(from: InputStream, to: OutputStream, a: Socket, b: Socket) {
        val buffer = ByteArray(16 * 1024)
        try {
            while (true) {
                val n = from.read(buffer)
                if (n < 0) break
                to.write(buffer, 0, n)
                to.flush()
            }
        } catch (_: IOException) {
        } finally {
            drop(a)
            drop(b)
        }
    }

    private fun track(socket: Socket) {
        open.add(socket)
        if (closed) drop(socket)
    }

    private fun drop(socket: Socket) {
        open.remove(socket)
        try {
            socket.close()
        } catch (_: IOException) {
        }
    }

    override fun close() {
        closed = true
        try {
            server.close()
        } catch (_: IOException) {
        }
        // Closing a TLS socket writes close_notify, which is network I/O: on the main thread (WebView teardown runs
        // there) Android throws NetworkOnMainThreadException and the app crashes. The listener is already closed.
        val sockets = synchronized(open) { open.toList() }
        Thread({ sockets.forEach { drop(it) } }, "kravein-pipe-close").apply { isDaemon = true }.start()
    }
}
