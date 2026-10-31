-- The earliest day a share's prices were asked for (app/lens_store.py).
-- Kite has nothing before a listing or on a holiday, so the earliest
-- candle kept can be later than what was asked; without this the same
-- span would be asked for again on every read.
ALTER TABLE lens_instruments ADD COLUMN IF NOT EXISTS read_from DATE;
