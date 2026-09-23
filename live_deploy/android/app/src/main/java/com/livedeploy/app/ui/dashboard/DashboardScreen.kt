package com.livedeploy.app.ui.dashboard

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.livedeploy.app.ui.AppViewModelFactory
import com.livedeploy.app.ui.formatSignedMoney
import com.livedeploy.app.ui.pnlColor

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DashboardScreen(factory: AppViewModelFactory) {
    val viewModel: DashboardViewModel = viewModel(factory = factory)
    val uiState by viewModel.uiState.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Dashboard") },
                actions = {
                    IconButton(onClick = { viewModel.refresh() }) {
                        Icon(Icons.Default.Refresh, contentDescription = "Refresh")
                    }
                },
            )
        },
    ) { padding ->
        when (val state = uiState) {
            is DashboardUiState.Loading -> Box(
                Modifier.fillMaxSize().padding(padding),
                contentAlignment = Alignment.Center,
            ) { CircularProgressIndicator() }

            is DashboardUiState.Error -> Box(
                Modifier.fillMaxSize().padding(padding).padding(24.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(state.message, color = MaterialTheme.colorScheme.error)
            }

            is DashboardUiState.Loaded -> DashboardKpiGrid(state.kpis, padding)
        }
    }
}

@Composable
private fun DashboardKpiGrid(kpis: DashboardKpis, padding: PaddingValues) {
    val cards = listOf(
        KpiCardData("Total P&L", formatSignedMoney(kpis.totalPnl), pnlColor(kpis.totalPnl)),
        KpiCardData("Realized", formatSignedMoney(kpis.realizedPnl), pnlColor(kpis.realizedPnl)),
        KpiCardData("Unrealized (live)", formatSignedMoney(kpis.unrealizedPnl), pnlColor(kpis.unrealizedPnl)),
        KpiCardData("Active", kpis.activeCount.toString(), null),
        KpiCardData("Paused", kpis.pausedCount.toString(), null),
        KpiCardData("Stopped", kpis.stoppedCount.toString(), null),
    )
    LazyVerticalGrid(
        columns = GridCells.Fixed(2),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        modifier = Modifier.fillMaxSize().padding(padding),
    ) {
        items(cards) { card -> KpiCard(card) }
    }
}

private data class KpiCardData(
    val label: String,
    val value: String,
    val valueColor: androidx.compose.ui.graphics.Color?,
)

@Composable
private fun KpiCard(data: KpiCardData) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Column(Modifier.padding(16.dp)) {
            Text(
                data.label.uppercase(),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                data.value,
                style = MaterialTheme.typography.titleLarge,
                color = data.valueColor ?: MaterialTheme.colorScheme.onSurface,
            )
        }
    }
}
