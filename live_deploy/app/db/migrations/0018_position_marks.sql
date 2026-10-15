-- Per-position price marks for the Cycle Replay chart (routers/cycles.py):
-- each open leg's last traded price, recorded every minute for intraday
-- deployments and every 5 minutes for positional ones, during market
-- hours (DeploymentManager.marks_loop). Kite keeps no history for expired
-- options, so this is what lets a cycle's per-leg P&L curve be drawn
-- after its contracts expire.
CREATE TABLE IF NOT EXISTS position_marks (
    position_id   UUID NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
    deployment_id UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    marked_at     TIMESTAMPTZ NOT NULL,
    price         NUMERIC(18,4) NOT NULL,
    PRIMARY KEY (position_id, marked_at)
);
