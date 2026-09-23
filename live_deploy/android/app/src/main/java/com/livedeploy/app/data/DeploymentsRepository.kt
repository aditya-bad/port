package com.livedeploy.app.data

import com.livedeploy.app.network.ActionResult
import com.livedeploy.app.network.ApiClient
import com.livedeploy.app.network.Deployment
import kotlinx.coroutines.flow.first

sealed class ApiResult<out T> {
    data class Success<T>(val data: T) : ApiResult<T>()
    data class Failure(val message: String) : ApiResult<Nothing>()
}

/**
 * Thin wrapper over ApiService that (a) resolves the current server URL +
 * API key from SettingsStore on every call — rebuilding a fresh
 * Retrofit/OkHttp client each time is cheap relative to this app's actual
 * call volume (a personal dashboard polled every few seconds at most, not
 * a hot path), and correctness (always using whatever was most recently
 * saved in Setup) matters more here than pooling a long-lived client —
 * and (b) turns every network/HTTP failure into ApiResult.Failure with a
 * message a screen can show directly, so no ViewModel needs its own
 * try/catch around a raw Retrofit call.
 */
class DeploymentsRepository(private val settingsStore: SettingsStore) {

    private suspend fun currentSettings(): ConnectionSettings =
        settingsStore.settings.first()
            ?: throw IllegalStateException("Not configured — open Settings and enter your server URL + API key.")

    private suspend fun <T> call(block: suspend (com.livedeploy.app.network.ApiService) -> retrofit2.Response<T>): ApiResult<T> {
        return try {
            val settings = currentSettings()
            val api = ApiClient.build(settings.normalizedBaseUrl, settings.apiKey)
            val response = block(api)
            if (response.isSuccessful) {
                val body = response.body()
                // Every endpoint this app calls returns a real JSON body on
                // success (a Deployment, a List<Deployment>, or an
                // ActionResult — see ApiService) — a null body here means
                // something unexpected happened server-side, not a
                // legitimate empty-success case to paper over with an
                // unchecked cast.
                if (body != null) ApiResult.Success(body)
                else ApiResult.Failure("Server returned an empty response")
            } else {
                ApiResult.Failure("Server returned ${response.code()}: ${response.message()}")
            }
        } catch (e: IllegalStateException) {
            ApiResult.Failure(e.message ?: "Not configured")
        } catch (e: java.io.IOException) {
            ApiResult.Failure("Couldn't reach the server — check the URL and that it's running (${e.message})")
        } catch (e: Exception) {
            ApiResult.Failure(e.message ?: "Unknown error")
        }
    }

    suspend fun checkConnection(baseUrl: String, apiKey: String): ApiResult<String> {
        return try {
            val api = ApiClient.build(if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/", apiKey)
            val response = api.health()
            val body = response.body()
            if (response.isSuccessful && body != null) {
                if (body.databaseConnected) ApiResult.Success("Connected — server status: ${body.status}")
                else ApiResult.Failure("Reached the server, but its own database connection is down (status: ${body.status}). Deployments won't load until that's fixed.")
            } else if (response.code() == 401 || response.code() == 403) {
                ApiResult.Failure("Reached the server, but the API key was rejected. Double-check it against config.json's app_auth_secret.")
            } else {
                ApiResult.Failure("Server returned ${response.code()} — check the URL.")
            }
        } catch (e: java.io.IOException) {
            ApiResult.Failure("Couldn't reach that address (${e.message}). Check the URL and that the server is running.")
        } catch (e: Exception) {
            ApiResult.Failure(e.message ?: "Unknown error")
        }
    }

    suspend fun listDeployments(): ApiResult<List<Deployment>> = call { it.listDeployments() }

    suspend fun getDeployment(id: String): ApiResult<Deployment> = call { it.getDeployment(id) }

    suspend fun pause(id: String): ApiResult<ActionResult> = call { it.pauseDeployment(id) }

    suspend fun resume(id: String): ApiResult<Deployment> = call { it.resumeDeployment(id) }

    suspend fun stop(id: String, forceClose: Boolean = false): ApiResult<ActionResult> =
        call { it.stopDeployment(id, forceClose) }

    suspend fun flatten(id: String): ApiResult<ActionResult> = call { it.flattenDeployment(id) }
}
