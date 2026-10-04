package com.livedeploy.app.network

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Mirrors app/deployments/schemas.py's DeploymentOut (live_deploy, the
 * Python backend this app talks to) — field-for-field, same names, since
 * the backend already returns plain JSON with these exact keys. Only the
 * fields this app's screens actually use are declared here; kotlinx.
 * serialization rejects unknown JSON keys by DEFAULT, so ApiClient's own
 * Json{} config explicitly turns ignoreUnknownKeys on — that's what lets
 * this model stay a deliberate SUBSET of the real response, rather than
 * needing a matching Kotlin property for every backend field.
 */
@Serializable
data class Deployment(
    val id: String,
    @SerialName("deployment_name") val deploymentName: String,
    @SerialName("strategy_name") val strategyName: String,
    val mode: String,               // "intraday" | "positional"
    val status: String,             // "active" | "paused" | "stopped"
    @SerialName("initial_capital") val initialCapital: Double,
    @SerialName("current_cash") val currentCash: Double,
    @SerialName("realized_pnl") val realizedPnl: Double = 0.0,
    @SerialName("unrealized_pnl") val unrealizedPnl: Double = 0.0,
    @SerialName("include_in_reports") val includeInReports: Boolean = true,
    val tags: List<String> = emptyList(),
    @SerialName("strategy_registered") val strategyRegistered: Boolean = true,
    // Step 108 (live_deploy) — the same live status hint the Deployed
    // Strategies web table shows under the status tag, e.g. a flat
    // strangle_monthly_v2's "Waiting for entry time (10:00 IST)". Empty
    // for the vast majority of strategies that don't override
    // get_status_fields() — see that method's own docstring.
    @SerialName("status_fields") val statusFields: List<StatusField> = emptyList(),
) {
    val totalPnl: Double get() = realizedPnl + unrealizedPnl
}

@Serializable
data class StatusField(
    val label: String,
    // The backend's own StatusField.value is `Any` (a string, a number, a
    // date-as-string, ...) — kotlinx.serialization has no direct `Any`
    // decode, so this rides in as JsonElement and each screen renders it
    // via the JsonElement.toDisplayString() extension (ApiClient.kt) — a
    // deliberate, minimal choice over hand-writing a custom
    // KSerializer<Any> for a field that's purely display text everywhere
    // it's used, never parsed back into a typed value on this side.
    val value: kotlinx.serialization.json.JsonElement,
)

/** GET /health's real shape (app/routers/health.py) — status/
 * database_connected/running_deployments plus the dispatcher's own
 * spread-in fields (dispatcher.status, see app/dispatcher.py) merged
 * straight into the same JSON object. kite_connected/needs_login back
 * the Dashboard's own Kite-status chip (only the fields this app
 * actually shows are declared; ignoreUnknownKeys drops the rest, see
 * ApiClient's Json{} config). */
@Serializable
data class HealthOut(
    val status: String = "unknown",
    @SerialName("database_connected") val databaseConnected: Boolean = false,
    @SerialName("kite_connected") val kiteConnected: Boolean = false,
    @SerialName("needs_login") val needsLogin: Boolean = false,
)

/** POST /deployments/{id}/pause|stop|flatten's own real response shape
 * (routers/deployments.py) — {"status": "paused"} etc. Modeled explicitly
 * rather than requested as Response<Unit>: with a JSON converter factory,
 * Retrofit still runs the body through it for a Unit-typed response and a
 * real (non-empty) JSON body there is a decode error waiting to happen,
 * not a documented no-op the way Response<Void> is. */
@Serializable
data class ActionResult(val status: String)

/** POST /deployments/{id}/remove-trades' real response shape (routers/
 * deployments.py's remove_trades) — {"positions_removed": int,
 * "cash_delta": float}. See DeploymentManager.remove_trades (the Python
 * backend) for exactly what this deletes/reverses — this model only
 * needs to carry enough back to confirm it to the user. */
@Serializable
data class RemoveTradesResult(
    @SerialName("positions_removed") val positionsRemoved: Int,
    @SerialName("cash_delta") val cashDelta: Double,
)

/** GET/POST /notifications/mute-status|mute-today|unmute's shared
 * response shape (routers/notifications.py) — the "Mark as holiday"
 * mute for Kite-disconnected alerts. mutedUntil is an ISO-8601 UTC
 * instant (7:00 IST the day after mute-today was called), or null when
 * not muted — rendered as-is via Formatting.kt's formatIsoInstant
 * rather than parsed into a typed date, same "pure display text"
 * reasoning as StatusField.value above. */
@Serializable
data class MuteStatus(
    val muted: Boolean,
    @SerialName("muted_until") val mutedUntil: String? = null,
)

/** GET /portfolio/pnl-report's real shape (routers/aggregate.py's
 * pnl_report) — mirrors PnlReportOut field-for-field. prevRealizedPnl is
 * null only for period="all" (there's no "previous all-time period" to
 * diff against — see that endpoint's own docstring). */
@Serializable
data class PnlReport(
    val period: String,
    val offset: Int,
    val label: String,
    @SerialName("realized_pnl") val realizedPnl: Double,
    @SerialName("positions_closed") val positionsClosed: Int,
    val wins: Int,
    val losses: Int,
    val fills: Int,
    @SerialName("prev_realized_pnl") val prevRealizedPnl: Double? = null,
    @SerialName("by_strategy") val byStrategy: List<PnlStrategyBreakdown> = emptyList(),
    @SerialName("by_deployment") val byDeployment: List<PnlDeploymentBreakdown> = emptyList(),
)

/** Mirrors PnlStrategyBreakdown (schemas.py) — one strategy's realized
 * P&L within the selected Reports period. */
@Serializable
data class PnlStrategyBreakdown(
    @SerialName("strategy_name") val strategyName: String,
    @SerialName("realized_pnl") val realizedPnl: Double,
    @SerialName("positions_closed") val positionsClosed: Int,
)

/** Mirrors PnlDeploymentBreakdown (schemas.py) — one deployment's
 * realized P&L within the selected Reports period. */
@Serializable
data class PnlDeploymentBreakdown(
    @SerialName("deployment_id") val deploymentId: String,
    @SerialName("deployment_name") val deploymentName: String,
    @SerialName("strategy_name") val strategyName: String,
    @SerialName("realized_pnl") val realizedPnl: Double,
    @SerialName("positions_closed") val positionsClosed: Int,
)
