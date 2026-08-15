-- Free-text metadata field for a deployment -- editable any time via
-- PATCH /deployments/{id}, unlike deployment_name/strategy_name/mode/
-- initial_capital/config which are either fixed identity or structural/
-- financial fields a running strategy already assumes are stable.
ALTER TABLE deployments ADD COLUMN IF NOT EXISTS notes TEXT;
