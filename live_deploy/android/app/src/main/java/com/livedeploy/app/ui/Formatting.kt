package com.livedeploy.app.ui

import androidx.compose.runtime.Composable
import com.livedeploy.app.ui.theme.PnlColors
import java.text.NumberFormat
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
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

private val istZone = ZoneId.of("Asia/Kolkata")
private val istInstantFormat = DateTimeFormatter.ofLocalizedDateTime(FormatStyle.MEDIUM, FormatStyle.SHORT)
    .withLocale(Locale("en", "IN"))
    .withZone(istZone)

/** An ISO-8601 UTC instant (e.g. MuteStatus.mutedUntil, always wire-
 * format from the backend — see that model's own comment) rendered in
 * IST, matching the web app's own fmtDateTime (static/js/api.js). Falls
 * back to the raw string rather than crashing if the backend ever sends
 * something unparseable — this is a display label, never re-parsed. */
fun formatIsoInstantIst(iso: String): String =
    try {
        istInstantFormat.format(Instant.parse(iso))
    } catch (e: Exception) {
        iso
    }
