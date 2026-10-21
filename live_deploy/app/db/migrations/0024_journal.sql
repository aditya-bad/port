-- The trade journal: what happened around the trades, in the trader's own
-- words. A note belongs to a day, and optionally to one deployment and
-- one of its positions (a trade); tags group notes across days ("event
-- day", "manual intervention", "Kite down") so their days' P&L can be
-- compared. Never read by a strategy.
CREATE TABLE IF NOT EXISTS journal_entries (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    day           DATE NOT NULL,
    deployment_id UUID REFERENCES deployments(id) ON DELETE CASCADE,
    position_id   UUID REFERENCES positions(id) ON DELETE SET NULL,
    body          TEXT NOT NULL,
    tags          TEXT[] NOT NULL DEFAULT '{}',
    author        TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_journal_day ON journal_entries(day DESC);
CREATE INDEX IF NOT EXISTS idx_journal_dep_day ON journal_entries(deployment_id, day DESC);
CREATE INDEX IF NOT EXISTS idx_journal_tags ON journal_entries USING GIN (tags);
