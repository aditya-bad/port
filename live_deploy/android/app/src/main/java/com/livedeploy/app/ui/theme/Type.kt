package com.livedeploy.app.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

// The web app pairs a condensed display face with a monospace body
// (static/index.html's --font-display/--font-body) — this app uses the
// platform's default sans/monospace instead of bundling matching font
// files, which would need actual font asset files this session can't
// source/verify licensing for. Numeric values (P&L, quantities) use
// FontFamily.Monospace specifically, same tabular-figures intent as the
// web app's own font-variant-numeric use wherever digits line up.
val LiveDeployTypography = Typography(
    headlineSmall = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.Bold, fontSize = 22.sp),
    titleLarge = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.Bold, fontSize = 18.sp),
    titleMedium = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.SemiBold, fontSize = 15.sp),
    bodyLarge = TextStyle(fontFamily = FontFamily.SansSerif, fontSize = 15.sp),
    bodyMedium = TextStyle(fontFamily = FontFamily.SansSerif, fontSize = 13.sp),
    labelSmall = TextStyle(fontFamily = FontFamily.SansSerif, fontWeight = FontWeight.SemiBold, fontSize = 11.sp),
)

val MonoNumbers = FontFamily.Monospace
