-- Generic scratch storage (Step 104) — a deliberately schema-less
-- escape hatch for whatever ad-hoc, low-stakes bit of state a future
-- feature needs to persist before it's clear it deserves a real column
-- or table of its own. Every feature so far that touched the DB got a
-- proper typed migration (tags, notes, include_in_reports, push
-- subscriptions, ...) — this is NOT a replacement for that discipline,
-- it's for the SMALLER asks in between: a per-deployment flag, a saved
-- preference, a cached one-off computed value, a note-to-self — things
-- where writing a migration for a single JSONB blob would be more
-- ceremony than the feature is worth, and where the eventual shape
-- isn't known yet. The moment a key here turns out to matter enough to
-- want real constraints/indexing/query performance, graduate it into
-- its own typed column via a normal migration and stop writing to it
-- here — this table is meant to stay a junk-drawer of exceptions, not
-- grow into a second, ungoverned schema living inside one JSONB column.
--
-- Two tables, not one, because "attached to a specific deployment" and
-- "global to the whole app" are different enough lifetimes to matter:
-- deployment_scratch cascades away with its deployment (see
-- ON DELETE CASCADE) so it can never outlive the thing it describes;
-- app_scratch has no owner and persists independently (e.g. a
-- portfolio-wide preference, not tied to any one deployment).
--
-- `value` is JSONB, not TEXT, so a future reader can store/query
-- structured data (a list, a small object) without a second migration
-- just to widen the column's meaning. `key` is a free-form string
-- namespace the CALLER defines and owns (e.g. "compare_pinned_columns",
-- "stats_default_granularity") — nothing here enforces what keys exist
-- or what shape their values take; that discipline lives in whichever
-- feature reads/writes a given key, not in the schema.

CREATE TABLE IF NOT EXISTS deployment_scratch (
    deployment_id  UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    key            TEXT NOT NULL,
    value          JSONB NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (deployment_id, key)
);

CREATE TABLE IF NOT EXISTS app_scratch (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
