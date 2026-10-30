-- The prices a strategy took from Kite's live bid/ask for a leg whose last
-- trade wasn't recent (strangle_monthly_v2's LIVE PRICES: no trade yet
-- today, or none in the last few minutes), one row each time it asked.
-- The end-of-day check and the day repair have Kite's 1-minute candles
-- but no order book from the day: they price those legs at these
-- (app/repair/engine.py _honour_prices), as the app did.
CREATE TABLE IF NOT EXISTS strategy_quotes (
    deployment_id     UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    at                TIMESTAMPTZ NOT NULL,
    instrument_token  BIGINT NOT NULL,
    price             NUMERIC(18,4) NOT NULL,
    PRIMARY KEY (deployment_id, instrument_token, at)
);
