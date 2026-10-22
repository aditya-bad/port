-- What a running deployment reports about itself (app/deployments/
-- health.py): the runner's own protections (a strategy that keeps
-- crashing, bad prices from the feed, a fill that failed
-- half-way through a trade, legs the strategy and the book disagree on, a
-- forced exit with no price) and what a strategy reports through
-- runner.report() (an entry it had to delay or skip, a rule that has been
-- blocked for a while). One open issue per (deployment, key); the
-- strategy or the runner resolves it when it clears, a transient one
-- expires on its own, and you can dismiss it. The health monitor
-- (app/ops_health.py) alerts on the open ones; the Repair page lists
-- them with the day each is about.

CREATE TABLE IF NOT EXISTS strategy_issues (
    id             BIGSERIAL PRIMARY KEY,
    deployment_id  UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    key            TEXT NOT NULL,                 -- stable while it lasts: "crash", "entry_delayed:2026-10-22"
    kind           TEXT NOT NULL,
    severity       TEXT NOT NULL DEFAULT 'warning' CHECK (severity IN ('critical', 'warning', 'info')),
    source         TEXT NOT NULL DEFAULT 'runner' CHECK (source IN ('runner', 'strategy')),
    title          TEXT NOT NULL,
    detail         TEXT,
    data           JSONB NOT NULL DEFAULT '{}'::jsonb,
    day            DATE,                          -- the trading day it's about, for the Repair page
    first_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    count          INT NOT NULL DEFAULT 1,
    expires_at     TIMESTAMPTZ,                   -- resolves on its own if not reported again by then
    resolved_at    TIMESTAMPTZ,
    resolved_by    TEXT                           -- 'cleared', 'expired' or 'dismissed'
);

CREATE UNIQUE INDEX IF NOT EXISTS one_open_strategy_issue ON strategy_issues (deployment_id, key) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS strategy_issues_by_time ON strategy_issues (first_at DESC);

-- An exit is never refused for want of cash: buying back a short that
-- went badly against you can cost more than the cash left, and refusing
-- it left the short open with nothing able to close it. Opening buys
-- are still checked (queries.record_fill); a close that takes cash below
-- zero is reported instead (runner: "cash_negative").
ALTER TABLE deployments DROP CONSTRAINT IF EXISTS deployments_current_cash_check;
