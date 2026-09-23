package com.livedeploy.app.ui.deployments

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.livedeploy.app.data.ApiResult
import com.livedeploy.app.data.DeploymentsRepository
import com.livedeploy.app.network.Deployment
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

sealed class DeploymentsUiState {
    data object Loading : DeploymentsUiState()
    data class Loaded(val deployments: List<Deployment>) : DeploymentsUiState()
    data class Error(val message: String) : DeploymentsUiState()
}

class DeploymentsViewModel(private val repository: DeploymentsRepository) : ViewModel() {

    private val _uiState = MutableStateFlow<DeploymentsUiState>(DeploymentsUiState.Loading)
    val uiState: StateFlow<DeploymentsUiState> = _uiState.asStateFlow()

    /** Per-deployment action feedback (e.g. "Pause failed: ..."), keyed by
     * deployment id — a Snackbar-shaped signal the screen consumes and
     * clears, not persisted UI state. */
    private val _actionMessage = MutableStateFlow<String?>(null)
    val actionMessage: StateFlow<String?> = _actionMessage.asStateFlow()

    init {
        refresh()
    }

    fun refresh() {
        viewModelScope.launch {
            _uiState.value = DeploymentsUiState.Loading
            load()
        }
    }

    private suspend fun load() {
        when (val result = repository.listDeployments()) {
            is ApiResult.Success -> _uiState.value = DeploymentsUiState.Loaded(
                result.data.sortedBy { it.deploymentName.lowercase() },
            )
            is ApiResult.Failure -> _uiState.value = DeploymentsUiState.Error(result.message)
        }
    }

    fun pause(id: String) = runAction { repository.pause(id) }
    fun resume(id: String) = runAction { repository.resume(id) }
    fun stop(id: String, forceClose: Boolean) = runAction { repository.stop(id, forceClose) }
    fun flatten(id: String) = runAction { repository.flatten(id) }

    private fun runAction(block: suspend () -> ApiResult<*>) {
        viewModelScope.launch {
            when (val result = block()) {
                is ApiResult.Success -> load()   // refresh the list so the new status/P&L shows immediately
                is ApiResult.Failure -> _actionMessage.value = result.message
            }
        }
    }

    fun clearActionMessage() {
        _actionMessage.value = null
    }
}
