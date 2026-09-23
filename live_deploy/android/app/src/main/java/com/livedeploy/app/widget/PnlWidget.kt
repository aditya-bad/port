package com.livedeploy.app.widget

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.action.actionStartActivity
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.provideContent
import androidx.glance.background
import androidx.glance.color.ColorProvider
import androidx.glance.currentState
import androidx.glance.layout.Alignment
import androidx.glance.layout.Column
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.padding
import androidx.glance.state.PreferencesGlanceStateDefinition
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import com.livedeploy.app.MainActivity

/** Keys for the small per-widget-instance state Glance persists on our
 * behalf (see PreferencesGlanceStateDefinition below) — written by
 * PnlWidgetWorker after each refresh, read here on every recomposition. */
object PnlWidgetKeys {
    val STATUS = stringPreferencesKey("status")             // "ok" | "error" | "unconfigured"
    val MESSAGE = stringPreferencesKey("message")            // error text, or the formatted P&L on success
    val ACTIVE_COUNT = stringPreferencesKey("active_count")
    val UPDATED_AT = longPreferencesKey("updated_at_millis")
}

class PnlWidget : GlanceAppWidget() {
    override val stateDefinition = PreferencesGlanceStateDefinition

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        provideContent {
            val prefs = currentState<Preferences>()
            WidgetContent(prefs)
        }
    }
}

@Composable
private fun WidgetContent(prefs: Preferences) {
    val status = prefs[PnlWidgetKeys.STATUS] ?: "unconfigured"
    val message = prefs[PnlWidgetKeys.MESSAGE]
    val activeCount = prefs[PnlWidgetKeys.ACTIVE_COUNT]

    Column(
        modifier = GlanceModifier
            .fillMaxSize()
            .background(ColorProvider(day = Color(0xFFF6F1E4), night = Color(0xFF150E22)))
            .padding(12.dp)
            .clickable(actionStartActivity<MainActivity>()),
        horizontalAlignment = Alignment.Horizontal.Start,
        verticalAlignment = Alignment.Vertical.CenterVertically,
    ) {
        Text(
            "live_deploy",
            style = TextStyle(
                fontSize = 11.sp,
                fontWeight = FontWeight.Medium,
                color = ColorProvider(day = Color(0xFF6B5A72), night = Color(0xFFB8A8C9)),
            ),
        )
        when (status) {
            "ok" -> {
                Text(
                    message ?: "—",
                    style = TextStyle(
                        fontSize = 22.sp,
                        fontWeight = FontWeight.Bold,
                        color = pnlColorProvider(message),
                    ),
                )
                Text(
                    "$activeCount active",
                    style = TextStyle(
                        fontSize = 11.sp,
                        color = ColorProvider(day = Color(0xFF6B5A72), night = Color(0xFFB8A8C9)),
                    ),
                )
            }
            "unconfigured" -> Text(
                "Tap to set up",
                style = TextStyle(fontSize = 13.sp, color = ColorProvider(day = Color(0xFF1B1130), night = Color(0xFFF1E8DC))),
            )
            else -> Text(
                message ?: "Couldn't refresh",
                style = TextStyle(fontSize = 12.sp, color = ColorProvider(day = Color(0xFFB23A48), night = Color(0xFFFF8B95))),
            )
        }
    }
}

@Composable
private fun pnlColorProvider(formattedValue: String?): ColorProvider {
    val negative = formattedValue?.startsWith("-") == true
    return if (negative) {
        ColorProvider(day = Color(0xFFB23A48), night = Color(0xFFFF8B95))
    } else {
        ColorProvider(day = Color(0xFF1F7A5C), night = Color(0xFF4FD9A4))
    }
}

class PnlWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = PnlWidget()

    override fun onUpdate(
        context: Context,
        appWidgetManager: android.appwidget.AppWidgetManager,
        appWidgetIds: IntArray,
    ) {
        super.onUpdate(context, appWidgetManager, appWidgetIds)
        // First placement (and every OS-triggered update) both schedule
        // the SAME periodic worker — WorkManager's KEEP policy (see
        // PnlWidgetWorker.schedule) makes re-scheduling on an already-
        // running periodic request a harmless no-op, so there's no need
        // to distinguish "first add" from "OS backstop tick" here.
        PnlWidgetWorker.schedule(context)
        PnlWidgetWorker.refreshNow(context)
    }

    override fun onEnabled(context: Context) {
        super.onEnabled(context)
        PnlWidgetWorker.schedule(context)
        PnlWidgetWorker.refreshNow(context)
    }

    override fun onDisabled(context: Context) {
        super.onDisabled(context)
        PnlWidgetWorker.unschedule(context)
    }
}
