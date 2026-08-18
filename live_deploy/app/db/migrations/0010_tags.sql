-- A small, admin-managed catalog of PREDEFINED tag names (Settings ->
-- Tags manages this list) plus the actual tags applied to each
-- deployment. Deliberately a curated catalog, not freeform per-
-- deployment text: a tag typo ("Experimntal" vs "Experimental")
-- silently creating a second, orphaned label is exactly the kind of
-- bug a fixed picker avoids.
--
-- The one tag this catalog does NOT hold is "Excluded from reports" --
-- that's synthesized in the frontend straight from
-- deployments.include_in_reports (see the 0009 migration), never
-- stored here or in deployments.tags. Keeping it purely derived avoids
-- a second, independently-editable place that could drift out of sync
-- with the boolean every report-filtering query actually reads.
CREATE TABLE IF NOT EXISTS tag_catalog (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The actual tag names applied to one deployment, e.g. {'Experimental',
-- 'High conviction'} -- validated against tag_catalog.name by the
-- router on every write (PATCH /deployments/{id}), never enforced at
-- the DB layer with a foreign key, since an array column can't express
-- "every element must exist in another table" directly. Editable
-- regardless of status, same as include_in_reports/notes -- pure
-- bookkeeping, not strategy state.
ALTER TABLE deployments ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}';
