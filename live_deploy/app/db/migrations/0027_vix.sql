-- India VIX, one row a minute (app/vix.py): the 30-day implied volatility
-- of Nifty options, for Geek mode's volatility views. From Kite's history
-- (backfilled once, then after each close) and from live ticks. Kite
-- keeps index history forever, but isn't meant for frequent polling, so
-- it's stored here once. Never read by a strategy.
CREATE TABLE IF NOT EXISTS vix_1m (
    minute  TIMESTAMPTZ PRIMARY KEY,
    open    NUMERIC(10,4) NOT NULL,
    high    NUMERIC(10,4) NOT NULL,
    low     NUMERIC(10,4) NOT NULL,
    close   NUMERIC(10,4) NOT NULL,
    source  TEXT NOT NULL DEFAULT 'kite'   -- 'kite' or 'ticks' (a tick-built minute never replaces Kite's)
);

-- Weekdays Kite had no VIX for (market holidays), so they're never asked
-- for again.
CREATE TABLE IF NOT EXISTS vix_no_data (
    day DATE PRIMARY KEY
);
