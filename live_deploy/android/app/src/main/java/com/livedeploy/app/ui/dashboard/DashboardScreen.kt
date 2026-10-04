package com.livedeploy.app.ui.dashboard

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.weight
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.livedeploy.app.ui.AppViewModelFactory
import com.livedeploy.app.ui.formatIsoInstantIst
import com.livedeploy.app.ui.formatSignedMoney
import com.livedeploy.app.ui.pnlColor
import com.livedeploy.app.ui.theme.PnlColors

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DashboardScreen(factory: AppViewModelFactory) {
    val viewModel: DashboardViewModel = viewModel(factory = factory)
    val uiState by viewModel.uiState.collectAsState()
    val kiteStatus by viewModel.kiteStatus.collectAsState()
    var showKiteDialog by remember { mutableStateOf(false) }

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
        Column(modifier = Modifier.fillMaxSize().padding(padding)) {
            KiteStatusRow(status = kiteStatus, onClick = { showKiteDialog = true })
            Box(Modifier.weight(1f).fillMaxWidth()) {
                when (val state = uiState) {
                    is DashboardUiState.Loading -> Box(
                        Modifier.fillMaxSize(),
                        contentAlignment = Alignment.Center,
                    ) { CircularProgressIndicator() }

                    is DashboardUiState.Error -> Box(
                        Modifier.fillMaxSize().padding(24.dp),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(state.message, color = MaterialTheme.colorScheme.error)
                    }

                    is DashboardUiState.Loaded -> DashboardKpiGrid(state.kpis)
                }
            }
        }
    }

    if (showKiteDialog) {
        KiteStatusDialog(
            status = kiteStatus,
            onMuteToday = { viewModel.muteToday() },
            onUnmute = { viewModel.unmute() },
            onDismiss = { showKiteDialog = false },
        )
    }
}

// "Trading control" chip, same purpose as the web app's own topbar Kite-
// status button (UIKit.ensureTopbar/openKitePopover) — tap it to see the
// full status + the "Mark as holiday" mute. Shown regardless of whether
// the KPI grid itself loaded (see DashboardViewModel.refreshKiteStatus'
// own "independent concern" comment) — a null status (health check
// hasn't resolved yet, or failed) reads as a neutral "Checking…" rather
// than blocking this row or erroring it out.
@Composable
private fun KiteStatusRow(status: DashboardKiteStatus?, onClick: () -> Unit) {
    val connected = status?.connected == true
    val dotColor = when {
        status == null -> MaterialTheme.colorScheme.onSurfaceVariant
        connected -> PnlColors.gain
        else -> PnlColors.loss
    }
    val label = when {
        status == null -> "Checking Kite…"
        connected -> "Kite connected"
        status.needsLogin -> "Kite — login required"
        else -> "Kite disconnected"
    }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Box(
                modifier = Modifier
                    .size(10.dp)
                    .clip(CircleShape)
                    .background(dotColor),
            )
            Text(label, style = MaterialTheme.typography.labelLarge)
            if (status?.muted == true) {
                Text(
                    "🔇 muted",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        TextButton(onClick = onClick) { Text("Details") }
    }
}

@Composable
private fun KiteStatusDialog(
    status: DashboardKiteStatus?,
    onMuteToday: () -> Unit,
    onUnmute: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Kite connection") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                val statusText = when {
                    status == null -> "Status unavailable."
                    status.connected -> "Kite connected."
                    status.needsLogin -> "Not connected — login required. Log in via the web dashboard."
                    else -> "Disconnected — reconnecting…"
                }
                Text(statusText)
                // "Mark as holiday" / "Unmute" — same mute this session's
                // own LiveDataDispatcher reminder (every 60s while down)
                // and the web app's own status-bar control read/write.
                // No "Re-login with Kite" action here (unlike the web
                // popover) — Kite's own OAuth login flow only exists in
                // the web dashboard; this app is a status/control
                // surface, not where that flow runs.
                if (status?.muted == true) {
                    Text(
                        "🔇 Alerts muted until ${status.mutedUntil?.let { formatIsoInstantIst(it) } ?: "—"}",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        },
        confirmButton = {
            if (status?.muted == true) {
                TextButton(onClick = { onUnmute(); onDismiss() }) { Text("Unmute") }
            } else {
                TextButton(onClick = { onMuteToday(); onDismiss() }) { Text("Mark as holiday") }
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Close") }
        },
    )
}

@Composable
private fun DashboardKpiGrid(kpis: DashboardKpis) {
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
        modifier = Modifier.fillMaxSize(),
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
