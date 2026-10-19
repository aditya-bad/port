-- One 1-minute candle table for every instrument the app ever holds or
-- watches: indices AND option/futures contracts. Exactly one row per
-- (instrument, minute) — the primary key makes a duplicate impossible —
-- and every coarser interval (5/15/60-minute, day) is rolled up from
-- these when read (app/candles.py), never stored separately. The
-- existing index candles carry over as-is (a rename, no copy).
--
-- source: where the row came from, so better data can replace worse:
--   ticks  — built live from the app's own tick stream (may have gaps)
--   kite   — Kite's historical API (authoritative; replaces 'ticks')
--   import — a user backup loaded with custom_scripts/import_candles.py
ALTER TABLE index_candles RENAME TO candles_1m;
ALTER TABLE candles_1m ADD COLUMN IF NOT EXISTS volume BIGINT;
ALTER TABLE candles_1m ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'ticks';

-- What each stored instrument token was. Kite reuses nothing, but an
-- expired contract disappears from its instrument list, so this is how
-- a chart still knows "NIFTY 22400 PE, 26 Nov" (and that asking Kite
-- for its history is pointless once it has expired).
CREATE TABLE IF NOT EXISTS contracts (
    instrument_token BIGINT PRIMARY KEY,
    tradingsymbol    TEXT NOT NULL,
    exchange         TEXT,
    underlying       TEXT,
    expiry           DATE,
    strike           NUMERIC(18,4),
    instrument_type  TEXT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_contracts_symbol ON contracts (tradingsymbol);
