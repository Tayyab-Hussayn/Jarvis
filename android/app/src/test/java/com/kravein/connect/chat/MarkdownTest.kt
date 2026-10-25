package com.kravein.connect.chat

import kotlin.test.Test
import kotlin.test.assertEquals

class MarkdownTest {
    @Test fun inlineSpans() {
        assertEquals(
            listOf(Span.Text("a "), Span.Bold("b"), Span.Text(" "), Span.Code("c"), Span.Text(" "), Span.Link("d", "https://x.io/p")),
            Markdown.spans("a **b** `c` [d](https://x.io/p)"),
        )
    }

    @Test fun bareUrlKeepsTrailingPunctuationOut() {
        assertEquals(listOf(Span.Text("see "), Span.Link("https://a.b/c", "https://a.b/c"), Span.Text(".")), Markdown.spans("see https://a.b/c."))
    }

    @Test fun nonWebLinkTargetsStayText() {
        for (source in listOf("[x](javascript:alert(1))", "[x](intent://a)")) {
            val spans = Markdown.spans(source)
            assertEquals(true, spans.none { it is Span.Link }, source)
            assertEquals(source, spans.joinToString("") { (it as Span.Text).text }, source)
        }
    }

    @Test fun blocks() {
        val b = Markdown.parse("Intro\nline2\n\n- one\n* two\n1. three\n\n```\nx = 1\n  y\n```\nend")
        assertEquals(
            listOf(
                Block.Paragraph(listOf(Span.Text("Intro\nline2"))),
                Block.Bullet(listOf(Span.Text("one"))),
                Block.Bullet(listOf(Span.Text("two"))),
                Block.Bullet(listOf(Span.Text("three"))),
                Block.CodeBlock("x = 1\n  y"),
                Block.Paragraph(listOf(Span.Text("end"))),
            ),
            b,
        )
    }

    @Test fun unclosedFenceIsStillCode() {
        assertEquals(listOf(Block.CodeBlock("a\nb")), Markdown.parse("```\na\nb"))
    }

    @Test fun htmlIsLiteral() {
        assertEquals(listOf(Block.Paragraph(listOf(Span.Text("<b>x</b>")))), Markdown.parse("<b>x</b>"))
    }
}
