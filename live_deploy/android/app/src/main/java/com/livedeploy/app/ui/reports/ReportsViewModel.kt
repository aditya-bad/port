package com.livedeploy.app.ui.reports

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.livedeploy.app.data.ApiResult
import com.livedeploy.app.data.DeploymentsRepository
import com.livedeploy.app.network.PnlReport
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

sealed class ReportsUiState {
    data object Loading : ReportsUiState()
    data class Loaded(val report: PnlReport) : ReportsUiState()
    data class Error(val message: String) : ReportsUiState()
}

/**
 * Mirrors the web app's own Reports page (static/js/reports.js) at the
 * single-period-drill-down level — Daily/Weekly/Monthly/Yearly/All Time,
 * stepped via Prev/Next/Latest, one GET /portfolio/pnl-report call per
 * state change. Deliberately NOT ported: the Recent Periods trend table,
 * the P&L contribution bar chart, and the Daily P&L Calendar heatmap —
 * this is the period drill-down itself (the part this session's own
 * "Yearly/All Time" request was actually about), not every section of
 * that page.
 *
 * `period`/`offset` are plain vars, not their own StateFlow — same
 * pattern DeploymentDetailViewModel's own `currentId` already uses:
 * every mutation here is immediately followed by a `load()` call that
 * updates `_uiState`, which IS observed, so a screen reading
 * `viewModel.period`/`viewModel.offset` right after a uiState emission
 * always sees the value that produced it.
 */
class ReportsViewModel(private val repository: DeploymentsRepository) : ViewModel() {

    private val _uiState = MutableStateFlow<ReportsUiState>(ReportsUiState.Loading)
    val uiState: StateFlow<ReportsUiState> = _uiState.asStateFlow()

    var period: String = "day"
        private set
    var offset: Int = 0
        private set

    init {
        load()
    }

    fun load() {
        viewModelScope.launch {
            _uiState.value = ReportsUiState.Loading
            when (val result = repository.pnlReport(period, offset)) {
                is ApiResult.Success -> _uiState.value = ReportsUiState.Loaded(result.data)
                is ApiResult.Failure -> _uiState.value = ReportsUiState.Error(result.message)
            }
        }
    }

    fun switchPeriod(newPeriod: String) {
        if (newPeriod == period) return
        period = newPeriod
        offset = 0
        load()
    }

    // delta=+1 -> Prev (further into the past); delta=-1 -> Next (toward
    // the present) -- same convention as the web app's own step(). All
    // Time has nowhere to step (exactly one all-time range), matching
    // the backend's own period_bounds(), which accepts but ignores a
    // nonzero offset for period="all" -- this guard is what actually
    // keeps Prev/Next inert for that tab, same reasoning as the web
    // app's own disabled-button guard.
    fun step(delta: Int) {
        if (period == "all") return
        val next = offset + delta
        if (next < 0) return
        offset = next
        load()
    }

    fun jumpToLatest() {
        if (period == "all" || offset == 0) return
        offset = 0
        load()
    }
}
