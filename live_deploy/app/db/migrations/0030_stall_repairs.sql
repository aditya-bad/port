-- Repairs applied over a stretch of the session the app missed (a Kite
-- tick stall): what was replayed, the paths chosen where 1-minute candles
-- couldn't tell, a summary, and everything needed to undo it (the rows it
-- added, the open positions, cash and strategy state from before). One
-- live repair per deployment and day. See app/repair/engine.py.
CREATE TABLE IF NOT EXISTS stall_repairs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deployment_id UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    day           DATE NOT NULL,
    window_from   TIMESTAMPTZ NOT NULL,
    window_to     TIMESTAMPTZ NOT NULL,
    choices       JSONB NOT NULL DEFAULT '{}',
    summary       JSONB NOT NULL DEFAULT '{}',
    undo          JSONB NOT NULL,
    applied_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    undone_at     TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS one_live_stall_repair ON stall_repairs(deployment_id, day) WHERE undone_at IS NULL;
