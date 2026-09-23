package com.livedeploy.app.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore(name = "live_deploy_settings")

data class ConnectionSettings(val serverUrl: String, val apiKey: String) {
    /** Guaranteed to end in exactly one "/" — Retrofit's Retrofit.Builder
     * requires this of baseUrl(), and every screen that builds a relative
     * request path assumes it too. Normalized here, once, rather than at
     * every call site. */
    val normalizedBaseUrl: String
        get() = if (serverUrl.endsWith("/")) serverUrl else "$serverUrl/"
}

/**
 * Where the server URL + API key (live_deploy's existing X-API-Key
 * scripted-access secret — see ApiService's own docstring for why no new
 * backend auth was needed) live on-device. Plain (unencrypted) DataStore,
 * not EncryptedSharedPreferences — see app/build.gradle.kts' own comment
 * on that dependency for why: this app's private storage directory is
 * already inaccessible to every other app on a normal, non-rooted device,
 * the same practical boundary the security library's extra encryption
 * layer would add on top of, and Android's EncryptedSharedPreferences /
 * Jetpack Security library has a documented history of MasterKey
 * initialization failures across OEM Keystore implementations — not worth
 * that fragility for a secret that's equivalent in sensitivity to a
 * bearer token any other locally-installed app in the same threat model
 * could extract from an encrypted store just as easily via a rooted
 * debugger attach.
 */
class SettingsStore(private val context: Context) {
    private object Keys {
        val SERVER_URL = stringPreferencesKey("server_url")
        val API_KEY = stringPreferencesKey("api_key")
    }

    val settings: Flow<ConnectionSettings?> = context.dataStore.data.map { prefs ->
        val url = prefs[Keys.SERVER_URL]
        val key = prefs[Keys.API_KEY]
        if (url.isNullOrBlank() || key.isNullOrBlank()) null else ConnectionSettings(url, key)
    }

    suspend fun save(serverUrl: String, apiKey: String) {
        context.dataStore.edit { prefs ->
            prefs[Keys.SERVER_URL] = serverUrl.trim()
            prefs[Keys.API_KEY] = apiKey.trim()
        }
    }

    suspend fun clear() {
        context.dataStore.edit { it.clear() }
    }
}
