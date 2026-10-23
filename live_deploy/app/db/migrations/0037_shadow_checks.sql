-- Shadow mode (app/shadow.py): for every paper fill, would Kite have
-- taken the order, and at what price? Checked after the fill, in the
-- background; nothing here ever changes a fill.
--
-- verdict:  fill      would fill in full at about the paper price
--           worse     would fill in full, `shortfall` rupees worse
--           partial   only `filled_qty` of `qty` would fill within 60 s
--           no_fill   the limit order wouldn't fill within 60 s
--           reject    Kite would refuse it (see checks)
--           unknown   couldn't check (no order book, no instrument data)
-- checks:   [{rule, status: ok|warn|reject|unknown, detail}] — every rule
--           that ran, so a page can say why.
-- followup: what the price did in the 60 s after a limit that didn't
--           fill at once (null when it filled at once).
-- estimated: true for checks made later from stored data (the backfill),
--           false for checks made as the fill happened.
CREATE TABLE IF NOT EXISTS shadow_checks (
    lot_id            UUID PRIMARY KEY REFERENCES position_lots(id) ON DELETE CASCADE,
    deployment_id     UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    executed_at       TIMESTAMPTZ NOT NULL,
    symbol            TEXT NOT NULL,
    instrument_token  BIGINT NOT NULL,
    action            TEXT NOT NULL CHECK (action IN ('buy', 'sell')),
    qty               NUMERIC(18,4) NOT NULL,
    paper_price       NUMERIC(18,4) NOT NULL,
    verdict           TEXT NOT NULL CHECK (verdict IN ('fill', 'worse', 'partial', 'no_fill', 'reject', 'unknown')),
    limit_price       NUMERIC(18,4),
    est_price         NUMERIC(18,4),
    filled_qty        NUMERIC(18,4),
    shortfall         NUMERIC(18,2),
    orders            INTEGER,
    checks            JSONB NOT NULL DEFAULT '[]'::jsonb,
    followup          JSONB,
    estimated         BOOLEAN NOT NULL DEFAULT false,
    checked_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_shadow_deployment_time ON shadow_checks(deployment_id, executed_at);
CREATE INDEX IF NOT EXISTS idx_shadow_verdict_time ON shadow_checks(verdict, executed_at);

-- The exchange's freeze limits (largest quantity one order may carry),
-- per underlying: the last good copy of the daily NSE file, kept so a
-- day the file can't be fetched still has limits. config.json's
-- shadow.freeze_qty overrides any of them.
CREATE TABLE IF NOT EXISTS freeze_limits (
    underlying  TEXT PRIMARY KEY,
    qty         INTEGER NOT NULL CHECK (qty > 0),
    source      TEXT NOT NULL,
    as_of       DATE NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
