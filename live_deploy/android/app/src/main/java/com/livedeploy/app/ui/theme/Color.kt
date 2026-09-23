package com.livedeploy.app.ui.theme

import androidx.compose.ui.graphics.Color

// Mirrors static/index.html's own CSS custom properties exactly (see that
// file's `:root { ... }` block and its `@media (prefers-color-scheme:
// dark)` counterpart) — same palette as the web app, both themes, so this
// app reads as the SAME product rather than a reskin.

object LightPalette {
    val bg = Color(0xFFF6F1E4)
    val paper = Color(0xFFFFFDF6)
    val panel = Color(0xFFEFE4C8)
    val sand = Color(0xFFF1E7CC)
    val ink = Color(0xFF1B1130)
    val parchment = Color(0xFF6B5A72)
    val line = Color(0xFF2A1B45)

    val accent = Color(0xFFE2711D)
    val accentSoft = Color(0xFFF6C177)
    val gain = Color(0xFF1F7A5C)
    val gainSoft = Color(0xFFDCEEE3)
    val loss = Color(0xFFB23A48)
    val lossSoft = Color(0xFFF7E1E1)
    val brass = Color(0xFFA6741A)
    val brassSoft = Color(0xFFF3E6C8)
    val info = Color(0xFF2D5C7A)
    val infoSoft = Color(0xFFDCEAF0)
}

object DarkPalette {
    val bg = Color(0xFF150E22)
    val paper = Color(0xFF1E1533)
    val panel = Color(0xFF251A40)
    val sand = Color(0xFF291C44)
    val ink = Color(0xFFF1E8DC)
    val parchment = Color(0xFFB8A8C9)
    val line = Color(0xFF4C3D74)

    val accent = Color(0xFFEB8A3D)
    val accentSoft = Color(0xFF8A5320)
    val gain = Color(0xFF4FD9A4)
    val gainSoft = Color(0xFF163A2E)
    val loss = Color(0xFFFF8B95)
    val lossSoft = Color(0xFF3D1B22)
    val brass = Color(0xFFE6B04A)
    val brassSoft = Color(0xFF3E2E10)
    val info = Color(0xFF7FC1E8)
    val infoSoft = Color(0xFF16303F)
}
