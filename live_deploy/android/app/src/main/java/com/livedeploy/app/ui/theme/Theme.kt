package com.livedeploy.app.ui.theme

import android.app.Activity
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

private val LightScheme = lightColorScheme(
    primary = LightPalette.accent,
    onPrimary = LightPalette.paper,
    secondary = LightPalette.brass,
    background = LightPalette.bg,
    surface = LightPalette.paper,
    surfaceVariant = LightPalette.panel,
    onBackground = LightPalette.ink,
    onSurface = LightPalette.ink,
    onSurfaceVariant = LightPalette.parchment,
    outline = LightPalette.line,
    error = LightPalette.loss,
    errorContainer = LightPalette.lossSoft,
)

private val DarkScheme = darkColorScheme(
    primary = DarkPalette.accent,
    onPrimary = DarkPalette.bg,
    secondary = DarkPalette.brass,
    background = DarkPalette.bg,
    surface = DarkPalette.paper,
    surfaceVariant = DarkPalette.panel,
    onBackground = DarkPalette.ink,
    onSurface = DarkPalette.ink,
    onSurfaceVariant = DarkPalette.parchment,
    outline = DarkPalette.line,
    error = DarkPalette.loss,
    errorContainer = DarkPalette.lossSoft,
)

/** Semantic P&L colors — used directly (not via MaterialTheme.colorScheme,
 * which has no "gain/loss" concept of its own) by every screen that
 * renders a signed money value. Mirrors the web app's own .pos/.neg CSS
 * classes 1:1. */
object PnlColors {
    val gain: androidx.compose.ui.graphics.Color
        @Composable get() = if (isSystemInDarkTheme()) DarkPalette.gain else LightPalette.gain
    val loss: androidx.compose.ui.graphics.Color
        @Composable get() = if (isSystemInDarkTheme()) DarkPalette.loss else LightPalette.loss
}

@Composable
fun LiveDeployTheme(content: @Composable () -> Unit) {
    val darkTheme = isSystemInDarkTheme()
    val colorScheme = if (darkTheme) DarkScheme else LightScheme

    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            window.statusBarColor = colorScheme.background.toArgb()
            WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = !darkTheme
        }
    }

    MaterialTheme(
        colorScheme = colorScheme,
        typography = LiveDeployTypography,
        content = content,
    )
}
