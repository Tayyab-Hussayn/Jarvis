package com.kravein.connect.chat

sealed interface Span {
    data class Text(val text: String) : Span
    data class Bold(val text: String) : Span
    data class Code(val text: String) : Span
    data class Link(val label: String, val url: String) : Span
}

sealed interface Block {
    data class Paragraph(val spans: List<Span>) : Block
    data class Bullet(val spans: List<Span>) : Block
    data class CodeBlock(val text: String) : Block
}

/**
 * Markdown-lite for answers: paragraphs, `-`/`*`/`1.` lists, fenced code, **bold**, `code` and links. No dependency,
 * and no HTML: the model's text is untrusted, so anything else stays literal text. Only http(s) links are links —
 * a `javascript:` or `intent:` target is shown as plain text, never made tappable.
 */
object Markdown {
    private val bullet = Regex("""^\s*(?:[-*•]|\d{1,3}[.)])\s+(.*)$""")
    private val inline = Regex("""\*\*(.+?)\*\*|`([^`\n]+)`|\[([^\]\n]+)]\((\S+?)\)|(https?://[^\s<>()]+[^\s<>().,;:!?])""")

    fun parse(source: String): List<Block> {
        val blocks = ArrayList<Block>()
        val lines = source.replace("\r\n", "\n").split('\n')
        val para = StringBuilder()
        fun flush() {
            if (para.isNotEmpty()) {
                blocks += Block.Paragraph(spans(para.toString()))
                para.clear()
            }
        }
        var i = 0
        while (i < lines.size) {
            val line = lines[i]
            if (line.trimStart().startsWith("```")) {
                flush()
                val code = StringBuilder()
                i++
                // An unclosed fence runs to the end: a half-streamed answer still reads as code.
                while (i < lines.size && !lines[i].trimStart().startsWith("```")) {
                    if (code.isNotEmpty()) code.append('\n')
                    code.append(lines[i])
                    i++
                }
                blocks += Block.CodeBlock(code.toString())
                i++
                continue
            }
            val item = bullet.matchEntire(line)
            when {
                line.isBlank() -> flush()
                item != null -> {
                    flush()
                    blocks += Block.Bullet(spans(item.groupValues[1]))
                }
                else -> {
                    if (para.isNotEmpty()) para.append('\n')
                    para.append(line)
                }
            }
            i++
        }
        flush()
        return blocks
    }

    fun spans(text: String): List<Span> {
        val out = ArrayList<Span>()
        var last = 0
        for (m in inline.findAll(text)) {
            if (m.range.first > last) out += Span.Text(text.substring(last, m.range.first))
            val g = m.groupValues
            out += when {
                g[1].isNotEmpty() -> Span.Bold(g[1])
                g[2].isNotEmpty() -> Span.Code(g[2])
                g[3].isNotEmpty() -> if (isWebUrl(g[4])) Span.Link(g[3], g[4]) else Span.Text(m.value)
                else -> Span.Link(g[5], g[5])
            }
            last = m.range.last + 1
        }
        if (last < text.length) out += Span.Text(text.substring(last))
        return out
    }

    fun isWebUrl(url: String): Boolean {
        val lower = url.lowercase()
        return (lower.startsWith("http://") || lower.startsWith("https://")) && url.length <= 2048 && url.none { it.isWhitespace() }
    }
}
