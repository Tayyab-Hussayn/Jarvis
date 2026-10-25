package com.kravein.connect.network

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import java.net.Inet6Address

/**
 * `_kravein._tcp` discovery on top of the platform NsdManager (no extra dependency). It only reports what it saw;
 * [HostRecovery] decides what is worth trying, and the TLS pin decides who is really the PC.
 */
class NsdPcDiscovery(context: Context) : PcDiscovery {
    private val nsd = context.applicationContext.getSystemService(Context.NSD_SERVICE) as NsdManager
    private var listener: NsdManager.DiscoveryListener? = null
    private var onFound: ((List<DiscoveredService>) -> Unit)? = null
    private val found = linkedMapOf<String, DiscoveredService>()
    private val toResolve = ArrayDeque<NsdServiceInfo>()
    private var resolving = false

    @Synchronized
    override fun start(onFound: (List<DiscoveredService>) -> Unit) {
        if (listener != null) return
        this.onFound = onFound
        val discovery = object : NsdManager.DiscoveryListener {
            override fun onServiceFound(info: NsdServiceInfo) = enqueue(info)

            override fun onServiceLost(info: NsdServiceInfo) = forget(info.serviceName)

            override fun onStartDiscoveryFailed(serviceType: String?, errorCode: Int) = reset()
            override fun onStopDiscoveryFailed(serviceType: String?, errorCode: Int) = Unit
            override fun onDiscoveryStarted(serviceType: String?) = Unit
            override fun onDiscoveryStopped(serviceType: String?) = Unit
        }
        listener = discovery
        try {
            nsd.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, discovery)
        } catch (_: RuntimeException) {
            listener = null
        }
    }

    @Synchronized
    override fun stop() {
        val active = listener ?: return
        try {
            nsd.stopServiceDiscovery(active)
        } catch (_: RuntimeException) {
            // Already stopped by the system.
        }
        reset()
    }

    @Synchronized
    private fun reset() {
        listener = null
        onFound = null
        found.clear()
        toResolve.clear()
        resolving = false
    }

    @Synchronized
    private fun forget(name: String) {
        if (found.remove(name) != null) onFound?.invoke(found.values.toList())
    }

    @Synchronized
    private fun enqueue(info: NsdServiceInfo) {
        // Android allows one resolve at a time on older versions, so they queue.
        if (info.serviceType?.startsWith(SERVICE_TYPE) != true) return
        toResolve.addLast(info)
        resolveNext()
    }

    @Synchronized
    private fun resolveNext() {
        if (resolving || listener == null) return
        val next = toResolve.removeFirstOrNull() ?: return
        resolving = true
        @Suppress("DEPRECATION")
        try {
            nsd.resolveService(next, object : NsdManager.ResolveListener {
                override fun onResolveFailed(info: NsdServiceInfo?, errorCode: Int) = resolved(null)
                override fun onServiceResolved(info: NsdServiceInfo) = resolved(info)
            })
        } catch (_: RuntimeException) {
            resolved(null)
        }
    }

    @Synchronized
    private fun resolved(info: NsdServiceInfo?) {
        resolving = false
        val service = info?.let(::toService)
        if (service != null && listener != null) {
            found[info.serviceName] = service
            onFound?.invoke(found.values.toList())
        }
        resolveNext()
    }

    private fun toService(info: NsdServiceInfo): DiscoveredService? {
        val address = info.host ?: return null
        // A link-local IPv6 address needs a scope id that a URL cannot carry; the PC also answers on IPv4.
        if (address is Inet6Address && address.isLinkLocalAddress) return null
        val host = address.hostAddress?.substringBefore('%') ?: return null
        return DiscoveredService(
            host = host,
            port = info.port,
            fp = info.attributes["fp"]?.toString(Charsets.UTF_8),
            version = info.attributes["v"]?.toString(Charsets.UTF_8),
        )
    }

    private companion object {
        const val SERVICE_TYPE = "_kravein._tcp"
    }
}
