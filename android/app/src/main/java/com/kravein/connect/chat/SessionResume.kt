package com.kravein.connect.chat

/**
 * Where the resumable-session handle (#111) is kept between runs. The id is a bearer of that conversation for this
 * device, so the real implementation is the encrypted preferences; it is never logged.
 */
interface SessionIdStore {
    fun get(deviceId: String): String?
    fun put(deviceId: String, sessionId: String)
    fun clear(deviceId: String)
}

class MemorySessionIds : SessionIdStore {
    private val ids = HashMap<String, String>()
    override fun get(deviceId: String): String? = ids[deviceId]
    override fun put(deviceId: String, sessionId: String) { ids[deviceId] = sessionId }
    override fun clear(deviceId: String) { ids.remove(deviceId) }
}

/** The rules around the stored id: what to ask for on connect and what to remember from `hello`. */
object SessionResume {
    /** The `resume` query value for this device, or null for a first connection. */
    fun resumeParam(store: SessionIdStore, deviceId: String): String? =
        store.get(deviceId)?.takeIf { it.isNotBlank() && it.length <= ChatFrames.MAX_SESSION_ID }

    /**
     * Remember the id the server named. A refused resume comes back as a new id with `resumed=false`, which replaces
     * the old one: the old conversation is gone for good.
     */
    fun onHello(store: SessionIdStore, deviceId: String, hello: ServerFrame.Hello) {
        val id = hello.sessionId ?: return
        if (store.get(deviceId) != id) store.put(deviceId, id)
    }
}
