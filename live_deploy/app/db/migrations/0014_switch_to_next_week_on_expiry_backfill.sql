-- Backfills the new `switch_to_next_week_on_expiry` config key
-- (pivot_supertrend_options.py / pivot_supertrend_options_inverse.py)
-- into every EXISTING deployment of those two strategies, so its
-- absence from an already-deployed config doesn't silently fall back
-- to the code-level default (false) for deployments that predate the
-- option's existence.
--
-- Per explicit instruction: existing pivot_supertrend_options
-- deployments get true (opt into re-resolving NEXT_WEEK instead of
-- selling a same-day-expiry leg); existing
-- pivot_supertrend_options_inverse deployments get false (unchanged
-- behavior — still buys the same-day-expiry leg as before). New
-- deployments of either strategy default to false in code
-- (default_config) unless the deployer sets it explicitly at deploy
-- time.
--
-- Idempotent by construction: `config || '{...}'::jsonb` always sets
-- the key to the SAME literal value regardless of whatever it was
-- before (absent, true, or false), so re-running this (or running it
-- against a database where these strategies don't exist, or where a
-- deployment already has the key set some other way) is a no-op past
-- the first application — no CASE/existence check needed.
UPDATE deployments
SET config = config || '{"switch_to_next_week_on_expiry": true}'::jsonb
WHERE strategy_name = 'pivot_supertrend_options';

UPDATE deployments
SET config = config || '{"switch_to_next_week_on_expiry": false}'::jsonb
WHERE strategy_name = 'pivot_supertrend_options_inverse';
