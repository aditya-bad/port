-- A day check run can be of one deployment (the Repair page's "Check
-- again", after a fix): which one, so it doesn't stand in for the day's
-- run (the after-export start, its announcement, a failed day) and the
-- page can show it on that deployment's row. NULL: every deployment.
ALTER TABLE day_check_runs ADD COLUMN IF NOT EXISTS deployment_id UUID REFERENCES deployments(id) ON DELETE CASCADE;
