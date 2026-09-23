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

sealed class DeploymentDetailUiState {
    data object Loading : DeploymentDetailUiState()
    data class Loaded(val deployment: Deployment) : DeploymentDetailUiState()
    data class Error(val message: String) : DeploymentDetailUiState()
}

class DeploymentDetailViewModel(private val repository: DeploymentsRepository) : ViewModel() {

    private val _uiState = MutableStateFlow<DeploymentDetailUiState>(DeploymentDetailUiState.Loading)
    val uiState: StateFlow<DeploymentDetailUiState> = _uiState.asStateFlow()

    private val _actionMessage = MutableStateFlow<String?>(null)
    val actionMessage: StateFlow<String?> = _actionMessage.asStateFlow()

    private var currentId: String? = null

    fun load(id: String) {
        currentId = id
        viewModelScope.launch {
            _uiState.value = DeploymentDetailUiState.Loading
            fetch(id)
        }
    }

    private suspend fun fetch(id: String) {
        when (val result = repository.getDeployment(id)) {
            is ApiResult.Success -> _uiState.value = DeploymentDetailUiState.Loaded(result.data)
            is ApiResult.Failure -> _uiState.value = DeploymentDetailUiState.Error(result.message)
        }
    }

    fun pause() = runAction { repository.pause(it) }
    fun resume() = runAction { repository.resume(it) }
    fun stop(forceClose: Boolean) = runAction { repository.stop(it, forceClose) }
    fun flatten() = runAction { repository.flatten(it) }

    private fun runAction(block: suspend (String) -> ApiResult<*>) {
        val id = currentId ?: return
        viewModelScope.launch {
            when (val result = block(id)) {
                is ApiResult.Success -> fetch(id)
                is ApiResult.Failure -> _actionMessage.value = result.message
            }
        }
    }

    fun clearActionMessage() {
        _actionMessage.value = null
    }
}
