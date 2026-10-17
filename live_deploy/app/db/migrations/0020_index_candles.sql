-- 1-minute OHLC candles for the indices (and every deployment's
-- underlying), built from the live ticks the app already receives
-- (DeploymentManager.index_candles_loop). The Cycle / Session charts
-- read these first and only fall back to Kite's historical API when a
-- window isn't fully covered — so charts load without a Kite login and
-- stay the same over time.
CREATE TABLE IF NOT EXISTS index_candles (
    instrument_token BIGINT NOT NULL,
    minute           TIMESTAMPTZ NOT NULL,
    open             NUMERIC(18,4) NOT NULL,
    high             NUMERIC(18,4) NOT NULL,
    low              NUMERIC(18,4) NOT NULL,
    close            NUMERIC(18,4) NOT NULL,
    PRIMARY KEY (instrument_token, minute)
);
