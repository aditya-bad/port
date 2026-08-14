-- Per-strategy admin toggle: whether a registered strategy shows up in
-- the Strategy Catalog / can be deployed. Strategies themselves are
-- Python code (app.strategies.registry), not DB rows — this table is
-- ONLY the enabled/disabled flag layered on top, so it needs to be
-- keyed by strategy_name (the same string the registry uses), not a
-- foreign key into anything. Missing a row entirely means "enabled" by
-- default (see queries.ensure_strategy_settings, called once at startup
-- for every currently-registered strategy so this stays true in
-- practice, not just in theory).
CREATE TABLE IF NOT EXISTS strategy_settings (
    strategy_name  TEXT PRIMARY KEY,
    enabled        BOOLEAN NOT NULL DEFAULT true,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
