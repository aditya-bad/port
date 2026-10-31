-- The kinds of alert a deployment doesn't raise (app/alert_types.py):
-- drawdown, losing streak, entries and exits... Empty: every kind on.
ALTER TABLE deployments ADD COLUMN IF NOT EXISTS alerts_off TEXT[] NOT NULL DEFAULT '{}';
