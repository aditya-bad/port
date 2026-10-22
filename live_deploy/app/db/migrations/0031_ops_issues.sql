-- Operational health (app/ops_health.py): every problem the monitor finds
-- with Kite, the database, the daily option-history export or a
-- deployment, kept open until it clears. While open (and not snoozed)
-- it alerts every minute; when it clears, it's closed with the time.

CREATE TABLE IF NOT EXISTS ops_issues (
    id             BIGSERIAL PRIMARY KEY,
    key            TEXT NOT NULL,                 -- what it is, stable while it lasts: "kite:login", "dep:<id>:errors"
    kind           TEXT NOT NULL,
    severity       TEXT NOT NULL DEFAULT 'critical' CHECK (severity IN ('critical', 'warning')),
    deployment_id  UUID REFERENCES deployments(id) ON DELETE CASCADE,
    title          TEXT NOT NULL,
    detail         TEXT,
    data           JSONB NOT NULL DEFAULT '{}'::jsonb,
    opened_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at    TIMESTAMPTZ,
    resolved_by    TEXT,                          -- 'cleared' (the check passed) or 'dismissed' (by hand)
    snoozed_until  TIMESTAMPTZ,
    last_alert_at  TIMESTAMPTZ,
    alerts         INT NOT NULL DEFAULT 0
);

-- one open issue per key: a problem that comes back after clearing is a new issue
CREATE UNIQUE INDEX IF NOT EXISTS one_open_ops_issue_per_key ON ops_issues (key) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS ops_issues_by_time ON ops_issues (opened_at DESC);
