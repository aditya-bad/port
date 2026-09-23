package com.livedeploy.app.ui.setup

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.livedeploy.app.data.ApiResult
import com.livedeploy.app.data.DeploymentsRepository
import com.livedeploy.app.data.SettingsStore
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

sealed class ConnectionTestState {
    data object Idle : ConnectionTestState()
    data object Testing : ConnectionTestState()
    data class Success(val message: String) : ConnectionTestState()
    data class Failed(val message: String) : ConnectionTestState()
}

class SetupViewModel(
    private val repository: DeploymentsRepository,
    private val settingsStore: SettingsStore,
) : ViewModel() {

    private val _testState = MutableStateFlow<ConnectionTestState>(ConnectionTestState.Idle)
    val testState: StateFlow<ConnectionTestState> = _testState.asStateFlow()

    private val _saved = MutableStateFlow(false)
    val saved: StateFlow<Boolean> = _saved.asStateFlow()

    /** Tests the connection and, only if it succeeds, saves it — never
     * saves an address/key combination that hasn't actually been
     * confirmed to work, so Dashboard/Deployments never open onto a
     * silent "can't reach the server" wall right after Setup. */
    fun testAndSave(serverUrl: String, apiKey: String) {
        val trimmedUrl = serverUrl.trim()
        val trimmedKey = apiKey.trim()
        if (trimmedUrl.isBlank() || trimmedKey.isBlank()) {
            _testState.value = ConnectionTestState.Failed("Both the server URL and API key are required.")
            return
        }
        viewModelScope.launch {
            _testState.value = ConnectionTestState.Testing
            when (val result = repository.checkConnection(trimmedUrl, trimmedKey)) {
                is ApiResult.Success -> {
                    settingsStore.save(trimmedUrl, trimmedKey)
                    _testState.value = ConnectionTestState.Success(result.data)
                    _saved.value = true
                }
                is ApiResult.Failure -> {
                    _testState.value = ConnectionTestState.Failed(result.message)
                }
            }
        }
    }
}
