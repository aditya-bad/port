package com.livedeploy.app.network

import retrofit2.Response
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

/**
 * The live_deploy backend's REST surface, scoped to exactly what this
 * app's first screens use (Dashboard KPIs, Deployments list, and the
 * pause/resume/stop/flatten actions) — see this project's own README for
 * what's deliberately NOT ported yet (Reports, Analytics charts, Catalog,
 * Compare, ...). Auth is NOT declared per-call here — ApiClient's own
 * OkHttp interceptor attaches the X-API-Key header to every request,
 * reusing the backend's existing "scripted/API access" auth path
 * (app/auth.py's AuthMiddleware._header_api_key_ok) rather than a new
 * per-user token scheme, so zero backend changes were needed to stand
 * this app up at all.
 */
interface ApiService {
    @GET("health")
    suspend fun health(): Response<HealthOut>

    @GET("deployments")
    suspend fun listDeployments(): Response<List<Deployment>>

    @GET("deployments/{id}")
    suspend fun getDeployment(@Path("id") id: String): Response<Deployment>

    @POST("deployments/{id}/pause")
    suspend fun pauseDeployment(@Path("id") id: String): Response<ActionResult>

    @POST("deployments/{id}/resume")
    suspend fun resumeDeployment(@Path("id") id: String): Response<Deployment>

    @POST("deployments/{id}/stop")
    suspend fun stopDeployment(
        @Path("id") id: String,
        @Query("force_close") forceClose: Boolean = false,
    ): Response<ActionResult>

    @POST("deployments/{id}/flatten")
    suspend fun flattenDeployment(@Path("id") id: String): Response<ActionResult>
}
