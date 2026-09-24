-- Backfills the new `long_days_target_percentage` config key
-- (strangle_monthly_v2.py) into every EXISTING deployment of that
-- strategy, so its absence from an already-deployed config doesn't
-- silently fall back to the code-level default only in the DB's own
-- blind spot -- it should show up in the Configuration tab / config
-- editor exactly like it does for a brand-new deployment.
--
-- Backfilled to 0.05, the SAME value new deployments get from
-- default_config -- this is a deliberate behavior change for existing
-- deployments too (their next fresh entry, if resolved more than 29
-- days from its own expiry outside the current calendar month, now
-- targets a closer, more liquid strike instead of the far-OTM/
-- practically-zero-liquidity one strike_selection_capital_pct alone
-- was producing). It only affects the STRIKE SELECTED AT THE NEXT
-- FRESH ENTRY -- never touches any already-open position's existing
-- legs, so no flatten/close of current trades is needed or triggered
-- by this migration.
--
-- Idempotent by construction: `config || '{...}'::jsonb` always sets
-- the key to the SAME literal value regardless of whatever it was
-- before, so re-running this (or running it against a database with
-- no strangle_monthly_v2 deployments) is a no-op past the first
-- application -- no CASE/existence check needed (see 0014's identical
-- reasoning).
UPDATE deployments
SET config = config || '{"long_days_target_percentage": 0.05}'::jsonb
WHERE strategy_name = 'strangle_monthly_v2';
