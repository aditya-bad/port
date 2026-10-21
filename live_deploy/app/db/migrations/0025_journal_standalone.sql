-- A journal note attached to nothing: an idea, a lesson, a plan. Not
-- about any deployment or any day's trading, so it carries no P&L and
-- stays out of the tags' P&L. (A note with no deployment and standalone
-- false is still a note about the whole day, every deployment.)
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS standalone BOOLEAN NOT NULL DEFAULT false;
