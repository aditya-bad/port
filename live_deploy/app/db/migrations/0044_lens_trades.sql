-- LinckLens trades (app/lens_trades.py): every trade from Zerodha Console
-- tradebook files, equity and mutual funds, as given.

-- Each file imported: when, what it held, what was new.
CREATE TABLE IF NOT EXISTS lens_imports (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT,
    rows INTEGER NOT NULL,
    added INTEGER NOT NULL,
    skipped INTEGER NOT NULL,
    first_day DATE,
    last_day DATE,
    problems JSONB NOT NULL DEFAULT '[]',
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A trade is one (exchange, segment, trade_id, isin, trade_type):
-- importing a file twice, or files that overlap, adds nothing. import_id:
-- the file that first brought it (removing that import removes it).
CREATE TABLE IF NOT EXISTS lens_trades (
    id BIGSERIAL PRIMARY KEY,
    import_id BIGINT REFERENCES lens_imports(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    isin TEXT NOT NULL DEFAULT '',
    trade_date DATE NOT NULL,
    exchange TEXT NOT NULL DEFAULT '',
    segment TEXT NOT NULL DEFAULT '',
    series TEXT,
    trade_type TEXT NOT NULL CHECK (trade_type IN ('buy', 'sell')),
    auction BOOLEAN NOT NULL DEFAULT false,
    quantity DOUBLE PRECISION NOT NULL CHECK (quantity > 0),
    price DOUBLE PRECISION NOT NULL CHECK (price >= 0),
    trade_id TEXT NOT NULL,
    order_id TEXT,
    executed_at TIMESTAMP,
    UNIQUE (exchange, segment, trade_id, isin, trade_type)
);
CREATE INDEX IF NOT EXISTS lens_trades_day ON lens_trades (trade_date);
