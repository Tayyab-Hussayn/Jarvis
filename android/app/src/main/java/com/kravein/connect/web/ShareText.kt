package com.kravein.connect.web

import java.net.URLEncoder

/**
 * Text shared into Kravein from another app. It becomes the `ask` query parameter of the web UI, which pre-fills the
 * composer (never sends). Only held in memory until the page is loaded; it is never logged or stored.
 */
object ShareText {
    /** The web UI's own cap on `ask`; longer text would be cut there anyway. */
    const val MAX_CHARS = 4000

    /** The text to pre-fill, or null when there is nothing worth sending. */
    fun clean(raw: CharSequence?): String? {
        val text = raw?.toString()?.trim().orEmpty()
        if (text.isEmpty()) return null
        if (text.length <= MAX_CHARS) return text
        // Never cut between the two halves of a surrogate pair: the server would receive a broken character.
        val end = if (Character.isHighSurrogate(text[MAX_CHARS - 1])) MAX_CHARS - 1 else MAX_CHARS
        return text.substring(0, end)
    }

    /** Percent-encoding with `%20` for spaces, so both `URLSearchParams` and `decodeURIComponent` read it back. */
    fun encode(text: String): String = URLEncoder.encode(text, "UTF-8").replace("+", "%20")
}
