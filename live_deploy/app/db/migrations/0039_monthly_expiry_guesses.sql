-- A monthly option's symbol (NIFTY26OCT22550CE) carries no day, and the
-- contracts it was recorded from the symbol alone were stored with the
-- month's last day as their expiry: a guess. NIFTY's October 2026 monthly
-- expired on Tuesday the 27th, not the 31st, and the end-of-day check took
-- the 31st as a real expiry Kite no longer listed. Those guesses are
-- cleared; the daily candle backfill records Kite's own expiry for the
-- contracts held (CandleService.remember_contracts), and readers fall back
-- to the symbol's month when there is none. (A month whose real monthly
-- expiry is its last day is cleared too: the fallback gives the same date.)
UPDATE contracts SET expiry = NULL, updated_at = now()
WHERE instrument_type IN ('CE', 'PE')
  AND tradingsymbol ~ '^[A-Z&]+[0-9]{2}(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[0-9.]+(CE|PE)$'
  AND expiry = (date_trunc('month', expiry) + interval '1 month - 1 day')::date;
