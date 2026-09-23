package com.livedeploy.app.ui.deployments

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
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
import com.livedeploy.app.ui.formatSignedMoney
import com.livedeploy.app.ui.pnlColor

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DeploymentsScreen(
    factory: AppViewModelFactory,
    onOpenDeployment: (String) -> Unit,
) {
    val viewModel: DeploymentsViewModel = viewModel(factory = factory)
    val uiState by viewModel.uiState.collectAsState()
    val actionMessage by viewModel.actionMessage.collectAsState()
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(actionMessage) {
        actionMessage?.let {
            snackbarHostState.showSnackbar(it)
            viewModel.clearActionMessage()
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Deployed Strategies") },
                actions = {
                    IconButton(onClick = { viewModel.refresh() }) {
                        Icon(Icons.Default.Refresh, contentDescription = "Refresh")
                    }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbarHostState) },
    ) { padding ->
        when (val state = uiState) {
            is DeploymentsUiState.Loading -> Box(
                Modifier.fillMaxSize().padding(padding),
                contentAlignment = Alignment.Center,
            ) { CircularProgressIndicator() }

            is DeploymentsUiState.Error -> Box(
                Modifier.fillMaxSize().padding(padding).padding(24.dp),
                contentAlignment = Alignment.Center,
            ) { Text(state.message, color = MaterialTheme.colorScheme.error) }

            is DeploymentsUiState.Loaded -> {
                if (state.deployments.isEmpty()) {
                    Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                        Text("No deployments yet.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                } else {
                    LazyColumn(
                        modifier = Modifier.fillMaxSize().padding(padding),
                        contentPadding = androidx.compose.foundation.layout.PaddingValues(12.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        items(state.deployments, key = { it.id }) { deployment ->
                            DeploymentRow(
                                deployment = deployment,
                                onClick = { onOpenDeployment(deployment.id) },
                                onPause = { viewModel.pause(deployment.id) },
                                onResume = { viewModel.resume(deployment.id) },
                                onStop = { viewModel.stop(deployment.id, forceClose = false) },
                                onFlatten = { viewModel.flatten(deployment.id) },
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun DeploymentRow(
    deployment: Deployment,
    onClick: () -> Unit,
    onPause: () -> Unit,
    onResume: () -> Unit,
    onStop: () -> Unit,
    onFlatten: () -> Unit,
) {
    var menuOpen by remember { mutableStateOf(false) }

    Card(
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(16.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(deployment.deploymentName, style = MaterialTheme.typography.titleMedium)
                Text(
                    "${deployment.strategyName} · ${deployment.mode}",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Row(modifier = Modifier.padding(top = 4.dp)) {
                    StatusChip(deployment.status)
                    if (!deployment.strategyRegistered) {
                        Text(
                            " · unregistered",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.error,
                        )
                    }
                }
                // Step 108 (live_deploy) — same live "why isn't this
                // trading" hint the web Deployed Strategies table shows
                // under the status tag.
                deployment.statusFields.firstOrNull()?.let { field ->
                    Text(
                        field.value.toDisplayString(),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 2.dp),
                    )
                }
            }

            Column(horizontalAlignment = Alignment.End) {
                Text(
                    formatSignedMoney(deployment.totalPnl),
                    style = MaterialTheme.typography.titleMedium,
                    color = pnlColor(deployment.totalPnl),
                )
                Box {
                    IconButton(onClick = { menuOpen = true }) {
                        Icon(Icons.Default.MoreVert, contentDescription = "Actions")
                    }
                    DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                        when (deployment.status) {
                            "active" -> {
                                DropdownMenuItem(text = { Text("Pause") }, onClick = { menuOpen = false; onPause() })
                                DropdownMenuItem(text = { Text("Flatten") }, onClick = { menuOpen = false; onFlatten() })
                                DropdownMenuItem(text = { Text("Stop") }, onClick = { menuOpen = false; onStop() })
                            }
                            "paused" -> {
                                DropdownMenuItem(text = { Text("Resume") }, onClick = { menuOpen = false; onResume() })
                                DropdownMenuItem(text = { Text("Stop") }, onClick = { menuOpen = false; onStop() })
                            }
                            else -> {
                                DropdownMenuItem(text = { Text("No actions (stopped)") }, onClick = { menuOpen = false }, enabled = false)
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun StatusChip(status: String) {
    // "paused" reuses the theme's own `secondary` role — set to the
    // web app's brass/ochre "paused/adjust/warn" color in both light and
    // dark schemes (ui/theme/Theme.kt) — rather than reaching into
    // LightPalette directly, which would ignore dark mode.
    val color = when (status) {
        "active" -> MaterialTheme.colorScheme.primary
        "paused" -> MaterialTheme.colorScheme.secondary
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }
    Text(
        status.uppercase(),
        style = MaterialTheme.typography.labelSmall,
        color = color,
    )
}
