package com.livedeploy.app.ui.setup

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.livedeploy.app.ui.AppViewModelFactory
import com.livedeploy.app.ui.theme.PnlColors

@Composable
fun SetupScreen(
    factory: AppViewModelFactory,
    onConnected: () -> Unit,
) {
    val viewModel: SetupViewModel = viewModel(factory = factory)
    val testState by viewModel.testState.collectAsState()
    val saved by viewModel.saved.collectAsState()

    var serverUrl by remember { mutableStateOf("") }
    var apiKey by remember { mutableStateOf("") }

    LaunchedEffect(saved) {
        if (saved) onConnected()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
    ) {
        Text("live_deploy", style = MaterialTheme.typography.headlineSmall)
        Text(
            "Connect to your paper-trading server",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        Spacer(Modifier.height(24.dp))

        OutlinedTextField(
            value = serverUrl,
            onValueChange = { serverUrl = it },
            label = { Text("Server URL") },
            placeholder = { Text("https://your-host.ts.net") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(12.dp))
        OutlinedTextField(
            value = apiKey,
            onValueChange = { apiKey = it },
            label = { Text("API key") },
            placeholder = { Text("config.json's app_auth_secret") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            modifier = Modifier.fillMaxWidth(),
        )
        Text(
            "This is the same value as \"app_auth_secret\" in the server's own " +
                "config.json (or the APP_AUTH_SECRET environment variable, if it's " +
                "set that way instead) — it's what live_deploy already uses for " +
                "scripted/API access, so no separate mobile login was needed.",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 4.dp),
        )

        Spacer(Modifier.height(20.dp))

        Button(
            onClick = { viewModel.testAndSave(serverUrl, apiKey) },
            enabled = testState !is ConnectionTestState.Testing,
            modifier = Modifier.fillMaxWidth(),
        ) {
            if (testState is ConnectionTestState.Testing) {
                CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
            } else {
                Text("Test & Connect")
            }
        }

        when (val state = testState) {
            is ConnectionTestState.Failed -> Text(
                state.message,
                color = PnlColors.loss,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(top = 12.dp),
            )
            is ConnectionTestState.Success -> Text(
                state.message,
                color = PnlColors.gain,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(top = 12.dp),
            )
            else -> {}
        }
    }
}
