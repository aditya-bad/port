package com.livedeploy.app.ui.dashboard

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.livedeploy.app.data.ApiResult
import com.livedeploy.app.data.DeploymentsRepository
import com.livedeploy.app.network.Deployment
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

data class DashboardKpis(
    val totalPnl: Double,
    val realizedPnl: Double,
    val unrealizedPnl: Double,
    val activeCount: Int,
    val pausedCount: Int,
    val stoppedCount: Int,
)

sealed class DashboardUiState {
    data object Loading : DashboardUiState()
    data class Loaded(val kpis: DashboardKpis) : DashboardUiState()
    data class Error(val message: String) : DashboardUiState()
}

/** GET /health (kite_connected/needs_login) + GET /notifications/mute-
 * status merged into one shape for the Dashboard's own Kite-status chip
 * — see DashboardScreen's KiteStatusRow/KiteStatusDialog. null (not
 * modeled as its own loading/error state) just means "not fetched yet
 * or the health check itself failed" — the chip shows a neutral
 * "Checking…" for that rather than its own full error screen, since
 * losing Kite status shouldn't block the KPI grid from rendering. */
data class DashboardKiteStatus(
    val connected: Boolean,
    val needsLogin: Boolean,
    val muted: Boolean,
    val mutedUntil: String?,
)

/** Deliberately client-side aggregation over the SAME GET /deployments
 * list Deployments screen already fetches (each row already carries its
 * own realized_pnl/unrealized_pnl, enriched server-side — see
 * routers/deployments.py's _enrich_pnl_many), not a port of the web
 * Dashboard's own richer per-mode "active period" logic
 * (routers/ux_summary.py) — that's a fair v1 simplification, not an
 * oversight; see this project's own README for what's intentionally not
 * ported yet. */
class DashboardViewModel(private val repository: DeploymentsRepository) : ViewModel() {

    private val _uiState = MutableStateFlow<DashboardUiState>(DashboardUiState.Loading)
    val uiState: StateFlow<DashboardUiState> = _uiState.asStateFlow()

    private val _kiteStatus = MutableStateFlow<DashboardKiteStatus?>(null)
    val kiteStatus: StateFlow<DashboardKiteStatus?> = _kiteStatus.asStateFlow()

    init {
        refresh()
    }

    fun refresh() {
        viewModelScope.launch {
            _uiState.value = DashboardUiState.Loading
            when (val result = repository.listDeployments()) {
                is ApiResult.Success -> _uiState.value = DashboardUiState.Loaded(computeKpis(result.data))
                is ApiResult.Failure -> _uiState.value = DashboardUiState.Error(result.message)
            }
        }
        refreshKiteStatus()
    }

    // Two independent calls, not folded into DashboardUiState — Kite
    // connectivity and the KPI grid have nothing to do with each other
    // (one failing shouldn't block the other from showing), same
    // "separate concern" reasoning the web app's own #statusBar follows
    // against its own deployments list.
    fun refreshKiteStatus() {
        viewModelScope.launch {
            val health = repository.health()
            val mute = repository.muteStatus()
            val healthData = (health as? ApiResult.Success)?.data
            _kiteStatus.value = if (healthData != null) {
                DashboardKiteStatus(
                    connected = healthData.kiteConnected,
                    needsLogin = healthData.needsLogin,
                    muted = (mute as? ApiResult.Success)?.data?.muted ?: false,
                    mutedUntil = (mute as? ApiResult.Success)?.data?.mutedUntil,
                )
            } else {
                null
            }
        }
    }

    // "Mark as holiday" — mutes kite_disconnected/kite_reconnected
    // alerts for the rest of today, resuming automatically at 7am IST
    // tomorrow (the backend decides the exact cutoff — see routers/
    // notifications.py's mute-today). Re-fetches status either way so
    // the chip/dialog reflect whatever the server actually stored, not
    // an assumed success.
    fun muteToday() {
        viewModelScope.launch {
            repository.muteToday()
            refreshKiteStatus()
        }
    }

    fun unmute() {
        viewModelScope.launch {
            repository.unmute()
            refreshKiteStatus()
        }
    }

    private fun computeKpis(deployments: List<Deployment>): DashboardKpis {
        val reportable = deployments.filter { it.includeInReports }
        return DashboardKpis(
            totalPnl = reportable.sumOf { it.totalPnl },
            realizedPnl = reportable.sumOf { it.realizedPnl },
            unrealizedPnl = reportable.sumOf { it.unrealizedPnl },
            activeCount = deployments.count { it.status == "active" },
            pausedCount = deployments.count { it.status == "paused" },
            stoppedCount = deployments.count { it.status == "stopped" },
        )
    }
}
