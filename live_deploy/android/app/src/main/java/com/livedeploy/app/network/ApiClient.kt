package com.livedeploy.app.network

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import java.util.concurrent.TimeUnit

/**
 * Builds a fresh ApiService bound to whatever server URL + API key the
 * user last saved in Setup (see SettingsStore) — there is deliberately no
 * app-wide singleton Retrofit instance here, unlike a typical single-
 * backend app, because THIS app's backend address is itself user-supplied
 * and can change (a redeploy to a new tailnet host, switching between a
 * home box and a VPS, ...): rebuilding on every settings change is
 * simpler and safer than trying to mutate a live OkHttpClient's base URL
 * in place.
 */
object ApiClient {
    private val json = Json {
        ignoreUnknownKeys = true   // this app's models are a deliberate
                                    // SUBSET of the backend's real response
                                    // shape (see ApiModels.kt) — every field
                                    // not declared there must be silently
                                    // dropped, not a hard decode failure.
        isLenient = true
    }

    fun build(baseUrl: String, apiKey: String): ApiService {
        val authInterceptor = okhttp3.Interceptor { chain ->
            val request = chain.request().newBuilder()
                .header("X-API-Key", apiKey)
                .build()
            chain.proceed(request)
        }

        val logging = HttpLoggingInterceptor().apply {
            // BASIC, not BODY — this touches account/position data; a
            // request/response line (method + URL + status + timing) is
            // useful for debugging connectivity without echoing trade
            // details into Logcat.
            level = HttpLoggingInterceptor.Level.BASIC
        }

        val client = OkHttpClient.Builder()
            .addInterceptor(authInterceptor)
            .addInterceptor(logging)
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
            .build()

        val contentType = "application/json".toMediaType()
        return Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(client)
            .addConverterFactory(json.asConverterFactory(contentType))
            .build()
            .create(ApiService::class.java)
    }
}

/** StatusField.value rides in as a JsonElement (see ApiModels.kt's own
 * comment on why) — this turns it into whatever plain text the web UI
 * would have shown, without re-implementing a typed decode for a field
 * that's pure display text everywhere it's used. */
fun JsonElement.toDisplayString(): String =
    if (this is JsonPrimitive && this.isString) this.content else this.toString().trim('"')
