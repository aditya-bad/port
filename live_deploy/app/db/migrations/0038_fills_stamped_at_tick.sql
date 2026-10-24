-- Moments the app's behaviour changed that later code has to know about.
-- 'fills_stamped_at_tick': from here on every fill is stamped with the
-- tick it happened on (StrategyBase.fill_time). Before it, four strategies
-- stamped a fill made at a candle's close with the candle's start, up to
-- one candle early; the end-of-day check reads their older fills across
-- that candle (app/repair/daycheck.py). Recorded when this migration runs,
-- which is the first start of the code with the fix.
CREATE TABLE IF NOT EXISTS app_marks (
    name  TEXT PRIMARY KEY,
    at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO app_marks (name) VALUES ('fills_stamped_at_tick') ON CONFLICT DO NOTHING;
