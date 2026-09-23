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
