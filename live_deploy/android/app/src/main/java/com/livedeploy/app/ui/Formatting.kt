package com.livedeploy.app.ui

import androidx.compose.runtime.Composable
import com.livedeploy.app.ui.theme.PnlColors
import java.text.NumberFormat
import java.util.Locale

// Matches the web app's own fmtMoney/fmtSignedMoney (static/js/api.js) —
// en-IN grouping (lakhs/crores commas), a leading rupee sign, no decimal
// places (this app deals in whole-rupee P&L everywhere it's shown, same
// as the web UI).
private val inrFormat: NumberFormat = NumberFormat.getCurrencyInstance(Locale("en", "IN")).apply {
    maximumFractionDigits = 0
    minimumFractionDigits = 0
}

fun formatMoney(value: Double): String = inrFormat.format(value)

fun formatSignedMoney(value: Double): String {
    val formatted = inrFormat.format(kotlin.math.abs(value))
    return if (value < 0) "-$formatted" else "+$formatted"
}

@Composable
fun pnlColor(value: Double) = if (value < 0) PnlColors.loss else PnlColors.gain
