package com.kravein.connect.ui

import com.kravein.connect.ui.theme.KraveinPalette
import com.kravein.connect.ui.theme.KraveinTokens
import java.io.File
import kotlin.math.roundToInt
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/** The native shell must look like the web UI: its palette is compared with `web/app/tokens.css` itself. */
class KraveinTokensTest {
    private val css = File(System.getProperty("kravein.tokens.css")!!).readText()
    private val resDir = File(System.getProperty("kravein.res.dir")!!)

    // :root holds the dark values; the light overrides sit in the prefers-color-scheme: light block.
    private val lightStart = css.indexOf("prefers-color-scheme: light")
    private val darkCss = css.substring(0, css.indexOf("@theme inline"))
    private val lightCss = css.substring(lightStart)

    private fun raw(block: String, name: String): String {
        val match = Regex("""--$name:\s*([^;]+);""").find(block) ?: error("--$name not in tokens.css block")
        return match.groupValues[1].trim()
    }

    /** `#rrggbb` or `rgba(r, g, b, a)` as 0xAARRGGBB. */
    private fun argb(value: String): Long {
        if (value.startsWith("#")) return 0xFF000000 or value.removePrefix("#").toLong(16)
        val parts = Regex("""rgba\(([^)]+)\)""").find(value)!!.groupValues[1].split(",").map { it.trim() }
        val alpha = (parts[3].toDouble() * 255).roundToInt().toLong()
        return (alpha shl 24) or (parts[0].toLong() shl 16) or (parts[1].toLong() shl 8) or parts[2].toLong()
    }

    private fun check(palette: KraveinPalette, block: String, fallback: String) {
        // A token the light block does not override keeps its dark value.
        fun token(name: String) = argb(runCatching { raw(block, name) }.getOrElse { raw(fallback, name) })
        val expected = mapOf(
            "bg" to palette.bg, "surface" to palette.surface, "surface-raised" to palette.surfaceRaised,
            "surface-sunken" to palette.surfaceSunken, "border" to palette.border, "border-strong" to palette.borderStrong,
            "glass" to palette.glass, "glass-strong" to palette.glassStrong, "glass-border" to palette.glassBorder,
            "text" to palette.text, "text-muted" to palette.textMuted, "text-faint" to palette.textFaint,
            "accent" to palette.accent, "accent-contrast" to palette.accentContrast, "accent-2" to palette.accent2,
            "moderate" to palette.moderate, "destructive" to palette.destructive, "critical" to palette.critical,
            "critical-contrast" to palette.criticalContrast,
        )
        for ((name, native) in expected) {
            assertEquals(token(name), native, "--$name (${if (palette.dark) "dark" else "light"}) differs from tokens.css")
        }
        val gradient = Regex("""#[0-9a-fA-F]{6}""").findAll(raw(block, "gradient-accent")).map { argb(it.value) }.toList()
        assertEquals(token("accent"), gradient[0])
        assertEquals(gradient[1], palette.gradientEnd, "gradient end")
    }

    @Test fun darkPaletteMatchesWebTokens() = check(KraveinTokens.Dark, darkCss, darkCss)

    @Test fun lightPaletteMatchesWebTokens() = check(KraveinTokens.Light, lightCss, darkCss)

    @Test fun radiiAndTypeScaleMatchWebTokens() {
        fun px(name: String) = raw(darkCss, name).removeSuffix("px").toInt()
        fun sp(name: String) = raw(darkCss, name).removeSuffix("rem").toFloat() * 16f
        assertEquals(px("radius-sm"), KraveinTokens.RADIUS_SM)
        assertEquals(px("radius"), KraveinTokens.RADIUS)
        assertEquals(px("radius-md"), KraveinTokens.RADIUS_MD)
        assertEquals(px("radius-lg"), KraveinTokens.RADIUS_LG)
        assertEquals(px("radius-xl"), KraveinTokens.RADIUS_XL)
        assertEquals(sp("text-xs"), KraveinTokens.TEXT_XS, 0.01f)
        assertEquals(sp("text-sm"), KraveinTokens.TEXT_SM, 0.01f)
        assertEquals(sp("text-base"), KraveinTokens.TEXT_BASE, 0.01f)
        assertEquals(sp("text-lg"), KraveinTokens.TEXT_LG, 0.01f)
        assertEquals(sp("text-xl"), KraveinTokens.TEXT_XL, 0.01f)
        assertEquals(sp("text-2xl"), KraveinTokens.TEXT_2XL, 0.01f)
        assertEquals(raw(darkCss, "tracking-label"), "${KraveinTokens.TRACKING_LABEL_EM}em")
    }

    @Test fun textPairsMeetWcagAaInBothThemes() {
        for (p in listOf(KraveinTokens.Dark, KraveinTokens.Light)) {
            val pairs = listOf(
                "text on bg" to (p.text to p.bg), "text on surface" to (p.text to p.surface),
                "text on raised" to (p.text to p.surfaceRaised),
                "muted on bg" to (p.textMuted to p.bg), "muted on raised" to (p.textMuted to p.surfaceRaised),
                "faint on bg" to (p.textFaint to p.bg),
                "accent on bg" to (p.accent to p.bg), "accent on raised" to (p.accent to p.surfaceRaised),
                "on-accent text" to (p.accentContrast to p.accent),
                "critical on bg" to (p.critical to p.bg), "critical on raised" to (p.critical to p.surfaceRaised),
            )
            for ((label, pair) in pairs) {
                val ratio = KraveinTokens.contrast(pair.first, pair.second)
                assertTrue(ratio >= 4.5, "${if (p.dark) "dark" else "light"} $label is ${"%.2f".format(ratio)}:1, below WCAG AA")
            }
        }
    }

    @Test fun androidResourceColorsAreTheWebTokens() {
        fun color(dir: String, name: String): Long {
            val xml = File(resDir, "$dir/colors.xml").readText()
            val hex = Regex("""<color name="$name">#([0-9A-Fa-f]{6})</color>""").find(xml)!!.groupValues[1]
            return 0xFF000000 or hex.toLong(16)
        }
        // The window and splash ground is the web --bg, so nothing flashes between splash, WebView and page.
        assertEquals(KraveinTokens.Light.bg, color("values", "kravein_bg"))
        assertEquals(KraveinTokens.Dark.bg, color("values-night", "kravein_bg"))
        assertEquals(KraveinTokens.Light.bg, color("values", "kravein_splash_bg"))
        assertEquals(KraveinTokens.Dark.bg, color("values-night", "kravein_splash_bg"))
        assertEquals(KraveinTokens.Light.accent, color("values", "kravein_accent"))
        assertEquals(KraveinTokens.Dark.accent, color("values-night", "kravein_accent"))
        assertEquals(KraveinTokens.Dark.bg, color("values", "kravein_icon_bg"))
    }

    @Test fun webViewAndNotificationColorsComeFromTheTokens() {
        assertEquals(KraveinTokens.Dark.bg.toInt(), KraveinTokens.background(dark = true))
        assertEquals(KraveinTokens.Light.bg.toInt(), KraveinTokens.background(dark = false))
        assertEquals(KraveinTokens.Dark.accent.toInt(), KraveinTokens.NOTIFICATION_ACCENT)
    }
}
