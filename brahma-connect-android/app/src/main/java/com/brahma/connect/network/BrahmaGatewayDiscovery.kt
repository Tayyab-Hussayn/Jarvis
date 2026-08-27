package com.brahma.connect.network

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import com.brahma.connect.core.AgentStateStore
import com.brahma.connect.core.GatewayEndpoint

class BrahmaGatewayDiscovery(private val context: Context) {
    companion object {
        /**
         * Must match `service_name` on the gateway — see
         * `kravein_connect/gateway/discovery.py` and `server.py`. If the two
         * disagree the phone simply never finds the desktop, with no error to
         * explain why, so this is the one string in the app that cannot be
         * changed on its own.
         *
         * Was `_BRAHMA._tcp.` until the assistant was renamed to Kravein on
         * 2026-08-24. It lived as a duplicated literal in two places here,
         * which is how it was able to drift from the gateway in the first
         * place; there is now one copy.
         */
        private const val SERVICE_TYPE = "_KRAVEIN._tcp."
    }

    private val nsdManager = context.getSystemService(Context.NSD_SERVICE) as NsdManager
    private var discoveryListener: NsdManager.DiscoveryListener? = null
    private var resolveListener: NsdManager.ResolveListener? = null

    fun start(onFound: (GatewayEndpoint) -> Unit, onError: (String) -> Unit = {}) {
        stop()
        resolveListener = object : NsdManager.ResolveListener {
            override fun onResolveFailed(serviceInfo: NsdServiceInfo?, errorCode: Int) {
                onError("Resolve failed: $errorCode")
            }

            override fun onServiceResolved(serviceInfo: NsdServiceInfo) {
                val host = serviceInfo.host?.hostAddress.orEmpty()
                val port = serviceInfo.port
                if (host.isNotBlank() && port > 0) {
                    val endpoint = GatewayEndpoint(
                        name = serviceInfo.serviceName ?: "Kravein PC",
                        host = host,
                        port = port,
                    )
                    AgentStateStore.setGateway(endpoint)
                    onFound(endpoint)
                }
            }
        }

        discoveryListener = object : NsdManager.DiscoveryListener {
            override fun onStartDiscoveryFailed(serviceType: String?, errorCode: Int) {
                onError("Discovery start failed: $errorCode")
            }

            override fun onStopDiscoveryFailed(serviceType: String?, errorCode: Int) {
                onError("Discovery stop failed: $errorCode")
            }

            override fun onDiscoveryStarted(serviceType: String?) = Unit
            override fun onDiscoveryStopped(serviceType: String?) = Unit

            override fun onServiceFound(serviceInfo: NsdServiceInfo) {
                val serviceType = serviceInfo.serviceType ?: return
                if (!serviceType.contains(SERVICE_TYPE.trimEnd('.'), ignoreCase = true)) return
                resolveListener?.let { nsdManager.resolveService(serviceInfo, it) }
            }

            override fun onServiceLost(serviceInfo: NsdServiceInfo) = Unit
        }

        runCatching {
            nsdManager.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, discoveryListener)
        }.onFailure {
            onError(it.message ?: "Discovery unavailable.")
        }
    }

    fun stop() {
        discoveryListener?.let { listener ->
            runCatching { nsdManager.stopServiceDiscovery(listener) }
        }
        discoveryListener = null
        resolveListener = null
    }
}
