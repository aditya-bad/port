package com.livedeploy.app.ui.reports

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.livedeploy.app.network.PnlDeploymentBreakdown
import com.livedeploy.app.network.PnlReport
import com.livedeploy.app.network.PnlStrategyBreakdown
import com.livedeploy.app.ui.AppViewModelFactory
import com.livedeploy.app.ui.formatSignedMoney
import com.livedeploy.app.ui.pnlColor

/**
 * The period drill-down half of the web app's Reports page (static/js/
 * reports.js) — Daily/Weekly/Monthly/Yearly/All Time, stepped via Prev/
 * Next/Latest, each period's stat row plus its By Strategy / By
 * Deployment breakdowns. See ReportsViewModel's own docstring for
 * exactly what this intentionally leaves out (trend table, contribution
 * chart, calendar heatmap) — this is the part of that page this
 * session's own "add Yearly/All Time" request was actually about.
 */
private val PERIODS = listOf("day" to "Daily", "week" to "Weekly", "month" to "Monthly", "year" to "Yearly", "all" to "All time")

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ReportsScreen(factory: AppViewModelFactory) {
    val viewModel: ReportsViewModel = viewModel(factory = factory)
    val uiState by viewModel.uiState.collectAsState()
    val isAllTime = viewModel.period == "all"

    Scaffold(
        topBar = { TopAppBar(title = { Text("Reports") }) },
    ) { padding ->
        Column(modifier = Modifier.fillMaxSize().padding(padding)) {
            PeriodTabs(selected = viewModel.period, onSelect = viewModel::switchPeriod)
            PeriodNavRow(
                label = (uiState as? ReportsUiState.Loaded)?.report?.label ?: "—",
                offset = viewModel.offset,
                isAllTime = isAllTime,
                onPrev = { viewModel.step(1) },
                onNext = { viewModel.step(-1) },
                onLatest = viewModel::jumpToLatest,
            )
            Box(Modifier.fillMaxSize()) {
                when (val state = uiState) {
                    is ReportsUiState.Loading -> Box(
                        Modifier.fillMaxSize(),
                        contentAlignment = Alignment.Center,
                    ) { CircularProgressIndicator() }

                    is ReportsUiState.Error -> Box(
                        Modifier.fillMaxSize().padding(24.dp),
                        contentAlignment = Alignment.Center,
                    ) { Text(state.message, color = MaterialTheme.colorScheme.error) }

                    is ReportsUiState.Loaded -> ReportsBody(state.report)
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PeriodTabs(selected: String, onSelect: (String) -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState())
            .padding(horizontal = 12.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        PERIODS.forEach { (value, label) ->
            FilterChip(
                selected = selected == value,
                onClick = { onSelect(value) },
                label = { Text(label) },
                colors = FilterChipDefaults.filterChipColors(
                    selectedContainerColor = MaterialTheme.colorScheme.primary,
                    selectedLabelColor = MaterialTheme.colorScheme.onPrimary,
                ),
            )
        }
    }
}

@Composable
private fun PeriodNavRow(
    label: String,
    offset: Int,
    isAllTime: Boolean,
    onPrev: () -> Unit,
    onNext: () -> Unit,
    onLatest: () -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        IconButton(onClick = onPrev, enabled = !isAllTime) {
            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Previous period")
        }
        Text(label, style = MaterialTheme.typography.titleMedium)
        Row {
            IconButton(onClick = onNext, enabled = !isAllTime && offset > 0) {
                Icon(Icons.AutoMirrored.Filled.ArrowForward, contentDescription = "Next period")
            }
            TextButton(onClick = onLatest, enabled = !isAllTime && offset > 0) { Text("Latest") }
        }
    }
}

@Composable
private fun ReportsBody(report: PnlReport) {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item { StatsCard(report) }
        item { SectionHeader("By Strategy") }
        if (report.byStrategy.isEmpty()) {
            item { EmptyNote("No positions closed by any strategy in this period.") }
        } else {
            items(report.byStrategy, key = { it.strategyName }) { StrategyRow(it) }
        }
        item { SectionHeader("By Deployment") }
        if (report.byDeployment.isEmpty()) {
            item { EmptyNote("No deployment closed a position in this period.") }
        } else {
            items(report.byDeployment, key = { it.deploymentId }) { DeploymentBreakdownRow(it) }
        }
    }
}

@Composable
private fun StatsCard(report: PnlReport) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Column {
                Text(
                    "REALIZED P&L",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    formatSignedMoney(report.realizedPnl),
                    style = MaterialTheme.typography.headlineMedium,
                    color = pnlColor(report.realizedPnl),
                )
                // prevRealizedPnl is null only for period="all" -- see
                // ReportsViewModel's own docstring and the backend's
                // pnl_report endpoint (there's no "previous all-time
                // period" to diff against).
                val prev = report.prevRealizedPnl
                if (prev != null) {
                    val delta = report.realizedPnl - prev
                    Text(
                        "${if (delta >= 0) "▲" else "▼"} ${formatSignedMoney(delta)} vs previous period",
                        style = MaterialTheme.typography.bodySmall,
                        color = pnlColor(delta),
                    )
                } else {
                    Text(
                        "Every realized rupee since the first trade",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            HorizontalDivider()
            Row(horizontalArrangement = Arrangement.spacedBy(24.dp)) {
                StatColumn("Positions closed", report.positionsClosed.toString())
                StatColumn("Wins", report.wins.toString())
                StatColumn("Losses", report.losses.toString())
                StatColumn("Fills", report.fills.toString())
            }
        }
    }
}

@Composable
private fun StatColumn(label: String, value: String) {
    Column {
        Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.titleMedium)
    }
}

@Composable
private fun SectionHeader(title: String) {
    Text(
        title,
        style = MaterialTheme.typography.titleMedium,
        modifier = Modifier.padding(top = 4.dp),
    )
}

@Composable
private fun EmptyNote(text: String) {
    Text(text, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

@Composable
private fun StrategyRow(row: PnlStrategyBreakdown) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(14.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Column {
                Text(row.strategyName, style = MaterialTheme.typography.bodyLarge)
                Text(
                    "${row.positionsClosed} position(s) closed",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Text(
                formatSignedMoney(row.realizedPnl),
                style = MaterialTheme.typography.titleMedium,
                color = pnlColor(row.realizedPnl),
            )
        }
    }
}

@Composable
private fun DeploymentBreakdownRow(row: PnlDeploymentBreakdown) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(14.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Column {
                Text(row.deploymentName, style = MaterialTheme.typography.bodyLarge)
                Text(
                    "${row.strategyName} · ${row.positionsClosed} position(s) closed",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Text(
                formatSignedMoney(row.realizedPnl),
                style = MaterialTheme.typography.titleMedium,
                color = pnlColor(row.realizedPnl),
            )
        }
    }
}
