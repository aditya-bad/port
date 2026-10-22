-- A day the end-of-day check flagged stays marked everywhere until it's
-- repaired or someone dismisses it from its case on the Repair page
-- (they've looked and the numbers stand). A re-run that finds something
-- different brings it back (app/repair/daycheck.py).
ALTER TABLE day_checks ADD COLUMN IF NOT EXISTS dismissed_at TIMESTAMPTZ;
