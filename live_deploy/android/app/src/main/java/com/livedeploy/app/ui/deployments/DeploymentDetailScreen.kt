package com.livedeploy.app.ui.deployments

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.livedeploy.app.network.Deployment
import com.livedeploy.app.network.toDisplayString
import com.livedeploy.app.ui.AppViewModelFactory
import com.livedeploy.app.ui.formatMoney
import com.livedeploy.app.ui.formatSignedMoney
import com.livedeploy.app.ui.pnlColor

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DeploymentDetailScreen(
    factory: AppViewModelFactory,
    deploymentId: String,
    onBack: () -> Unit,
) {
    val viewModel: DeploymentDetailViewModel = viewModel(factory = factory)
    val uiState by viewModel.uiState.collectAsState()
    val actionMessage by viewModel.actionMessage.collectAsState()
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(deploymentId) { viewModel.load(deploymentId) }
    LaunchedEffect(actionMessage) {
        actionMessage?.let {
            snackbarHostState.showSnackbar(it)
            viewModel.clearActionMessage()
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Deployment") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbarHostState) },
    ) { padding ->
        when (val state = uiState) {
            is DeploymentDetailUiState.Loading -> Box(
                Modifier.fillMaxSize().padding(padding),
                contentAlignment = Alignment.Center,
            ) { CircularProgressIndicator() }

            is DeploymentDetailUiState.Error -> Box(
                Modifier.fillMaxSize().padding(padding).padding(24.dp),
                contentAlignment = Alignment.Center,
            ) { Text(state.message, color = MaterialTheme.colorScheme.error) }

            is DeploymentDetailUiState.Loaded -> DeploymentDetailBody(
                deployment = state.deployment,
                padding = padding,
                onPause = viewModel::pause,
                onResume = viewModel::resume,
                onStop = { viewModel.stop(forceClose = false) },
                onFlatten = viewModel::flatten,
                onRemoveTrades = viewModel::removeTrades,
            )
        }
    }
}

@Composable
private fun DeploymentDetailBody(
    deployment: Deployment,
    padding: androidx.compose.foundation.layout.PaddingValues,
    onPause: () -> Unit,
    onResume: () -> Unit,
    onStop: () -> Unit,
    onFlatten: () -> Unit,
    onRemoveTrades: () -> Unit,
) {
    // Irreversible (see manager.remove_trades on the backend), same
    // "confirm before firing" requirement the web app's own
    // window.confirm() enforces for this action — a plain button tap
    // with no confirmation would be too easy to hit by accident next to
    // Pause/Flatten/Stop.
    var showRemoveTradesConfirm by remember { mutableStateOf(false) }
    if (showRemoveTradesConfirm) {
        AlertDialog(
            onDismissRequest = { showRemoveTradesConfirm = false },
            title = { Text("Remove trades?") },
            text = {
                Text(
                    "Permanently DELETE the current trade for \"${deployment.deploymentName}\".\n\n" +
                        "This is not a close — every position in it (including already-closed legs of " +
                        "the same cycle, for a strategy that rolls) is wiped from the database entirely: " +
                        "cash is reversed as if it never happened, and it will no longer appear anywhere " +
                        "in Reports or History.\n\nThis cannot be undone.",
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    showRemoveTradesConfirm = false
                    onRemoveTrades()
                }) { Text("Remove trades", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = {
                TextButton(onClick = { showRemoveTradesConfirm = false }) { Text("Cancel") }
            },
        )
    }
    Column(
        modifier = Modifier.fillMaxSize().padding(padding).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(deployment.deploymentName, style = MaterialTheme.typography.headlineSmall)
        Text(
            "${deployment.strategyName} · ${deployment.mode} · ${deployment.status}",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
        ) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                DetailRow("Total P&L", formatSignedMoney(deployment.totalPnl), pnlColor(deployment.totalPnl))
                DetailRow("Realized", formatSignedMoney(deployment.realizedPnl), pnlColor(deployment.realizedPnl))
                DetailRow("Unrealized", formatSignedMoney(deployment.unrealizedPnl), pnlColor(deployment.unrealizedPnl))
                DetailRow("Initial capital", formatMoney(deployment.initialCapital), null)
                DetailRow("Current cash", formatMoney(deployment.currentCash), null)
            }
        }

        if (deployment.statusFields.isNotEmpty()) {
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("Live strategy state", style = MaterialTheme.typography.titleMedium)
                    deployment.statusFields.forEach { field ->
                        DetailRow(field.label, field.value.toDisplayString(), null)
                    }
                }
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            when (deployment.status) {
                "active" -> {
                    OutlinedButton(onClick = onPause) { Text("Pause") }
                    OutlinedButton(onClick = onFlatten) { Text("Flatten") }
                    Button(onClick = onStop) { Text("Stop") }
                }
                "paused" -> {
                    Button(onClick = onResume) { Text("Resume") }
                    OutlinedButton(onClick = onStop) { Text("Stop") }
                }
                else -> Text("Stopped — no actions available.", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        // Flatten's destructive sibling (see manager.remove_trades) —
        // own row, not crowded in with Pause/Flatten/Stop above, same
        // "set apart from the routine lifecycle actions" reasoning the
        // web app's own separator before this button in its "···More"
        // menu already follows. Available whenever the deployment isn't
        // stopped, same condition as Flatten.
        if (deployment.status != "stopped") {
            OutlinedButton(
                onClick = { showRemoveTradesConfirm = true },
                colors = androidx.compose.material3.ButtonDefaults.outlinedButtonColors(
                    contentColor = MaterialTheme.colorScheme.error,
                ),
            ) { Text("Remove trades") }
        }
    }
}

@Composable
private fun DetailRow(label: String, value: String, valueColor: androidx.compose.ui.graphics.Color?) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(label, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, color = valueColor ?: MaterialTheme.colorScheme.onSurface)
    }
}
