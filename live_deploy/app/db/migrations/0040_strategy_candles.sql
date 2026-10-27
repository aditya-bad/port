-- The candles a candle strategy (SuperTrend, pivots: one built with a
-- CandleAggregator) completed from its own ticks, as it fed them to its
-- signal (DeploymentRunner._keep_candles). The app stores no index
-- candles of its own, and Kite's minutes can close a 5-minute candle a
-- few points from where the app's last tick did: a SuperTrend flip can
-- hinge on that. The end-of-day check and the day repair replay the
-- strategy on these (app/repair/engine.py). start: the candle's start.
CREATE TABLE IF NOT EXISTS strategy_candles (
    deployment_id  UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    start          TIMESTAMPTZ NOT NULL,
    minutes        INTEGER NOT NULL,
    open           NUMERIC(18,4) NOT NULL,
    high           NUMERIC(18,4) NOT NULL,
    low            NUMERIC(18,4) NOT NULL,
    close          NUMERIC(18,4) NOT NULL,
    recorded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (deployment_id, start)
);
