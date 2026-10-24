package com.kravein.connect.ui.theme

/**
 * The web UI's "aurora" design tokens (`web/app/tokens.css`) as plain ARGB values, so the native shell looks
 * like the same product. Kept free of Compose types so a JVM test can compare them with the CSS file: if the web
 * palette changes and this file does not, `KraveinTokensTest` fails.
 */
class KraveinPalette(
    val dark: Boolean,
    val bg: Long,
    val surface: Long,
    val surfaceRaised: Long,
    val surfaceSunken: Long,
    val border: Long,
    val borderStrong: Long,
    val glass: Long,
    val glassStrong: Long,
    val glassBorder: Long,
    val text: Long,
    val textMuted: Long,
    val textFaint: Long,
    val accent: Long,
    val accentContrast: Long,
    val accent2: Long,
    val gradientEnd: Long,
    val moderate: Long,
    val destructive: Long,
    val critical: Long,
    val criticalContrast: Long,
)

object KraveinTokens {
    val Dark = KraveinPalette(
        dark = true,
        bg = 0xFF04060C, surface = 0xFF0B1019, surfaceRaised = 0xFF121A28, surfaceSunken = 0xFF070B13,
        border = 0xFF1B2638, borderStrong = 0xFF2B3A54,
        glass = 0xB80E1522, glassStrong = 0xE0121B2B, glassBorder = 0x248CB4FF,
        text = 0xFFEAF0FB, textMuted = 0xFFA6B3C8, textFaint = 0xFF8493AB,
        accent = 0xFF3DE3FF, accentContrast = 0xFF031016, accent2 = 0xFF9D8CFF, gradientEnd = 0xFF7B8CFF,
        moderate = 0xFFFFC94D, destructive = 0xFFFF8F4A, critical = 0xFFFF6B7A, criticalContrast = 0xFF1A0206,
    )

    val Light = KraveinPalette(
        dark = false,
        bg = 0xFFEEF2F9, surface = 0xFFF8FAFF, surfaceRaised = 0xFFFFFFFF, surfaceSunken = 0xFFE4EAF4,
        border = 0xFFD5DEEC, borderStrong = 0xFFB3C0D6,
        glass = 0xB8FFFFFF, glassStrong = 0xEBFFFFFF, glassBorder = 0x24284678,
        text = 0xFF0C1424, textMuted = 0xFF44536B, textFaint = 0xFF566580,
        accent = 0xFF006B82, accentContrast = 0xFFFFFFFF, accent2 = 0xFF5A45D6, gradientEnd = 0xFF4A47D0,
        moderate = 0xFF8A5A00, destructive = 0xFFA63F06, critical = 0xFFB3182F, criticalContrast = 0xFFFFFFFF,
    )

    fun palette(dark: Boolean) = if (dark) Dark else Light

    /** Radii in dp (`--radius-sm/-/-md/-lg/-xl`). */
    const val RADIUS_SM = 8
    const val RADIUS = 12
    const val RADIUS_MD = 14
    const val RADIUS_LG = 18
    const val RADIUS_XL = 24

    /** Type scale in sp (the web rem sizes x 16): xs .6875, sm .8125, base .9375, lg 1.125, xl 1.5, 2xl 2. */
    const val TEXT_XS = 11f
    const val TEXT_SM = 13f
    const val TEXT_BASE = 15f
    const val TEXT_LG = 18f
    const val TEXT_XL = 24f
    const val TEXT_2XL = 32f

    /** `--tracking-label` 0.1em, used on uppercase section labels. */
    const val TRACKING_LABEL_EM = 0.1f

    /** Window / WebView ground before the page paints; equals `--bg` so there is no flash. */
    fun background(dark: Boolean): Int = palette(dark).bg.toInt()

    /** Notification accent: the dark-theme cyan on every theme, since the tray is dark-tinted by the system. */
    val NOTIFICATION_ACCENT: Int = Dark.accent.toInt()

    /** WCAG contrast ratio of two opaque ARGB colours. */
    fun contrast(a: Long, b: Long): Double {
        val la = luminance(a)
        val lb = luminance(b)
        return (maxOf(la, lb) + 0.05) / (minOf(la, lb) + 0.05)
    }

    private fun luminance(argb: Long): Double {
        fun channel(shift: Int): Double {
            val c = ((argb shr shift) and 0xFF) / 255.0
            return if (c <= 0.03928) c / 12.92 else Math.pow((c + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * channel(16) + 0.7152 * channel(8) + 0.0722 * channel(0)
    }
}
