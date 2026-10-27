-- LinckLens prices (app/lens_store.py).
--
-- lens_daily: one daily candle per instrument per day, for the shares
-- held (and held before, from imported trades) and NIFTY 50. Read from
-- Kite once, then topped up at the 17:00 sync.
CREATE TABLE IF NOT EXISTS lens_daily (
    instrument_token BIGINT NOT NULL,
    day DATE NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume BIGINT,
    PRIMARY KEY (instrument_token, day)
);

-- What each kept instrument is, and the last day Kite has been asked for.
CREATE TABLE IF NOT EXISTS lens_instruments (
    instrument_token BIGINT PRIMARY KEY,
    tradingsymbol TEXT NOT NULL,
    exchange TEXT,
    fetched_through DATE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
