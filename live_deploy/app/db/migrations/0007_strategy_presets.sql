-- Named, reusable strategy config snapshots — deploying the same
-- strategy repeatedly (e.g. intraday_dtt_adjusted's 14 fields) with
-- the same values every time meant retyping all of them each time
-- before this. Scoped to (strategy_name, preset_name) rather than a
-- globally unique preset_name -- the same preset name can mean
-- something different for two different strategies (e.g. "conservative"
-- for pivot_supertrend vs. "conservative" for strangle_monthly_v2),
-- and there's no reason to force distinct names across strategies that
-- have nothing to do with each other.
--
-- `config` mirrors deployments.config's own shape exactly (the
-- strategy-specific fields only -- deployment_name/mode/initial_capital
-- are per-deployment metadata, never part of this) so a saved preset
-- can be dropped straight into a new deployment's config with no
-- translation step.
CREATE TABLE IF NOT EXISTS strategy_presets (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_name TEXT NOT NULL,
    preset_name   TEXT NOT NULL,
    config        JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (strategy_name, preset_name)
);

CREATE INDEX IF NOT EXISTS idx_strategy_presets_strategy ON strategy_presets(strategy_name);
