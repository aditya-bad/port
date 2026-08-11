-- live_deploy schema — deployment lifecycle, positions, fills, reporting.
--
-- Design notes (see README for the full rationale):
--   - One strategy can have MULTIPLE deployments (strategy_name is just a
--     label). Every other table hangs off deployment_id, so deployments
--     never share positions, cash, or trade history — full isolation.
--   - Position/lot model mirrors backtest.py's Position/Lot classes
--     exactly: a same-direction fill ADDS a lot (averaging), an
--     opposite-direction fill must CLOSE THE ENTIRE position (no partial
--     exits, no same-fill reversal). Enforced in application code
--     (app/db/queries.py:record_fill), not in SQL.
--   - A partial unique index guarantees at most one OPEN position per
--     (deployment, instrument) at the database level — this is the
--     actual enforcement behind "deployments never overlap."

CREATE TABLE IF NOT EXISTS deployments (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deployment_name  TEXT NOT NULL UNIQUE,
    strategy_name    TEXT NOT NULL,
    mode             TEXT NOT NULL CHECK (mode IN ('intraday', 'positional')),
    status           TEXT NOT NULL CHECK (status IN ('active', 'paused', 'stopped')) DEFAULT 'active',
    initial_capital  NUMERIC(18,2) NOT NULL,
    current_cash     NUMERIC(18,2) NOT NULL CHECK (current_cash >= 0),
    config           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_deployments_status ON deployments(status);
CREATE INDEX IF NOT EXISTS idx_deployments_strategy ON deployments(strategy_name);

CREATE TABLE IF NOT EXISTS positions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deployment_id     UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    symbol            TEXT NOT NULL,
    instrument_token  BIGINT NOT NULL,
    side              TEXT NOT NULL CHECK (side IN ('long', 'short')),
    status            TEXT NOT NULL CHECK (status IN ('open', 'closed')) DEFAULT 'open',
    qty               NUMERIC(18,4) NOT NULL DEFAULT 0,
    avg_entry_price   NUMERIC(18,4) NOT NULL DEFAULT 0,
    realized_pnl      NUMERIC(18,2) NOT NULL DEFAULT 0,
    opened_at         TIMESTAMPTZ NOT NULL,
    closed_at         TIMESTAMPTZ,
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- At most one OPEN position per (deployment, instrument) — the DB-level
-- guarantee that a deployment's own positions never overlap themselves.
CREATE UNIQUE INDEX IF NOT EXISTS one_open_position_per_instrument
    ON positions (deployment_id, instrument_token)
    WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_positions_deployment ON positions(deployment_id);
CREATE INDEX IF NOT EXISTS idx_positions_deployment_status ON positions(deployment_id, status);

CREATE TABLE IF NOT EXISTS position_lots (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    position_id    UUID NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
    deployment_id  UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    action         TEXT NOT NULL CHECK (action IN ('buy', 'sell')),
    qty            NUMERIC(18,4) NOT NULL,
    price          NUMERIC(18,4) NOT NULL,
    executed_at    TIMESTAMPTZ NOT NULL,
    reason         TEXT,
    metadata       JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_lots_position ON position_lots(position_id);
CREATE INDEX IF NOT EXISTS idx_lots_deployment_time ON position_lots(deployment_id, executed_at);

CREATE TABLE IF NOT EXISTS deployment_events (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deployment_id  UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    event_type     TEXT NOT NULL,
    message        TEXT,
    metadata       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_events_deployment_time ON deployment_events(deployment_id, created_at);

CREATE TABLE IF NOT EXISTS deployment_snapshots (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deployment_id            UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    snapshot_at              TIMESTAMPTZ NOT NULL,
    cash                     NUMERIC(18,2) NOT NULL,
    open_positions_value     NUMERIC(18,2) NOT NULL,
    total_value              NUMERIC(18,2) NOT NULL,
    realized_pnl_cumulative  NUMERIC(18,2) NOT NULL,
    metadata                 JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_snapshots_deployment_time ON deployment_snapshots(deployment_id, snapshot_at);
