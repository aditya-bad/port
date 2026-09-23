package com.livedeploy.app.widget

import android.content.Context
import androidx.glance.appwidget.GlanceAppWidgetManager
import androidx.glance.appwidget.state.updateAppWidgetState
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.livedeploy.app.LiveDeployApplication
import com.livedeploy.app.data.ApiResult
import com.livedeploy.app.data.DeploymentsRepository
import com.livedeploy.app.ui.formatSignedMoney
import kotlinx.coroutines.flow.first
import java.util.concurrent.TimeUnit

/**
 * Refreshes the P&L widget's own persisted state (see PnlWidgetKeys) by
 * calling the same GET /deployments this app's Dashboard/Deployments
 * screens use, then re-composing every placed widget instance.
 *
 * Scheduled two ways, both landing here:
 *   - schedule(): a periodic WorkManager request, 15 minutes (the
 *     platform-enforced minimum for periodic work — see WorkManager's own
 *     documented floor; this is NOT a choice made here, just the fastest
 *     Android allows).
 *   - refreshNow(): a one-off request fired immediately whenever a widget
 *     is first placed/re-enabled, so a newly-added widget doesn't sit on
 *     "Tap to set up"-stale data for up to 15 minutes before its first
 *     real refresh.
 */
class PnlWidgetWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val app = applicationContext as LiveDeployApplication
        val repository = DeploymentsRepository(app.settingsStore)

        val configured = app.settingsStore.settings.first()
        val glanceIds = GlanceAppWidgetManager(applicationContext).getGlanceIds(PnlWidget::class.java)
        if (glanceIds.isEmpty()) return Result.success()   // no widgets currently placed — nothing to do

        if (configured == null) {
            glanceIds.forEach { id ->
                updateAppWidgetState(applicationContext, PnlWidget().stateDefinition, id) { prefs ->
                    prefs.toMutablePreferences().apply {
                        this[PnlWidgetKeys.STATUS] = "unconfigured"
                        remove(PnlWidgetKeys.MESSAGE)
                    }
                }
                PnlWidget().update(applicationContext, id)
            }
            return Result.success()
        }

        when (val result = repository.listDeployments()) {
            is ApiResult.Success -> {
                val deployments = result.data.filter { it.includeInReports }
                val totalPnl = deployments.sumOf { it.totalPnl }
                val activeCount = result.data.count { it.status == "active" }
                glanceIds.forEach { id ->
                    updateAppWidgetState(applicationContext, PnlWidget().stateDefinition, id) { prefs ->
                        prefs.toMutablePreferences().apply {
                            this[PnlWidgetKeys.STATUS] = "ok"
                            this[PnlWidgetKeys.MESSAGE] = formatSignedMoney(totalPnl)
                            this[PnlWidgetKeys.ACTIVE_COUNT] = activeCount.toString()
                            this[PnlWidgetKeys.UPDATED_AT] = System.currentTimeMillis()
                        }
                    }
                    PnlWidget().update(applicationContext, id)
                }
                return Result.success()
            }
            is ApiResult.Failure -> {
                glanceIds.forEach { id ->
                    updateAppWidgetState(applicationContext, PnlWidget().stateDefinition, id) { prefs ->
                        prefs.toMutablePreferences().apply {
                            this[PnlWidgetKeys.STATUS] = "error"
                            this[PnlWidgetKeys.MESSAGE] = result.message
                        }
                    }
                    PnlWidget().update(applicationContext, id)
                }
                // retry() (not failure()) -- a transient network blip
                // shouldn't need waiting for the next full 15-minute
                // period; WorkManager's own backoff policy handles
                // spacing retries out.
                return Result.retry()
            }
        }
    }

    companion object {
        private const val PERIODIC_WORK_NAME = "pnl_widget_periodic_refresh"
        private const val ONE_TIME_WORK_NAME = "pnl_widget_immediate_refresh"

        fun schedule(context: Context) {
            val request = PeriodicWorkRequestBuilder<PnlWidgetWorker>(15, TimeUnit.MINUTES)
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build(),
                )
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                PERIODIC_WORK_NAME, ExistingPeriodicWorkPolicy.KEEP, request,
            )
        }

        fun refreshNow(context: Context) {
            val request = OneTimeWorkRequestBuilder<PnlWidgetWorker>().build()
            WorkManager.getInstance(context).enqueueUniqueWork(
                ONE_TIME_WORK_NAME, ExistingWorkPolicy.REPLACE, request,
            )
        }

        fun unschedule(context: Context) {
            WorkManager.getInstance(context).cancelUniqueWork(PERIODIC_WORK_NAME)
        }
    }
}
