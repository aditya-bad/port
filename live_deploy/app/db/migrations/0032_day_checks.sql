-- The end-of-day check (app/repair/daycheck.py), run after the 15:45 export.

-- Each deployment's strategy state as it stood at the open and after the
-- close (copied from deployment_state, which only ever holds the latest):
-- a day is replayed from its start, so it needs the state of that start.
CREATE TABLE IF NOT EXISTS deployment_state_days (
    deployment_id  UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    day            DATE NOT NULL,
    at             TEXT NOT NULL CHECK (at IN ('open', 'close')),
    state          JSONB,
    captured_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (deployment_id, day, at)
);

-- One run of the check over a day (by the worker, custom_scripts/day_check.py).
CREATE TABLE IF NOT EXISTS day_check_runs (
    id           BIGSERIAL PRIMARY KEY,
    day          DATE NOT NULL,
    trigger      TEXT NOT NULL DEFAULT 'auto',      -- 'auto' (after the export) or 'manual'
    status       TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'failed')),
    progress     JSONB NOT NULL DEFAULT '{}'::jsonb, -- {total, done, current}
    error        TEXT,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS day_check_runs_by_day ON day_check_runs (day, started_at DESC);

-- What the check found for one deployment on one day (the latest run's).
--   ok          the replay did what the app did, and the end state is as expected
--   rule        the end state breaks a rule (an intraday deployment open after its cut-off, an expired leg held)
--   mismatch    the replay traded differently from the app, unambiguously
--   ambiguous   it traded differently, but a minute could be read two ways: you choose (the Repair page)
--   cant_check  no start-of-day state, or no prices for a leg: the rules were checked, the replay wasn't
--   error       the replay failed
CREATE TABLE IF NOT EXISTS day_checks (
    id             BIGSERIAL PRIMARY KEY,
    run_id         BIGINT REFERENCES day_check_runs(id) ON DELETE SET NULL,
    day            DATE NOT NULL,
    deployment_id  UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    status         TEXT NOT NULL CHECK (status IN ('ok', 'rule', 'mismatch', 'ambiguous', 'cant_check', 'error')),
    headline       TEXT NOT NULL,
    rules          JSONB NOT NULL DEFAULT '[]'::jsonb,  -- the end-of-day rules broken
    diff           JSONB NOT NULL DEFAULT '{}'::jsonb,  -- trades the replay made that the app didn't, and the other way; end states
    replay         JSONB NOT NULL DEFAULT '{}'::jsonb,  -- the replay: its fills, the minutes it read the usual way, notes
    checked_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (day, deployment_id)
);
