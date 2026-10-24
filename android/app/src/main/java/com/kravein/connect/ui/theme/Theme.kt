package com.kravein.connect.ui.theme

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Colours the Material scheme has no slot for (glass fill, hairline, danger ramp), read through [KraveinTheme]. */
class KraveinColors(palette: KraveinPalette) {
    val glass = Color(palette.glass)
    val glassStrong = Color(palette.glassStrong)
    val glassBorder = Color(palette.glassBorder)
    val accent2 = Color(palette.accent2)
    val gradient = Brush.linearGradient(listOf(Color(palette.accent), Color(palette.gradientEnd)))
    val moderate = Color(palette.moderate)
    val destructive = Color(palette.destructive)
    val critical = Color(palette.critical)
}

private val LocalKraveinColors = staticCompositionLocalOf { KraveinColors(KraveinTokens.Dark) }

object KraveinTheme {
    val colors: KraveinColors
        @Composable @ReadOnlyComposable get() = LocalKraveinColors.current
}

private fun scheme(p: KraveinPalette): ColorScheme =
    if (p.dark) {
        darkColorScheme(
            primary = Color(p.accent), onPrimary = Color(p.accentContrast),
            secondary = Color(p.accent2), onSecondary = Color(p.accentContrast),
            tertiary = Color(p.moderate),
            background = Color(p.bg), onBackground = Color(p.text),
            surface = Color(p.surface), onSurface = Color(p.text),
            surfaceVariant = Color(p.surfaceRaised), onSurfaceVariant = Color(p.textMuted),
            surfaceContainer = Color(p.surface), surfaceContainerHigh = Color(p.surfaceRaised),
            outline = Color(p.borderStrong), outlineVariant = Color(p.border),
            error = Color(p.critical), onError = Color(p.criticalContrast),
        )
    } else {
        lightColorScheme(
            primary = Color(p.accent), onPrimary = Color(p.accentContrast),
            secondary = Color(p.accent2), onSecondary = Color(p.accentContrast),
            tertiary = Color(p.moderate),
            background = Color(p.bg), onBackground = Color(p.text),
            surface = Color(p.surface), onSurface = Color(p.text),
            surfaceVariant = Color(p.surfaceRaised), onSurfaceVariant = Color(p.textMuted),
            surfaceContainer = Color(p.surface), surfaceContainerHigh = Color(p.surfaceRaised),
            outline = Color(p.borderStrong), outlineVariant = Color(p.border),
            error = Color(p.critical), onError = Color(p.criticalContrast),
        )
    }

// Inter is not bundled (APK size); the platform sans never falls to a monospace face, which is what the web
// token comment warns about.
private val Sans = FontFamily.SansSerif

private fun style(size: Float, weight: FontWeight, tracking: Float = 0f) =
    TextStyle(fontFamily = Sans, fontSize = size.sp, fontWeight = weight, letterSpacing = tracking.sp, lineHeight = (size * 1.4f).sp)

private fun typography() = Typography(
    displayLarge = style(KraveinTokens.TEXT_2XL + 8, FontWeight.Black, -1f),
    displayMedium = style(KraveinTokens.TEXT_2XL, FontWeight.Bold, -0.5f),
    headlineLarge = style(KraveinTokens.TEXT_2XL, FontWeight.Bold, -0.5f),
    headlineMedium = style(KraveinTokens.TEXT_XL, FontWeight.Bold, -0.3f),
    headlineSmall = style(KraveinTokens.TEXT_XL - 4, FontWeight.Bold),
    titleLarge = style(KraveinTokens.TEXT_LG + 2, FontWeight.SemiBold),
    titleMedium = style(KraveinTokens.TEXT_LG, FontWeight.SemiBold),
    titleSmall = style(KraveinTokens.TEXT_BASE, FontWeight.SemiBold),
    bodyLarge = style(KraveinTokens.TEXT_BASE + 1, FontWeight.Normal),
    bodyMedium = style(KraveinTokens.TEXT_BASE, FontWeight.Normal),
    bodySmall = style(KraveinTokens.TEXT_SM, FontWeight.Normal),
    labelLarge = style(KraveinTokens.TEXT_BASE - 1, FontWeight.SemiBold),
    // Section labels: callers upper-case them; tracked at --tracking-label.
    labelMedium = style(KraveinTokens.TEXT_SM, FontWeight.SemiBold, KraveinTokens.TEXT_SM * KraveinTokens.TRACKING_LABEL_EM),
    labelSmall = style(KraveinTokens.TEXT_XS, FontWeight.SemiBold, KraveinTokens.TEXT_XS * KraveinTokens.TRACKING_LABEL_EM),
)

private val KraveinShapes = Shapes(
    extraSmall = RoundedCornerShape(KraveinTokens.RADIUS_SM.dp),
    small = RoundedCornerShape(KraveinTokens.RADIUS.dp),
    medium = RoundedCornerShape(KraveinTokens.RADIUS_MD.dp),
    large = RoundedCornerShape(KraveinTokens.RADIUS_LG.dp),
    extraLarge = RoundedCornerShape(KraveinTokens.RADIUS_XL.dp),
)

/** Dark first, like the web UI; the light palette follows the system setting. */
@Composable
fun KraveinConnectTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val palette = KraveinTokens.palette(dark)
    CompositionLocalProvider(LocalKraveinColors provides KraveinColors(palette)) {
        MaterialTheme(colorScheme = scheme(palette), typography = typography(), shapes = KraveinShapes, content = content)
    }
}

/** The web `.btn-primary`: accent gradient fill, accent-contrast text, the 12 dp radius. */
@Composable
fun GradientButton(onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, content: @Composable RowScope.() -> Unit) {
    val shape = RoundedCornerShape(KraveinTokens.RADIUS.dp)
    val fill = if (enabled) KraveinTheme.colors.gradient else Brush.linearGradient(List(2) { MaterialTheme.colorScheme.surfaceVariant })
    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier.background(fill, shape),
        shape = shape,
        colors = ButtonDefaults.buttonColors(
            containerColor = Color.Transparent,
            contentColor = MaterialTheme.colorScheme.onPrimary,
            disabledContainerColor = Color.Transparent,
            disabledContentColor = MaterialTheme.colorScheme.onSurfaceVariant,
        ),
        contentPadding = PaddingValues(horizontal = 20.dp, vertical = 12.dp),
        content = content,
    )
}

/** The web `.glass` hairline. */
@Composable
fun glassBorder(): BorderStroke = BorderStroke(1.dp, KraveinTheme.colors.glassBorder)
