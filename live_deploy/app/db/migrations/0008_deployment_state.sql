-- Generic per-deployment live-state dump. Any strategy can persist
-- whatever JSON-serializable internal state it wants to survive a
-- restart (e.g. pivot_supertrend's live-learned pivots/SuperTrend
-- internals, which live only in Python memory otherwise and would
-- silently revert to a stale config-provided seed on every restart).
--
-- One row per deployment, overwritten WHOLESALE on each dump — this is
-- "my latest resumable snapshot," not an event log, so there's nothing
-- to replay or reconcile, just one JSON blob a strategy reads back
-- verbatim in on_start(). ON DELETE CASCADE so deleting a deployment
-- cleans this up the same way it does positions/lots/events.
CREATE TABLE IF NOT EXISTS deployment_state (
    deployment_id  UUID PRIMARY KEY REFERENCES deployments(id) ON DELETE CASCADE,
    state          JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
