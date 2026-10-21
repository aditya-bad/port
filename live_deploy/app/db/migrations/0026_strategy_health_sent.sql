-- Strategy health findings already pushed (app/strategy_health.py), so
-- each is pushed once however often the check runs or the app restarts.
CREATE TABLE IF NOT EXISTS strategy_health_sent (
    key           TEXT PRIMARY KEY,
    deployment_id UUID REFERENCES deployments(id) ON DELETE CASCADE,
    sent_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
