-- Checks a strategy made that did NOT trade: a daily check that found
-- nothing to do, a trigger refuted by live quotes, management held for
-- stale prices. Trades already explain themselves (each fill's metadata
-- carries its trigger and the values behind it); these rows fill in the
-- rest, for Geek mode's Decisions view. Read-only history, never used
-- to decide anything.
CREATE TABLE IF NOT EXISTS strategy_decisions (
    id            BIGSERIAL PRIMARY KEY,
    deployment_id UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    decided_at    TIMESTAMPTZ NOT NULL,
    rule          TEXT NOT NULL,
    outcome       TEXT NOT NULL CHECK (outcome IN ('no_action', 'blocked')),
    summary       TEXT NOT NULL,
    data          JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_strategy_decisions_dep_time ON strategy_decisions(deployment_id, decided_at DESC);
